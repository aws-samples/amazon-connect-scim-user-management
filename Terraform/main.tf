##############
# Module Call #
##############

# Values come from variables rather than inline placeholders, so a
# dev.auto.tfvars file drives the deployment as the README describes.
module "amazon_connect_user_mgmgt" {
  source = "./modules"

  connect_instance_id   = var.connect_instance_id
  s3_bucket             = var.s3_bucket
  s3_user_mgmt_object   = var.s3_user_mgmt_object
  s3_lambda_auth_object = var.s3_lambda_auth_object
  swagger_file_path     = var.swagger_file_path
  stage_name            = var.stage_name

  default_routing_profile  = var.default_routing_profile
  default_security_profile = var.default_security_profile
  api_token_length         = var.api_token_length

  # Set exactly one of these to true in your tfvars.
  IsOKTAIdpType  = var.IsOKTAIdpType
  IsAzureIdpType = var.IsAzureIdpType
}
