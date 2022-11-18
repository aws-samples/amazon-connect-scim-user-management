# Amazon Connect SCIM User Management

The solution is recommened to be used in conjunction with an Amazon Connect instance deployed with SAML authentication to manage users and security profiles that is integrated with an identity provider’s System for Cross-domain Identity Management (SCIM) application. We will also walk through additional guardrails you need to implement to ensure this solution controls CRUD of users and associated permissions within the Amazon Connect instance.

## Prerequisites 

- Amazon Connect instance is deployed with SAML identity management.
- Mechanism to create security profiles within an Amazon Connect Instance. For example, deploying security profiles using a CI/CD pipeline.
- SCIM application with an identity provider (IdP) (e.g. Okta/Azure AD). 
- Credentials for an IAM principal to provision resources in the same AWS account where the Amazon Connect instance is deployed.

## Solution Architecture

/scim_architecture_diagram.png

1. *SCIM Rest API* is an API Gateway to manage SCIM requests from an IdP application to the *SCIM user provisioning* Lambda function .
- * A resource {Users+}
- * A method for the {Users+} resource is created with a ‘POST"‘ for an AWS proxy to the SCIM user management Lambda function with a Lambda authorizer configured
- * A stage for ‘dev’ is configured to deploy the API gateway
- * A usage plan is created for the sage and associated configurations for throttling

2. *API token generation* Lambda function - during deployment, a custom resource creates an API Token required to configure your IdP’s API connection and stores the value in a Systems Manager parameter. This is to restrict authorized entities, such as the IdP application, is authenticated to invoke the API Gateway. 

**Important**: In a Production environment, this API token should be provisioned and stored in accordance with credential management standards of the environment.

3. *Authorizer* Lambda function is used to verify Header request auth tokens, such as (JSON Web Token) JWT or Oauth token, matches the Systems Manager parameter value to authorize requests to the API gateway. 

**Important**: Lambda authorizers can be configured in many ways and depend on requirements and standards used in the environment. You can read about different ways to [Use API Gateway Lambda authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-use-lambda-authorizer.html). 

4. *SCIM user provisioning* Lambda function handles the SCIM requests coming from the IdP to Create, Read, Update, Delete users and security profile associations.

## Authors

* Jonathan Nguyen [tamg@amazon.com] - Sr. Security consultant
* Gopinath Jagadesan [gopinjag@amazon.com] - Cloud Infrastructure Architect

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.