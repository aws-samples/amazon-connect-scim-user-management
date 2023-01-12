##############
# Module Call #
##############

module "amazon_connect_user_mgmgt" {
  source                = "./modules"
  connect_instance_id   = "<Amazon Connect Instance ID>"
  s3_bucket             = "<s3 bucket that cotains the Code for Lambda function provisioning>"
  s3_user_mgmt_object   = "<Zip file that contains the User management Lambda code.>"
  s3_lambda_auth_object = "<Zip file that contains the Lambda authorizer code.>"
  swagger_file_path     = "./modules/swaggerconnect.json"
  IsAzureIdpType        = true
}
