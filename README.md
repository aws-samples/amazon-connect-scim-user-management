# Amazon Connect User Management Code Artifact

The solution is recommened to be used in conjunction with an Amazon Connect instance deployed with SAML authentication to manage users and security profiles that is integrated with an identity provider’s System for Cross-domain Identity Management (SCIM) application. We will also walk through additional guardrails you need to implement to ensure this solution controls CRUD of users and associated permissions within the Amazon Connect instance.

## Prerequisites 

- Amazon Connect instance is deployed with SAML identity management.

- Mechanism to create security profiles within an Amazon Connect Instance.

- Application with an identity provider (Okta/Azure AD) to create a SCIM application. 

- Credentials for an IAM principal to provision resources in the same AWS account where the Amazon Connect instance is deployed.

## Solution Architecture

(1) API gateway has a configured to manage SCIM requests from an IdP.

(1) Authorizer Lambda function is primarily used to authenticate requests to the API gateway

(1) SCIM user management Lambda function handles the SCIM requests coming from the IdP to Create, Read, Update, Delete users and security profile associations.

## Authors

* Jonathan Nguyen [tamg@amazon.com] - Sr. Security consultant
* Gopinath Jagadesan [gopinjag@amazon.com] - Cloud Infrastructure Architect

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.