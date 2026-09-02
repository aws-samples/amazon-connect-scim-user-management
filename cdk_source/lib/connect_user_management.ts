import {
  CfnOutput,
  CfnParameter,
  CustomResource,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import {
  AccessLogFormat,
  EndpointType,
  Integration,
  IntegrationType,
  LogGroupLogDestination,
  MethodLoggingLevel,
  Period,
  RestApi,
  TokenAuthorizer,
} from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import { ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { Key } from 'aws-cdk-lib/aws-kms';
import { Code, Function, Runtime } from 'aws-cdk-lib/aws-lambda';
import { LogGroup, RetentionDays } from 'aws-cdk-lib/aws-logs';
import * as customresources from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { join } from 'path';

/** Identity providers this stack can be deployed for. */
const SUPPORTED_IDP_TYPES = ['okta', 'azure'] as const;
type IdpType = (typeof SUPPORTED_IDP_TYPES)[number];

/** Systems Manager parameter that holds the SCIM API bearer token. */
const API_TOKEN_PARAMETER_NAME = '/connect/scim-integration/api-token';

export class ConnnectUserManagement extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // The handler module is chosen at synth time from context, so an unsupported
    // value has to fail here rather than produce a Lambda that cannot import.
    const idp_type = this.node.tryGetContext('idp_type');
    if (!SUPPORTED_IDP_TYPES.includes(idp_type)) {
      throw new Error(
        `Unsupported idp_type '${idp_type}'. Synthesize with -c idp_type=<${SUPPORTED_IDP_TYPES.join('|')}>.`,
      );
    }
    const idpType: IdpType = idp_type;

    // Amazon Connect User Management parameters
    const connect_instance_id = new CfnParameter(this, 'connect_instance_id', {
      description: 'Enter your Amazon Connect Instance Id.',
      type: 'String',
      allowedPattern: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      constraintDescription: 'Must be an Amazon Connect instance UUID.',
    });

    const api_key_length = new CfnParameter(this, 'api_key_length', {
      description: 'Length of the API bearer token generated for the Lambda authorizer.',
      type: 'Number',
      default: 32,
      minValue: 32,
      maxValue: 256,
    });

    const default_routing_profile = new CfnParameter(this, 'default_routing_profile', {
      description:
        'Amazon Connect routing profile assigned to users the IdP does not supply a routing profile for.',
      type: 'String',
      default: 'Basic Routing Profile',
    });

    const default_security_profile = new CfnParameter(this, 'default_security_profile', {
      description:
        'Amazon Connect security profile assigned to users the IdP does not supply entitlements for.',
      type: 'String',
      default: 'Agent',
    });

    // Customer-managed key for the API token. A dedicated key is used because
    // IAM policies cannot be scoped to the default aws/ssm key, so the authorizer
    // could not otherwise be granted decrypt access to this token alone.
    const api_token_key = new Key(this, 'api_token_key', {
      description: 'Encrypts the Amazon Connect SCIM API bearer token in Parameter Store.',
      alias: 'connect-scim-api-token',
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // The parameter is created by the custom resource rather than by this
    // template, so it never briefly holds a guessable placeholder value.
    const api_token_parameter_arn = this.formatArn({
      service: 'ssm',
      resource: 'parameter',
      resourceName: API_TOKEN_PARAMETER_NAME.replace(/^\//, ''),
    });

    // IDP SCIM provisioner on Amazon Connect instance Lambda function
    const SCIM_provisioning_lambda_role = new iam.Role(this, 'SCIM_provisioning_lambda_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: 'connect-scim-user-management',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const SCIM_provisioning_lambda_function = new Function(
      this,
      'SCIM_provisioning_lambda_function',
      {
        runtime: Runtime.PYTHON_3_14,
        code: Code.fromAsset(join(__dirname, '../lambdas/user_management')),
        handler: `${idpType}.lambda_handler`,
        description: 'AWS Lambda function to provision Amazon Connect users via IdP SCIM integration.',
        timeout: Duration.seconds(900),
        memorySize: 512,
        functionName: 'connect-scim-user-management',
        role: SCIM_provisioning_lambda_role,
        environment: {
          INSTANCE_ID: connect_instance_id.valueAsString,
          DEFAULT_ROUTING_PROFILE: default_routing_profile.valueAsString,
          DEFAULT_SECURITY_PROFILE: default_security_profile.valueAsString,
        },
      },
    );

    const instanceArn = this.formatArn({
      service: 'connect',
      resource: 'instance',
      resourceName: connect_instance_id.valueAsString,
    });

    const SCIM_provisioning_lambda_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: 'ConnectManagement',
          effect: iam.Effect.ALLOW,
          // Scoped to the operations the handler actually calls. Amazon Connect
          // requires the instance for the list/search/create operations and the
          // child resources for the per-user and per-profile ones.
          actions: [
            'connect:CreateUser',
            'connect:DeleteUser',
            'connect:DescribeUser',
            'connect:ListRoutingProfiles',
            'connect:ListSecurityProfiles',
            'connect:ListUsers',
            'connect:SearchUsers',
            'connect:UpdateUserSecurityProfiles',
          ],
          resources: [
            instanceArn,
            `${instanceArn}/security-profile/*`,
            `${instanceArn}/routing-profile/*`,
            `${instanceArn}/agent/*`,
          ],
        }),
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaSCIMManagedPolicy', {
      description: 'Policy to allow Lambda function to manage Amazon Connect instance users.',
      document: SCIM_provisioning_lambda_policy,
      managedPolicyName: 'connect-user-management-policy',
      roles: [SCIM_provisioning_lambda_role],
    });

    // Lambda authorizer to authorize SCIM requests to SCIM provisioning Lambda function
    const lambda_authorizer_role = new iam.Role(this, 'lambda_authorizer_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: 'lambda-authorizer-role',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const lambda_authorizer_function = new Function(this, 'lambda_authorizer_function', {
      runtime: Runtime.PYTHON_3_14,
      code: Code.fromAsset(join(__dirname, '../lambdas/lambda_authorizer')),
      handler: 'lambda_authorizer.lambda_handler',
      description: 'Validates the SCIM API bearer token presented by the IdP application.',
      // An API Gateway authorizer must answer well inside the 29 second
      // integration limit; it only reads one parameter.
      timeout: Duration.seconds(10),
      functionName: 'lambda-authorizer-scim-api-gw',
      memorySize: 256,
      role: lambda_authorizer_role,
      environment: {
        PARAMETER_NAME: API_TOKEN_PARAMETER_NAME,
      },
    });

    // The authorizer needs to read exactly one parameter and decrypt it with
    // exactly one key. The previous policy granted ssm:*, ec2messages:*,
    // ds:CreateComputer and cloudwatch:PutMetricData on all resources, none of
    // which this function uses.
    const lambda_authorizer_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: 'ReadApiToken',
          effect: iam.Effect.ALLOW,
          actions: ['ssm:GetParameter'],
          resources: [api_token_parameter_arn],
        }),
        new iam.PolicyStatement({
          sid: 'DecryptApiToken',
          effect: iam.Effect.ALLOW,
          actions: ['kms:Decrypt'],
          resources: [api_token_key.keyArn],
        }),
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaAuthorizerManagedPolicy', {
      description: 'Allows the authorizer to read the SCIM API token from Parameter Store.',
      document: lambda_authorizer_policy,
      managedPolicyName: 'connect-scim-user-management-policy',
      roles: [lambda_authorizer_role],
    });

    // API Key generation Lambda function
    const api_key_generation_role = new iam.Role(this, 'api_key_generation_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: 'api_key_generation_role',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const api_key_generation_custom_resource = new Function(
      this,
      'api_key_generation_custom_resource',
      {
        runtime: Runtime.PYTHON_3_14,
        code: Code.fromAsset(join(__dirname, '../lambdas/custom_resource')),
        handler: 'custom_resource.lambda_handler',
        description: 'Generates the API token used for the Connect SCIM user management integration.',
        timeout: Duration.seconds(30),
        functionName: 'api-key-custom-resource-lambda-authorizer',
        memorySize: 256,
        role: api_key_generation_role,
        environment: {
          PARAMETER_NAME: API_TOKEN_PARAMETER_NAME,
          KMS_KEY_ID: api_token_key.keyArn,
        },
      },
    );

    const api_key_generation_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: 'ManageApiToken',
          effect: iam.Effect.ALLOW,
          actions: ['ssm:PutParameter', 'ssm:GetParameter', 'ssm:DeleteParameter'],
          resources: [api_token_parameter_arn],
        }),
        new iam.PolicyStatement({
          sid: 'EncryptApiToken',
          effect: iam.Effect.ALLOW,
          // A standard SecureString is encrypted directly, which needs Encrypt;
          // reading it back to check for an existing token needs Decrypt.
          actions: ['kms:Encrypt', 'kms:Decrypt'],
          resources: [api_token_key.keyArn],
        }),
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaGenerateAPIKeyManagedPolicy', {
      description: 'Generates an API token and stores it in an AWS Systems Manager parameter.',
      document: api_key_generation_policy,
      managedPolicyName: 'lambda-api-key-custom-resource-policy',
      roles: [api_key_generation_role],
    });

    const provider = new customresources.Provider(this, 'ResourceProvider', {
      onEventHandler: api_key_generation_custom_resource,
      logGroup: new LogGroup(this, 'api_key_provider_log_group', {
        retention: RetentionDays.ONE_WEEK,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });

    // ServiceToken is supplied by the provider, so it must not also be passed in
    // 'properties' where it would be silently overwritten.
    new CustomResource(this, 'api_key_custom_action', {
      serviceToken: provider.serviceToken,
      resourceType: 'Custom::ScimApiToken',
      properties: {
        ApiLength: api_key_length.valueAsNumber,
      },
    });

    // API Gateway for SCIM requests to Lambda function
    const scim_api_authorizer = new TokenAuthorizer(this, 'scim_api_authorizer', {
      handler: lambda_authorizer_function,
      // Caching keeps a provisioning burst from invoking the authorizer for every
      // request. The authorizer's own cache is shorter, so a rotated token is
      // picked up without waiting for both to expire.
      resultsCacheTtl: Duration.minutes(5),
    });

    const scim_api_access_logs = new LogGroup(this, 'scim_api_access_logs', {
      // Access logs are the audit trail for authentication attempts against the
      // SCIM endpoint, so they are retained for a year, matching the
      // CloudFormation and Terraform deployments.
      retention: RetentionDays.ONE_YEAR,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const scim_api_gw = new RestApi(this, 'scim_api_gw', {
      restApiName: 'connect-scim-api-gateway',
      cloudWatchRole: true,
      description:
        'API GW invoked from IdP SCIM application to invoke the Amazon Connect user management lambda function.',
      endpointConfiguration: {
        types: [EndpointType.EDGE],
      },
      deployOptions: {
        stageName: 'dev',
        loggingLevel: MethodLoggingLevel.ERROR,
        // Data tracing writes full request and response bodies to CloudWatch,
        // which would include the bearer token and user attributes.
        dataTraceEnabled: false,
        metricsEnabled: true,
        accessLogDestination: new LogGroupLogDestination(scim_api_access_logs),
        accessLogFormat: AccessLogFormat.jsonWithStandardFields({
          caller: false,
          httpMethod: true,
          ip: true,
          protocol: true,
          requestTime: true,
          resourcePath: true,
          responseLength: true,
          status: true,
          user: false,
        }),
      },
    });

    const scim_api_resource = scim_api_gw.root.addResource('{Users+}');

    scim_api_resource.addMethod(
      'ANY',
      new Integration({
        integrationHttpMethod: 'POST',
        type: IntegrationType.AWS_PROXY,
        uri: this.formatArn({
          service: 'apigateway',
          account: 'lambda',
          resource: 'path/2015-03-31/functions',
          resourceName: `${SCIM_provisioning_lambda_function.functionArn}/invocations`,
        }),
      }),
      {
        authorizer: scim_api_authorizer,
        requestParameters: {
          'method.request.path.proxy': true,
        },
      },
    );

    scim_api_gw.addUsagePlan('scim_api_usage', {
      apiStages: [
        {
          api: scim_api_gw,
          stage: scim_api_gw.deploymentStage,
        },
      ],
      quota: {
        limit: 5000,
        period: Period.DAY,
      },
      throttle: {
        burstLimit: 1000,
        rateLimit: 500,
      },
      name: 'scim-api-usage-plan',
    });

    SCIM_provisioning_lambda_function.addPermission('lambda_api_gw_scim_permission', {
      principal: new ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: scim_api_gw.arnForExecuteApi('*', '/*', scim_api_gw.deploymentStage.stageName),
    });

    // The base URL differs per provider: the Okta SCIM 2.0 Test App validates its
    // connection with a userName filter, while the Entra ID application appends
    // its own /scim/v2 path segments.
    const base_url = `${scim_api_gw.url.replace(/\/$/, '')}`;
    new CfnOutput(this, 'IdP-API-Base-URL', {
      description:
        idpType === 'okta'
          ? 'Base URL for the Okta SCIM 2.0 Test App (Header Auth) provisioning connection.'
          : 'Base URL for the Microsoft Entra ID provisioning connection (Tenant URL).',
      value:
        idpType === 'okta'
          ? `${base_url}/Users?filter=userName%20eq%20%22test.user%22`
          : `${base_url}/scim/v2`,
    });

    new CfnOutput(this, 'IdP-API-Token-SSM-Parameter', {
      description:
        'The AWS Systems Manager parameter holding the API token to configure in the SCIM application. Read it with: aws ssm get-parameter --with-decryption --name ' +
        API_TOKEN_PARAMETER_NAME,
      value: api_token_parameter_arn,
    });

    new CfnOutput(this, 'IdP-API-Token-KMS-Key', {
      description: 'Customer-managed KMS key encrypting the SCIM API token.',
      value: api_token_key.keyArn,
    });
  }
}
