# Amazon Connect SCIM User Management

The solution is recommened to be used in conjunction with an Amazon Connect instance deployed with SAML authentication to manage users and security profiles that is integrated with an identity provider’s System for Cross-domain Identity Management (SCIM) application. We will also walk through additional guardrails you need to implement to ensure this solution controls CRUD of users and associated permissions within the Amazon Connect instance.

## Prequisites 
1. Create an Amazon Connect instance - You can follow the steps outlined in the [Connect administrator guide](https://docs.aws.amazon.com/connect/latest/adminguide/amazon-connect-instances.html) using *SAML 2.0-based authentication* for identity authentication options and [configure SAML with IAM](https://docs.aws.amazon.com/connect/latest/adminguide/configure-saml.html).
2. Setup IdP instance:
- * For Okta, you can create an [Okta demo instance](https://developer.okta.com/signup/) for free. Otherwise, you can use an existing Okta instance you have access to and can create Okta applications.
3. Setup SAML federation for your Amazon Connect instance by following the (Amazon Connect SSO Setup Workshop)[https://catalog.us-east-1.prod.workshops.aws/workshops/33e6d0e7-f927-4531-abb1-f28a86ba0872/en-US].
4. Create Amazon Connect security and routing profiles - the solution deployed relies on Amazon Connect security profiles that exist. For demo or PoC purposes, you can use existing security and routing profiles or create a security profile in the AWS console using the steps outlined within the (Connect administrator guide)[https://docs.aws.amazon.com/connect/latest/adminguide/create-security-profile.html]. If using in an Production environment, make sure security and routing profiles are provisioned with appropriate permissions using authorized IAM service principals.
5. Credentials for an IAM principal to deploy resources listed in the Solution Architecture section in the same AWS account where the Amazon Connect instance is deployed.

## Solution Architecture

![connect_scim_architecture](/scim_architecture_diagram.png)

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

The solution relies on a separate Lambda function that is configured to invoke API calls on the Amazon Connect instance to manage CRUD for users and security profile associations. Amazon Connect API throttling quotas applicable for this solution and fall under a RateLimit of 2 requests per second, and a BurstLimit of 5 requests per second.. It is important to note the API throttling quotas are by AWS account per Region. If you have multiple Amazon Connect instances in a single AWS account and Region, the quotas will apply to all instances.

## Build
Before building this app, you need to clone the repository by running the following:

    $ git clone  git@github.com:aws-samples/amazon-connect-scim-user-management.git
    <clones the repository locally>

To build this app, you need to be in the project root folder. Then run the following:

    $ npm install -g aws-cdk
    <installs AWS CDK>

    $ npm install
    <installs appropriate packages found in the package.json>

## Deploy

    $ cdk bootstrap aws://<INSERT_CONNECT_AWS_ACCOUNT>/<INSERT_REGION>
    <builds S3 bucket for CDK to store files to perform deployment>

    $ cdk deploy ConnnectUserManagement --parameters connectinstanceid=<INSERT_AMAZON_CONNECT_INSTANCE_ID>
    <deploys the solution resources into an AWS account where you are authenticated.>
    Example Connect instance id: '12345678-1234-abcd-efgh-aaaaaabbccdd'

Note the following **Output** after the deployment completes:
1. *Okta-API-Base-URL* - Base URL for the SCIM 2.0 Test App (Header Auth) credentials to authorize provisioning users from the identity provider and the Connect instance.
2. *Okta-API-Token-SSM-Parameter* - The AWS Systems Manager parameter ARN that has the API Token to configure in the SCIM application to communicate with the API Gateway.
**Important**: This is for demo purposes and the API token should be provisioned and stored in accordance with credential management standards of the environment.

Once the CDK application has successfully deployed, you can configure the SCIM application for your identity provider. The following walkthrough will focus on Okta.

## Okta Setup

### Okta application setup
1. Once your SCIM application is created , select the **Provisioning** tab
2. Select **Configure API Integration** and select **Enable API integration**
3. Enter in the following information for the API integration:
- * Base URL: Enter in the API Gateway URL output **OktaAPIBaseURL** from the CloudFormation template (e.g. https://<API_GATEWAY_ID>.execute-api.<REGION>.amazonaws.com/dev/Users?filter=userName%20eq%20%22test.user)
- * API Token: Enter in the bearer token value found in the AWS Systems Manager parameter ARN listed in **OktaAPITokenSSMParameter**. The bearer token will be a 32 alphanumeric value (e.g. 123abc456def789ghi101jklexamples)
4. Once the information is entered, select **Test API Credentials**
- * If successful, you will see the message, “<SCIM application name> was verified successfully!”. Select **Save**
- * If unsuccessful, the error is most likely a result of an incorrectly entered Base URL and/or API Token. To resolve, look at the CloudWatch logs for the API gateway and lambda authorizer function. Alternatively, the error could be incorrect parameter (e.g. invalid Connect instance-id) or IAM permissions. To resolve, look at the CloudWatch logs for the lambda SCIM provisioner.
5. Additional Settings for **To App**, and **To Okta** should appear if the provisioning integration is successful
6. Select **To App**, and for the *Provisioning to App* settings, select **Edit**
- * Select *Enable* for the following settings:
- - * Create Users
- - * Update User Attributes
- - * Deactivate Users
- * Leave the *Sync Password* setting disabled
- * Select **Save**
7. Under the *<SCIM application name> Attribute Mappings*, select **Show Unmapped Attributes**
8. Edit the following unmapped attributes:
- * **entitlements**
- - * Select **Same value for all users**
- - * Select **Add Another** and enter in “Agent”
- - - * Note: Note: Since this security profile will get assigned to all users managed by the SCIM application, you should use an existing security profile with no permissions assigned. To add security profiles, add additional entitlements at the Okta group level which will add additional security profiles to each user.
- - * Select **Create and update**
- - * Select **Save**
- * **roles**
- - * Select **Same value for all users**
- - * Select **Add Another** and enter in Basic Routing Profile
- - * Select **Create**
- - * Select **Save**
9. Under the *<SCIM application name> Attribute Mappings*, select **Go to Profile Editor**
- * Edit the **entitlements** attribute
- - * For *Group Priority*, select **Combine values across groups**
- - * For *Attribute required*, select **Yes**
- - * Select **Save Attribute**
- * Note: You do not need to do this for the **roles** attribute.

### Okta Group Provisioning
Once the SCIM application is created, you can create/add Okta groups to manage users and assigning security profiles to the Connect instance. If you have existing Okta groups and associated users, the step to create a new Okta group and add user(s) can be skipped.
1. [Create a groups in Okta](https://help.okta.com/asa/en-us/Content/Topics/Adv_Server_Access/docs/setup/create-a-group.htm)
- * In your Okta instance, on the left-hand navigation select **Groups**
- * Enter in a *Name* and *Description* for the group and select **Save**
2. [Add user(s) to a group](https://help.okta.com/asa/en-us/Content/Topics/Adv_Server_Access/docs/group-add-user.htm)
- * Select the Group you want to add users to and select **Assign people**
- * For each user(s) you want to add select the **+** in that user’s row
- - * Note: All users in this group will have a user created within the Amazon Connect instance. Depending on the entitlements added, those users will have those specific permissions within the Connect instance.
3. Select the SCIM application you want to add a Group to and navigate to the **Assignments**
4. Select **Assign** and select **Assign to Groups**
- * Although there are multiple attributes to enter, it is important to enter in an entitlements value for the SCIM application to send the appropriate SCIM payload to the API gateway to manage users in Connect:
- - * **entitlements**
- - - * Select **Add Another**
- - - * Enter in the [Amazon Connect security profile name](https://docs.aws.amazon.com/connect/latest/adminguide/connect-security-profiles.html) you want all users in the Okta group to be assigned.
- - - - * Note: If you leave this blank, the **Agent** security profile (or whichever security profile value is assigned within the SCIM application) will be assigned to each user in this Group
- - * **roles**
- - - * Select **Add Another**
- - - * Enter in the [Amazon Connect routing profile name](https://docs.aws.amazon.com/connect/latest/adminguide/concepts-routing.html) you want all users in the Okta group to be assigned.
- - - - * Note: If you leave this blank, the **Basic routing profile** routing profile (or whichever security profile value is assigned within the SCIM application) will be assigned to each user in this Group
- - - - * The SCIM application has the **Basic routing profile** to be a default value assigned to each user in this Group. That default value for the routing profile can be updated by changing the value in the Lambda function’s environment variable **ROUTING_PROFILE**. You must assign a default routing profile to create Connect users. Once users are created, you should manage routing profiles outside of Okta as agents can be moved or assigned to different routing profiles as needed during a shift.

### Validate Okta SCIM application 
1. Add or Remove a user in the Okta group assigned to the SCIM application
2. Add or Remove an Okta group assigned to the SCIM application.
3. Add or Remove Okta group attributes, such as **entitlements** which associate an Amazon Connect security profile to all users within that AD group


## CDK Toolkit

The [`cdk.json`](./cdk.json) file in the root of this repository includes
instructions for the CDK toolkit on how to execute this program.

After building your TypeScript code, you will be able to run the CDK toolkits commands as usual:

    $ cdk ls
    <list all stacks in this program>

    $ cdk synth
    <generates and outputs cloudformation template>

    $ cdk deploy
    <deploys stack to your account>

    $ cdk diff
    <shows diff against deployed stack>

## Authors

* Jonathan Nguyen [tamg@amazon.com] - Sr. Security consultant
* Gopinath Jagadesan [gopinjag@amazon.com] - Cloud Infrastructure Architect

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.