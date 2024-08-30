# Amazon Connect SCIM User Management

The solution is recommened to be used in conjunction with an Amazon Connect instance deployed with SAML authentication to manage users and security profiles that is integrated with an identity provider’s System for Cross-domain Identity Management (SCIM) application. We will also walk through additional guardrails you need to implement to ensure this solution controls CRUD of users and associated permissions within the Amazon Connect instance.

## Prequisites

1. Create an Amazon Connect instance - You can follow the steps outlined in the [Connect administrator guide](https://docs.aws.amazon.com/connect/latest/adminguide/amazon-connect-instances.html) using *SAML 2.0-based authentication* for identity authentication options and [configure SAML with IAM](https://docs.aws.amazon.com/connect/latest/adminguide/configure-saml.html).
2. Setup IdP instance:

* For Okta, you can create an [Okta demo instance](https://developer.okta.com/signup/) for free. Otherwise, you can use an existing Okta instance you have access to and can create Okta applications.

3. Setup SAML federation for your Amazon Connect instance by following the [Amazon Connect SSO Setup Workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/33e6d0e7-f927-4531-abb1-f28a86ba0872/en-US).
4. Create Amazon Connect security and routing profiles - the solution deployed relies on Amazon Connect security profiles that exist. For demo or PoC purposes, you can use existing security and routing profiles or create a security profile in the AWS console using the steps outlined within the [Connect administrator guide](https://docs.aws.amazon.com/connect/latest/adminguide/create-security-profile.html). If using in an Production environment, make sure security and routing profiles are provisioned with appropriate permissions using authorized IAM service principals.
5. Credentials for an IAM principal to deploy resources listed in the Solution Architecture section in the same AWS account where the Amazon Connect instance is deployed.

## Solution Architecture

![connect_scim_architecture](/scim_architecture_diagram.png)

1. *SCIM Rest API* is an API Gateway to manage SCIM requests from an IdP application to the *SCIM user provisioning* Lambda function .

* A resource {Users+}
* A method for the {Users+} resource is created with a ‘POST"‘ for an AWS proxy to the SCIM user management Lambda function with a Lambda authorizer configured
* A stage for ‘dev’ is configured to deploy the API gateway
* A usage plan is created for the sage and associated configurations for throttling

2. *API token generation* Lambda function - during deployment, a custom resource creates an API Token required to configure your IdP’s API connection and stores the value in a Systems Manager parameter. This is to restrict authorized entities, such as the IdP application, is authenticated to invoke the API Gateway.

**Important**: In a Production environment, this API token should be provisioned and stored in accordance with credential management standards of the environment.

3. *Authorizer* Lambda function is used to verify Header request auth tokens, such as (JSON Web Token) JWT or Oauth token, matches the Systems Manager parameter value to authorize requests to the API gateway.

**Important**: Lambda authorizers can be configured in many ways and depend on requirements and standards used in the environment. You can read about different ways to [Use API Gateway Lambda authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-use-lambda-authorizer.html).

4. *SCIM user provisioning* Lambda function handles the SCIM requests coming from the IdP to Create, Read, Update, Delete users and security profile associations.

The solution relies on a separate Lambda function that is configured to invoke API calls on the Amazon Connect instance to manage CRUD for users and security profile associations. Amazon Connect API throttling quotas applicable for this solution and fall under a RateLimit of 2 requests per second, and a BurstLimit of 5 requests per second.. It is important to note the API throttling quotas are by AWS account per Region. If you have multiple Amazon Connect instances in a single AWS account and Region, the quotas will apply to all instances.

We have provided with 3 Infrastructure as code options as part of this repository. Use the preferred IaC to deploy the SCIM API solution.

## CDK

### Build

Before building this app, you need to clone the repository by running the following:

    $ git clone  git@github.com:aws-samples/amazon-connect-scim-user-management.git
    <clones the repository locally>

To build this app, you need to be in the project root folder. Then run the following:

    $ npm install -g aws-cdk
    <installs AWS CDK>

    $ npm install
    <installs appropriate packages found in the package.json>

### Deploy

    $ cdk bootstrap aws://<INSERT_CONNECT_AWS_ACCOUNT>/<INSERT_REGION>
    <builds S3 bucket for CDK to store files to perform deployment>

    $ cdk deploy ConnnectUserManagement --parameters connectinstanceid=<INSERT_AMAZON_CONNECT_INSTANCE_ID>
    <deploys the solution resources into an AWS account where you are authenticated.>
    Example Connect instance id: '12345678-1234-abcd-efgh-aaaaaabbccdd'

Note the following **Output** after the deployment completes:

1. *IdP-API-Base-URL* - Base URL for the SCIM 2.0 Test App (Header Auth) credentials to authorize provisioning users from the identity provider and the Connect instance.
2. *IdP-API-Token-SSM-Parameter* - The AWS Systems Manager parameter ARN that has the API Token to configure in the SCIM application to communicate with the API Gateway.

## CloudFormation

* The Cloudformation template to deploy the SCIM solution can be downloaded from [here](./CloudFormation/user_management_cloudformation.yaml).
* The SCIM solution creates 3 Lambda function, download the below Lambda code
    [Authorizer Lambda code](./CloudFormation/lambdas/lambda_authorizer/lambda_authorizer.py)

    [API key Lambda code](./CloudFormation/lambdas/custom_resource/custom_resource_lambda.py)

    [OKTA User management Lambda code](./CloudFormation/lambdas/user_management/okta_idp/user_management_lambda.py)

    [AZURE User management Lambda code](./CloudFormation/lambdas/user_management/azure_idp/user_management_lambda.py)

**NOTE:**  Either OKTA or Azure User management Lambda code can be downloaded based on the Idp Type.

* Compress the Lambda code to **.Zip** format and upload the Lambda code to an existing s3 Bucket or Create a new bucket and upload the Lambda code. Click [here](https://docs.aws.amazon.com/AmazonS3/latest/userguide/create-bucket-overview.html) to see the steps to create s3 bucket.

## Deploy the SCIM Solution from Console

* Sign in to your AWS account and select the appropriate AWS Region which contains the Amazon Connect instance.
* Open the [AWS CloudFormation console](https://console.aws.amazon.com/cloudformation)
* Select Create Stack with new Resources option.
* Select **upload new template** and upload the template that was downloaded from the previous section.
* Pass the below values as Parameters for the stack deployment to be successful.

### SCIM Configuration on AWS

    * Stack name - Name of the Stack e.g., AmazonConnectUserManagement
    * Enter your Amazon Connect Instance Id - Connect instance ID 
    * Enter the Length for the API key to be generated for lambda authorizer - The IDP Authorization Key Length.
    * The Idptype for user management , allowed values OKTA , Azure
    * The default routing profile that will be associated with the User provisioning - Default value "Basic routing Profile"

### Artifact details for Lambda function provisioning

The SCIM solution Provisions 3 Lambda functions, pass the s3 bucket name and the Object key in a **.ZIP** format.

    * s3 bucket that cotains the Code for Lambda function provisioning
    * Zip file that contains the User management Lambda code.
    * Zip file that contains the Lambda authorizer code.
    * Zip file that cotains the API Key for IDP Authorization Lambda code.

* Click Next twice and select **I acknowledge that AWS CloudFormation might create IAM resources with custom names.**.
* Click **Submit** to deploy the SCIM solution.

Note the following **Output** after the deployment completes:

1. *IdP-API-Base-URL* - Base URL for the SCIM 2.0 Test App (Header Auth) credentials to authorize provisioning users from the identity provider and the Connect instance.
2. *IdP-API-Token-SSM-Parameter* - The AWS Systems Manager parameter store that has the API Token to configure in the SCIM application to communicate with the API Gateway.

## Terraform

### Download the Terraform Code Files and Artifacts

* All the **.tf** files required for the deployment of the SCIM solution can be found [here](./Terraform/)
* The SCIM solution creates 2 Lambda function, download the below Lambda code.

    [Authorizer Lambda code](./Terraform/lambdas/lambda_authorizer/lambda_authorizer.py)

    [OKTA User management Lambda code](./Terraform/lambdas/user_management/okta_idp/user_management_lambda.py)

    [AZURE User management Lambda code](./Terraform/lambdas/user_management/azure_idp/user_management_lambda.py)

**NOTE:**  Either OKTA or Azure User management Lambda code can be downloaded based on the Idp Type.

* Compress the Lambda code to **.Zip** format and upload the Lambda code to an existing s3 Bucket or Create a new bucket and upload the Lambda code. Click [here](https://docs.aws.amazon.com/AmazonS3/latest/userguide/create-bucket-overview.html) to see the steps to create s3 bucket.

## Deploy the SCIM Solution

* Create a directory and copy all the **.tf** files to the folder.
* Create a file **dev.auto.tfvars** and pass the below values for the variable

### Variables

    * connect_instance_id     = (Amazon Connect Instance ID)
    * default_routing_profile = (The Routing profile to be associated with user , default value is "Basic Routing Profile")
    * s3_bucket               = (s3 bucket that cotains the Code for Lambda function provisioning)
    * s3_user_mgmt_object     = (Zip file that contains the User management Lambda code.)
    * s3_lambda_auth_object   = (Zip file that contains the Lambda authorizer code.)
    * stage_name              = (Stage name to be used for creation of API, default value is "dev")
    * swagger_file_path       = (Path of the swagger file that is used to deploy the api gateway)
    * IsAzureIdpType          = (bool value for Azure Idp type, default value is false)
    * IsOktaIdpType           = (bool value for Azure Idp type, default value is false)

***NOTE:*** An example swagger file is included in the ![repo](./Terraform/modules/swaggerconnect.json). This can be modified based on your organization requirements.

* Configure Credentials of the AWS Account to which the SCIM solution to be provisioned. Click [here](https://registry.terraform.io/providers/hashicorp/aws/latest/docs) to see the steps.

* Run **terraform init** to ensure the provider used in the **versions.tf** are downloaded successfully.
* Run **terraform plan** and verify if the resources to be provisioned are as expected.
* Run **terraform apply --auto-approve** to deploy the resources required to be provisioned.

Note the following **Output** after the deployment completes:

1. *IdP-API-Base-URL* - Base URL for the SCIM 2.0 Test App (Header Auth) credentials to authorize provisioning users from the identity provider and the Connect instance.
2. *IdP-API-Token-SSM-Parameter* - The AWS Systems Manager parameter Arn that has the API Token to configure in the SCIM application to communicate with the API Gateway.

**Important**: This is for demo purposes and the API token should be provisioned and stored in accordance with credential management standards of the environment.

Once the SCIM Solution has successfully deployed based on any one of the above Iac, you can configure the SCIM application for your identity provider.

## Okta Setup

### Okta application creation

1. Sign in to your Okta instance as a user with appropriate permissions to Create Applications
2. On the left-hand navigation, select **Applications** and then select **Applications**
3. Select **Browse App Catalog**
4. In the search bar, enter in “*SCIM 2.0 Test App (Header Auth)*" and select **Add Integration**
5. Enter an “Application label” name and select **Next**
6. For the *Sign-on method*, select **SAML 2.0**

* Leave the default selection and the Default Relay State empty

7. For the *Credential Details*, select the following:

* Application username format: **Okta username**

* Update application username on: **Create and update**

8. Select **Done** to create the application

### Okta application setup

1. Once your SCIM application is created , select the **Provisioning** tab
2. Select **Configure API Integration** and select **Enable API integration**
3. Enter in the following information for the API integration:

* Base URL: Enter in the API Gateway URL output **OktaAPIBaseURL** from the CloudFormation template (e.g. <<https://<API_GATEWAY_ID>.execute-api>>.<REGION>.amazonaws.com/dev/Users?filter=userName%20eq%20%22test.user)
* API Token: Enter in the bearer token value found in the AWS Systems Manager parameter ARN listed in **OktaAPITokenSSMParameter**. The bearer token will be a 32 alphanumeric value (e.g. 123abc456def789ghi101jklexamples)

4. Once the information is entered, select **Test API Credentials**

* If successful, you will see the message, “<SCIM application name> was verified successfully!”. Select **Save**
* If unsuccessful, the error is most likely a result of an incorrectly entered Base URL and/or API Token. To resolve, look at the CloudWatch logs for the API gateway and lambda authorizer function. Alternatively, the error could be incorrect parameter (e.g. invalid Connect instance-id) or IAM permissions. To resolve, look at the CloudWatch logs for the lambda SCIM provisioner.

5. Additional Settings for **To App**, and **To Okta** should appear if the provisioning integration is successful
6. Select **To App**, and for the *Provisioning to App* settings, select **Edit**

* Select *Enable* for the following settings:
  * Create Users
  * Update User Attributes
  * Deactivate Users
* Leave the *Sync Password* setting disabled
* Select **Save**

7. Under the *<SCIM application name> Attribute Mappings*, select **Show Unmapped Attributes**
8. Edit the following unmapped attributes:

* **entitlements**
  * Select **Same value for all users**
  * Select **Add Another** and enter in “Agent”
    * Note: Note: Since this security profile will get assigned to all users managed by the SCIM application, you should use an existing security profile with no permissions assigned. To add security profiles, add additional entitlements at the Okta group level which will add additional security profiles to each user.
  * Select **Create and update**
  * Select **Save**
* **roles**
  * Select **Same value for all users**
  * Select **Add Another** and enter in Basic Routing Profile
  * Select **Create**
  * Select **Save**

9. Under the *<SCIM application name> Attribute Mappings*, select **Go to Profile Editor**

* Edit the **entitlements** attribute
  * For *Group Priority*, select **Combine values across groups**
  * For *Attribute required*, select **Yes**
  * Select **Save Attribute**
* Note: You do not need to do this for the **roles** attribute.

### Okta Group Provisioning

Once the SCIM application is created, you can create/add Okta groups to manage users and assigning security profiles to the Connect instance. If you have existing Okta groups and associated users, the step to create a new Okta group and add user(s) can be skipped.

1. [Create a groups in Okta](https://help.okta.com/asa/en-us/Content/Topics/Adv_Server_Access/docs/setup/create-a-group.htm)

* In your Okta instance, on the left-hand navigation select **Groups**
* Enter in a *Name* and *Description* for the group and select **Save**

2. [Add user(s) to a group](https://help.okta.com/asa/en-us/Content/Topics/Adv_Server_Access/docs/group-add-user.htm)

* Select the Group you want to add users to and select **Assign people**
* For each user(s) you want to add select the **+** in that user’s row
  * Note: All users in this group will have a user created within the Amazon Connect instance. Depending on the entitlements added, those users will have those specific permissions within the Connect instance.

3. Select the SCIM application you want to add a Group to and navigate to the **Assignments**
4. Select **Assign** and select **Assign to Groups**

* Although there are multiple attributes to enter, it is important to enter in an entitlements value for the SCIM application to send the appropriate SCIM payload to the API gateway to manage users in Connect:
  * **entitlements**
    * Select **Add Another**
    * Enter in the [Amazon Connect security profile name](https://docs.aws.amazon.com/connect/latest/adminguide/connect-security-profiles.html) you want all users in the Okta group to be assigned.
      * Note: If you leave this blank, the **Agent** security profile (or whichever security profile value is assigned within the SCIM application) will be assigned to each user in this Group
  * **roles**
    * Select **Add Another**
    * Enter in the [Amazon Connect routing profile name](https://docs.aws.amazon.com/connect/latest/adminguide/concepts-routing.html) you want all users in the Okta group to be assigned.
      * Note: If you leave this blank, the **Basic routing profile** routing profile (or whichever security profile value is assigned within the SCIM application) will be assigned to each user in this Group
      * The SCIM application has the **Basic routing profile** to be a default value assigned to each user in this Group. That default value for the routing profile can be updated by changing the value in the Lambda function’s environment variable **ROUTING_PROFILE**. You must assign a default routing profile to create Connect users. Once users are created, you should manage routing profiles outside of Okta as agents can be moved or assigned to different routing profiles as needed during a shift.

### Validate Okta SCIM application

1. Add or Remove a user in the Okta group assigned to the SCIM application
2. Add or Remove an Okta group assigned to the SCIM application.
3. Add or Remove Okta group attributes, such as **entitlements** which associate an Amazon Connect security profile to all users within that AD group

## Azure Setup

### Azure Application Creation

1. Sign in to your Azure AD Portal as a user with appropriate permissions to Create Enterprise Applications.
2. On the left-hand navigation, select **Enterprise Applications** and then select **New Application**
3. Select **Create your own application** and give your application a name e.g.,AzureSCIMConnectProvisioning.Select **Create**, leaving the default options.
4. Select **Provisioning** on the left-hand navigation from the application. Select Provisioning mode as **Automatic** and in the Admin credentials, enter the URL and Secret token obtained from the SCIM solution deployment output section.
5. Click on **Test Connection** and validate if it returns **Success**.
6. As we will be provisioning only users in Amazon Connect instance, Select **Edit Attribute mappings** in the Provisioning section and you have Provision Azure Active Directory Groups Enabled value set to **No**
7. In the same Mappings section, Select **Provision Azure Active Directory User** and ensure only the below values are present. If there are other default values, delete them. Since we are using only these attributes during user creation, others can be deleted.

### AzureAD Group Provisioning

The SCIM application built on the AWS account, looks for the specific user attribute to determine the Security Profile to be associated with the User at the time of Provisioning. The Lambda code looks for **Department** attibute in payload.The User in the Azure AD should have **Department** attribute with the value mapping to the Security profile name present in the Connect instance e.g., Agent, Admin, CallCenterManager etc. In order for user to be provisioned in connect instance at SCALE, we will leverage [Azure Dynamic AD groups](https://learn.microsoft.com/en-us/azure/active-directory/enterprise-users/groups-create-rule).

1. Select **Groups** from the Azure AD portal and select **New Group**
2. Enter the Group name e.g. Connect-Admin and enter meaningful description.
3. Select **Dynamic User** from the **Membership type**.
4. Select **Add dynamic query** and select the below options
    * Property - Department
    * Operator - Equals
    * Value - (The Security profile name) e.g, Admin
5. Click Save and Create group.

Create as many number of Dynamic Groups based on the different Security profile that has to be assigned to various users. Since it's Dynamic Groups, the users are automatically assigned to the necessary Group based on the Department to which they are associated.

**NOTE:**

1. In case the **Department** attribute is already being used, use other attribute of the user but make sure the Lambda code is updated as well.
2. There could be a possibility where the user can be part of more than 1 group, ensure you add the security profile name comma separated e.g., Admin,CallCenterManager. When creating the dyanamic rule select the below options
    * Property - Department
    * Operator - Equals
    * Value - Admin,CallCenterManager

### Test Provisioning

1. Navigate to the application created in the previously and select **Users and Groups** from the left Navigation pane.
2. Select **Add User/Group** and add the dynamic group(s) created from the previous step.
3. Select **provisioning** from the application and then select **start provisioning**.

### Validation AzureAD SCIM Integration

1. Create an Azure SCIM application
2. Setup and integrate the SCIM application with AWS API Gateway and send the appropriate payload for managing Connect users.
3. Configure the Attribute mappings in the Azure SCIM application.
4. Create Azure Dynamic Groups
5. Assign Azure Dynamic Groups groups to the SCIM application to add/remove/update users and user attributes
6. Validate the Azure SCIM integration on your Amazon Connect instance.

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

* Jonathan Nguyen [tamg@amazon.com] - Sr. Security Consultant
* Gopinath Jagadesan [gopinjag@amazon.com] - Cloud Infrastructure Architect

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
