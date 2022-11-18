import { CustomResource,Stack, StackProps, Duration, CfnParameter, CfnOutput } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { Function, Runtime, Code } from 'aws-cdk-lib/aws-lambda';
import { join } from 'path';
import * as customresources from 'aws-cdk-lib/custom-resources';
import { RestApi, EndpointType, Integration, IntegrationType, TokenAuthorizer, Deployment, Period, Stage, MethodLoggingLevel } from 'aws-cdk-lib/aws-apigateway';
import { ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { RetentionDays } from 'aws-cdk-lib/aws-logs';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';

export class ConnnectUserManagement extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // Amazon Connect User Management parameters
    const connect_instance_id = new CfnParameter(this, 'connect_instance_id', {
      description: 'Enter your Amazon Connect Instance Id.',
      type: 'String'
    });

    const api_key_length = new CfnParameter(this, 'api_key_length', {
      description: 'Enter the value of the API key to be generated for lambda authorizer.',
      type: 'Number',
      default: 32
    });

    const api_key_name = new StringParameter(this, 'security_demo_parameter', {
      parameterName: '/connect/scim-integration/api-token',
      stringValue: 'default',
      description: 'The SSM parameter name for the API gateway API key.',
    });

    // IDP SCIM provisioner on Amazon Connect instance Lambda function
    const SCIM_provisioning_lambda_role = new iam.Role(this, 'SCIM_provisioning_lambda_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: "connect-scim-user-management",
      managedPolicies: [
        iam.ManagedPolicy.fromManagedPolicyArn(this, 'lambdaConnectSCIMExecutionPolicy', 'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole')
      ]
    });

    const idp_type = this.node.tryGetContext('idp_type')

    const SCIM_provisioning_lambda_function = new Function(this, 'SCIM_provisioning_lambda_function', {
      runtime: Runtime.PYTHON_3_9,
      code: Code.fromAsset(join(__dirname, "../lambdas/user_management")),
      handler: idp_type + '.lambda_handler',
      description: 'AWS Lambda function to provision Amazon Connect users via IdP SCIM integration.',
      timeout: Duration.seconds(900),
      memorySize: 512,
      functionName: 'connect-scim-user-management',
      role: SCIM_provisioning_lambda_role,
      environment:{
        INSTANCE_ID: connect_instance_id.valueAsString,
        DEFAULT_ROUTING_PROFILE: 'Basic Routing Profile'
      },
    });

    const SCIM_provisioning_lambda_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: "ConnectManagement",
          effect: iam.Effect.ALLOW,
          actions: [
            "connect:UpdateUserIdentityInfo",
            "connect:DeleteUser",
            "connect:ListRoutingProfiles",
            "connect:ListUsers",
            "connect:CreateUser",
            "connect:SearchUsers",
            "connect:ListSecurityProfiles",
            "connect:DescribeUser",
            "connect:DescribeSecurityProfile",
            "connect:UpdateUserSecurityProfiles"          
          ],
          resources: [
            'arn:aws:connect:' + this.region + ':' + this.account + ':instance/' + connect_instance_id.valueAsString,
            'arn:aws:connect:' + this.region + ':' + this.account + ':instance/' + connect_instance_id.valueAsString + '/security-profile/*',
            'arn:aws:connect:' + this.region + ':' + this.account + ':instance/' + connect_instance_id.valueAsString + '/routing-profile/*',
            'arn:aws:connect:' + this.region + ':' + this.account + ':instance/' + connect_instance_id.valueAsString + '/agent/*'
          ]   
        }),
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaSCIMManagedPolicy', {
      description: 'Policy to allow Lambda function to manage Amazon Connect instance users.',
      document:SCIM_provisioning_lambda_policy,
      managedPolicyName: 'connect-user-management-policy',
      roles: [SCIM_provisioning_lambda_role]
    });


    // Lambda authorizer to authorize SCIM requests to SCIM provisioning Lambda function
    const lambda_authorizer_role = new iam.Role(this, 'lambda_authorizer_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: "lambda-authorizer-role",
      managedPolicies: [
        iam.ManagedPolicy.fromManagedPolicyArn(this, 'lambdaAuthorizerExecutionPolicy', 'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole')
      ]
    });

    const lambda_authorizer_function = new Function(this, 'lambda_authorizer_function', {
      runtime: Runtime.PYTHON_3_9,
      code: Code.fromAsset(join(__dirname, "../lambdas/lambda_authorizer")),
      handler: 'lambda_authorizer.lambda_handler',
      description: 'AWS Lambda authorizer to check if requester is able to invoke' + SCIM_provisioning_lambda_function.functionArn + '.',
      timeout: Duration.seconds(900),
      functionName: 'lambda-authorizer-scim-api-gw',
      memorySize: 512,
      role: lambda_authorizer_role,
      environment:{
        PARAMETER_NAME: api_key_name.parameterName
      },
    });

    const lambda_authorizer_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: "CloudwatchMetricUse",
          effect: iam.Effect.ALLOW,
          actions: [
            "cloudwatch:PutMetricData"
          ],
          resources: [
            "*",
          ]   
        }),
        new iam.PolicyStatement({
          sid: "DirectoryServiceAccess",
          effect: iam.Effect.ALLOW,
          actions: [
            "ds:CreateComputer",
            "ds:DescribeDirectories"
          ],
          resources: [
            "*",
          ]   
        }),
        new iam.PolicyStatement({
          sid: "SSMAccess",
          effect: iam.Effect.ALLOW,
          actions: [
            "ec2:DescribeInstanceStatus",
            "ec2messages:*",
            "ssm:*",
            "ssmmessages:CreateControlChannel",
            "ssmmessages:CreateDataChannel",
            "ssmmessages:OpenControlChannel",
            "ssmmessages:OpenDataChannel"
          ],
          resources: [
            "*",
          ]   
        }),
        new iam.PolicyStatement({
          sid: "SSMServiceLinkedRole",
          effect: iam.Effect.ALLOW,
          actions: [
            "iam:CreateServiceLinkedRole",
            "iam:DeleteServiceLinkedRole",
            "iam:GetServiceLinkedRoleDeletionStatus"
          ],
          resources: [
            "arn:aws:iam::*:role/aws-service-role/ssm.amazonaws.com/AWSServiceRoleForAmazonSSM*",
          ]   
        }),
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaAuthorizerManagedPolicy', {
      description: 'Policy to allow Lambda function to manage Amazon Connect instance users.',
      document:lambda_authorizer_policy,
      managedPolicyName: 'connect-scim-user-management-policy',
      roles: [lambda_authorizer_role]
    });

    // API Key generation Lambda function
    const api_key_generation_role = new iam.Role(this, 'api_key_generation_role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      roleName: "api_key_generation_role",
      managedPolicies: [
        iam.ManagedPolicy.fromManagedPolicyArn(this, 'lambdaAPIKeyGenerationExecutionPolicy', 'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole')
      ]
    });

    const api_key_generation_custom_resource = new Function(this, 'api_key_generation_custom_resource', {
      runtime: Runtime.PYTHON_3_9,
      code: Code.fromAsset(join(__dirname, "../lambdas/custom_resource")),
      handler: 'custom_resource.lambda_handler',
      description: 'Generates an API key to use for the Connect SCIM user management integration.',
      timeout: Duration.seconds(30),
      functionName: 'api-key-custom-resource-lambda-authorizer',
      memorySize: 512,
      role: api_key_generation_role,
      environment:{
        PARAMETER_NAME: api_key_name.parameterName
      },
    });

    const api_key_generation_policy = new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          sid: "SSMAllow",
          effect: iam.Effect.ALLOW,
          actions: [
            "ssm:PutParameter",
            "ssm:DeleteParameter",
            "ssm:DeleteParameters"
          ],
          resources: [
            api_key_name.parameterArn
          ]   
        })
      ],
    });

    new iam.ManagedPolicy(this, 'lambdaGenerateAPIKeyManagedPolicy', {
      description: 'Generates an API key and stores in AWS Systems Manager parameter.',
      document:api_key_generation_policy,
      managedPolicyName: 'lambda-api-key-custom-resource-policy',
      roles: [api_key_generation_role]
    });

    const provider = new customresources.Provider(this, 'ResourceProvider', {
      onEventHandler: api_key_generation_custom_resource,
      logRetention: RetentionDays.ONE_WEEK
    });

    const api_key_custom_action = new CustomResource(this, 'api_key_custom_action', {
      serviceToken: provider.serviceToken,
      resourceType: 'Custom::ActionTarget',
      properties: {
        Action: 'lambda:InvokeFunction',
          Description: 'The custom Resource to create Api key and used by Lambda and authorizer.',
          ApiLength: api_key_length,
          ServiceToken: api_key_generation_custom_resource.functionArn
      }
    });

    // SQS Queue
    const scim_api_gw_sqs_role = new iam.Role(this, "scim_api_gw_sqs_role", {
      assumedBy: new iam.ServicePrincipal("apigateway.amazonaws.com"),
    });

    // API Gateway for SCIM requests to Lambda function

    const scim_api_authorizer = new TokenAuthorizer(this, 'scim_api_authorizer', {
      handler: lambda_authorizer_function,
    })

    const scim_api_gw = new RestApi(this, 'scim_api_gw', {
      restApiName: 'connect-scim-api-gateway',
      cloudWatchRole: true,
      description: 'API GW invoked from IdP SCIM application to invoke the Amazon Connect user management lambda function.',
      endpointConfiguration: {
        types: [EndpointType.EDGE]
      },
      deploy: false
    });

    const scim_api_resource = scim_api_gw.root.addResource('{Users+}')

    // SQS TESTING -------------------------------------------------------------
    // const scim_sqs_dlq_queue = new Queue(this, "scim_sqs_dlq_queue",{
    //   queueName: 'connect-scim-sqs-dlq',
    //   retentionPeriod: Duration.days(10),
    // });

    // const scim_sqs_queue = new Queue(this, "scim_sqs_queue",{
    //   queueName: 'connect-scim-sqs',
    //   visibilityTimeout: Duration.seconds(900),
    //   deadLetterQueue:{
    //     maxReceiveCount: 1,
    //     queue: scim_sqs_dlq_queue
    //   }
    // });
    // scim_sqs_queue.grantConsumeMessages(SCIM_provisioning_lambda_function);
    // scim_sqs_queue.grantSendMessages(SCIM_provisioning_lambda_function);
    // scim_sqs_queue.grantSendMessages(scim_api_gw_sqs_role);

    // const sqs_message_integration = new AwsIntegration({
    //   service: "sqs",
    //   path: `${this.account}/${scim_sqs_queue.queueName}`,
    //   integrationHttpMethod: "POST",
    //   options: {
    //     credentialsRole: scim_api_gw_sqs_role,
    //     passthroughBehavior: PassthroughBehavior.NEVER,
    //     requestParameters: {
    //       //'method.request.path.proxy': 'true'
    //       'integration.request.header.Content-Type': `'application/x-www-form-urlencoded'`
    //     },
    //     requestTemplates: {
    //       //"application/json": `Action=SendMessage&MessageBody=$util.urlEncode("$method.request.querystring.message")`
    //       'application/json': 'Action=SendMessage&MessageBody=$input.body'
    //     },
    //     integrationResponses: [
    //       {
    //         statusCode: "200",
    //         responseTemplates: {
    //           "application/json": `{"done": true}`,
    //         },
    //       },
    //       {
    //         statusCode: '400',
    //       },
    //       {
    //         statusCode: '500',
    //       }
    //     ],
    //   },
    // })

    // scim_api_resource.addMethod("ANY", sqs_message_integration,
    //   {
    //     authorizer: scim_api_authorizer,
    //     // requestParameters:{
    //     //   'method.request.path.proxy': true
    //     // },
    //   }
    // );

    // const sqs_event_source = new SqsEventSource(scim_sqs_queue,{
    //   batchSize: 1
    // }) 
    //SCIM_provisioning_lambda_function.addEventSource(sqs_event_source)

    // SQS TESTING -------------------------------------------------------------

    scim_api_resource.addMethod('ANY', new Integration(
      {
        integrationHttpMethod: 'POST',
        type: IntegrationType.AWS_PROXY,
        uri: 'arn:' + this.partition + ':apigateway:' + this.region + ':lambda:path/2015-03-31/functions/' + SCIM_provisioning_lambda_function.functionArn + '/invocations'
      },
    ),
    {
      authorizer: scim_api_authorizer,
      requestParameters:{
        'method.request.path.proxy': true
      },
    })

    const scim_api_deployment = new Deployment(this, 'scim_api_deployment', {
      api: scim_api_gw,
    })

    const scim_api_stage = new Stage(this, 'dev', {
      stageName: 'dev',
      deployment: scim_api_deployment,
      // REMOVE AFTER TROUBLESHOOTING
      loggingLevel: MethodLoggingLevel.INFO,
      // REMOVE AFTER TROUBLESHOOTING
      dataTraceEnabled: true
    });

    scim_api_gw.addUsagePlan ('scim_api_usage', {
      apiStages:[{
        api:scim_api_gw,
        stage: scim_api_stage,
      }],
      quota:{
        limit: 5000,
        period: Period.DAY
      },
      throttle: {
        burstLimit: 1000,
        rateLimit: 500
      },
      name: 'scim-api-usage-plan'
    }) 

    SCIM_provisioning_lambda_function.addPermission('lambda_api_gw_scim_permission', {
      principal: new ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: 'arn:' + this.partition + ':execute-api:' + this.region + ':' + this.account + ':' + scim_api_gw.restApiId + '/' + scim_api_stage.stageName + '/*/*',
    })

    SCIM_provisioning_lambda_function.addPermission('lambda_api_gw_authorizer_permission', {
      principal: new ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: 'arn:' + this.partition + ':execute-api:' + this.region + ':' + this.account + ':' + scim_api_gw.restApiId + '/authorizers/' + scim_api_authorizer.authorizerId + '/*/*',
    })

    new CfnOutput(this,'Okta-API-Base-URL', {
      description:'Base URL for the SCIM 2.0 Test App (Header Auth) credentials to authorize provisioning users from the identity provider and the Connect instance',
      value: 'https://' + scim_api_gw.restApiId + '.execute-api.' + this.region + '.' + this.urlSuffix + '/' + scim_api_stage.stageName + '/Users?filter=userName%20eq%20%22test.user%22'
    })

    new CfnOutput(this,'Okta-API-Token-SSM-Parameter', {
      description:'The AWS Systems Manager parameter ARN that has the API Token to configure in the SCIM application to communicate with the API Gateway.',
      value: api_key_name.parameterArn
    })

  }
}
