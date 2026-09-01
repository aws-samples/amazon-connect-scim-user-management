import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ConnnectUserManagement } from '../lib/connect_user_management';

function synth(idpType: string = 'okta'): Template {
  const app = new cdk.App({ context: { idp_type: idpType } });
  const stack = new ConnnectUserManagement(app, 'TestStack');
  return Template.fromStack(stack);
}

describe('idp_type context', () => {
  it('rejects an unsupported identity provider at synth time', () => {
    // The Lambda handler name is derived from this value, so a bad value has to
    // fail here rather than deploy a function that cannot import its handler.
    expect(() => synth('google')).toThrow(/Unsupported idp_type 'google'/);
  });

  it('rejects a missing identity provider', () => {
    expect(() => {
      const app = new cdk.App();
      new ConnnectUserManagement(app, 'TestStack');
    }).toThrow(/Unsupported idp_type/);
  });

  it.each(['okta', 'azure'])('accepts %s and wires up its handler', (idpType) => {
    synth(idpType).hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'connect-scim-user-management',
      Handler: `${idpType}.lambda_handler`,
    });
  });

  it('emits an Okta base URL with the connection-test filter', () => {
    const outputs = synth('okta').findOutputs('IdPAPIBaseURL');
    expect(JSON.stringify(outputs)).toContain('filter=userName');
  });

  it('emits an Entra ID base URL with the scim/v2 path', () => {
    const outputs = synth('azure').findOutputs('IdPAPIBaseURL');
    expect(JSON.stringify(outputs)).toContain('/scim/v2');
  });
});

describe('Lambda functions', () => {
  it('runs every function this stack authors on a supported Python runtime', () => {
    const functions = synth().findResources('AWS::Lambda::Function');
    // Only the functions this stack defines; the custom-resource provider's own
    // framework function is created and versioned by aws-cdk-lib.
    const runtimes = Object.values(functions)
      .filter((fn) => typeof fn.Properties.FunctionName === 'string')
      .map((fn) => fn.Properties.Runtime);
    expect(runtimes).toHaveLength(3);
    // python3.9 reached end of support on 2025-12-15.
    expect(runtimes).not.toContain('python3.9');
    for (const runtime of runtimes) {
      expect(runtime).toMatch(/^python3\.1[2-9]$/);
    }
  });

  it('gives the authorizer a short timeout', () => {
    synth().hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'lambda-authorizer-scim-api-gw',
      // An authorizer has to answer inside API Gateway's 29 second limit.
      Timeout: 10,
    });
  });

  it('passes the Connect instance and profile defaults to the handler', () => {
    synth().hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'connect-scim-user-management',
      Environment: {
        Variables: {
          INSTANCE_ID: { Ref: 'connectinstanceid' },
          DEFAULT_ROUTING_PROFILE: { Ref: 'defaultroutingprofile' },
          DEFAULT_SECURITY_PROFILE: { Ref: 'defaultsecurityprofile' },
        },
      },
    });
  });
});

describe('SCIM provisioning IAM policy', () => {
  it('grants only the Connect operations the handler calls', () => {
    synth().hasResourceProperties('AWS::IAM::ManagedPolicy', {
      ManagedPolicyName: 'connect-user-management-policy',
      PolicyDocument: {
        Statement: [
          Match.objectLike({
            Sid: 'ConnectManagement',
            Effect: 'Allow',
            Action: [
              'connect:CreateUser',
              'connect:DeleteUser',
              'connect:DescribeUser',
              'connect:ListRoutingProfiles',
              'connect:ListSecurityProfiles',
              'connect:ListUsers',
              'connect:SearchUsers',
              'connect:UpdateUserSecurityProfiles',
            ],
          }),
        ],
      },
    });
  });

  it('does not grant Connect actions the handler no longer uses', () => {
    const policy = JSON.stringify(
      synth().findResources('AWS::IAM::ManagedPolicy', {
        Properties: { ManagedPolicyName: 'connect-user-management-policy' },
      }),
    );
    expect(policy).not.toContain('connect:UpdateUserIdentityInfo');
    expect(policy).not.toContain('connect:DescribeSecurityProfile');
  });
});

describe('authorizer IAM policy', () => {
  const authorizerPolicy = () =>
    JSON.stringify(
      synth().findResources('AWS::IAM::ManagedPolicy', {
        Properties: { ManagedPolicyName: 'connect-scim-user-management-policy' },
      }),
    );

  it('grants only reading and decrypting the API token', () => {
    synth().hasResourceProperties('AWS::IAM::ManagedPolicy', {
      ManagedPolicyName: 'connect-scim-user-management-policy',
      PolicyDocument: {
        Statement: [
          Match.objectLike({ Sid: 'ReadApiToken', Action: 'ssm:GetParameter' }),
          Match.objectLike({ Sid: 'DecryptApiToken', Action: 'kms:Decrypt' }),
        ],
      },
    });
  });

  it.each([
    'ssm:*',
    'ec2messages:*',
    'ds:CreateComputer',
    'ds:DescribeDirectories',
    'cloudwatch:PutMetricData',
    'ssmmessages:CreateControlChannel',
    'iam:CreateServiceLinkedRole',
    'ec2:DescribeInstanceStatus',
  ])('no longer grants the unrelated permission %s', (action) => {
    // The previous policy carried an SSM-managed-instance permission set that
    // this function never used.
    expect(authorizerPolicy()).not.toContain(action);
  });

  it('does not grant the authorizer write access to the token', () => {
    expect(authorizerPolicy()).not.toContain('ssm:PutParameter');
    expect(authorizerPolicy()).not.toContain('ssm:DeleteParameter');
  });

  it('scopes the token read to a single parameter', () => {
    expect(authorizerPolicy()).toContain('parameter/connect/scim-integration/api-token');
    expect(authorizerPolicy()).not.toContain('"Resource":"*"');
  });
});

describe('API token', () => {
  it('is encrypted with a rotating customer-managed key', () => {
    synth().hasResourceProperties('AWS::KMS::Key', {
      EnableKeyRotation: true,
    });
  });

  it('is not created as a plaintext template parameter', () => {
    // Previously an AWS::SSM::Parameter was created holding the literal value
    // 'default' until the custom resource overwrote it, so the placeholder was
    // briefly a valid bearer token.
    const parameters = synth().findResources('AWS::SSM::Parameter');
    expect(Object.keys(parameters)).toHaveLength(0);
  });

  it('lets the generator write and encrypt, but nothing broader', () => {
    const policy = JSON.stringify(
      synth().findResources('AWS::IAM::ManagedPolicy', {
        Properties: { ManagedPolicyName: 'lambda-api-key-custom-resource-policy' },
      }),
    );
    expect(policy).toContain('ssm:PutParameter');
    expect(policy).toContain('kms:Encrypt');
    expect(policy).not.toContain('"ssm:*"');
  });

  it('does not pass ServiceToken through custom resource properties', () => {
    // ServiceToken is supplied by the provider; passing it in properties too made
    // CDK warn that the value would be overwritten.
    const resources = synth().findResources('Custom::ScimApiToken');
    const [resource] = Object.values(resources);
    expect(resource.Properties).toHaveProperty('ApiLength');
    expect(Object.keys(resource.Properties).filter((k) => k === 'ServiceToken')).toHaveLength(1);
  });

  it('constrains the requested token length', () => {
    const parameters = synth().toJSON().Parameters;
    expect(parameters.apikeylength.MinValue).toBe(32);
    expect(parameters.apikeylength.MaxValue).toBe(256);
  });
});

describe('API Gateway', () => {
  it('protects the SCIM method with the token authorizer', () => {
    synth().hasResourceProperties('AWS::ApiGateway::Method', {
      HttpMethod: 'ANY',
      AuthorizationType: 'CUSTOM',
    });
  });

  it('creates a request-header token authorizer', () => {
    synth().hasResourceProperties('AWS::ApiGateway::Authorizer', {
      Type: 'TOKEN',
      IdentitySource: 'method.request.header.Authorization',
    });
  });

  it('does not trace request and response bodies', () => {
    // Data tracing would write the bearer token and user attributes to logs.
    synth().hasResourceProperties('AWS::ApiGateway::Stage', {
      StageName: 'dev',
      MethodSettings: Match.arrayWith([Match.objectLike({ DataTraceEnabled: false })]),
    });
  });

  it('enables access logging', () => {
    synth().hasResourceProperties('AWS::ApiGateway::Stage', {
      AccessLogSetting: Match.objectLike({ DestinationArn: Match.anyValue() }),
    });
  });

  it('throttles through a usage plan', () => {
    synth().hasResourceProperties('AWS::ApiGateway::UsagePlan', {
      UsagePlanName: 'scim-api-usage-plan',
      Throttle: { BurstLimit: 1000, RateLimit: 500 },
    });
  });

  it('validates the Connect instance id format', () => {
    const parameters = synth().toJSON().Parameters;
    expect(parameters.connectinstanceid.AllowedPattern).toBeDefined();
  });
});

describe('template hygiene', () => {
  it('does not emit always-true conditions', () => {
    // idp_type is synth-time context, so comparing it in a CfnCondition produced
    // a condition CloudFormation reported as always true.
    const conditions = synth().toJSON().Conditions ?? {};
    expect(Object.keys(conditions)).toHaveLength(0);
  });

  it('retains log groups with an explicit retention', () => {
    const logGroups = synth().findResources('AWS::Logs::LogGroup');
    expect(Object.keys(logGroups).length).toBeGreaterThan(0);
    for (const group of Object.values(logGroups)) {
      expect(group.Properties.RetentionInDays).toBeGreaterThan(0);
    }
  });
});
