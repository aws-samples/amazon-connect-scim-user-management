variable "IsOKTAIdpType" {
  type        = bool
  default     = false
  description = "True if OKTA is the identity provider"
}

variable "IsAzureIdpType" {
  type        = bool
  default     = false
  description = "True if Azure is the identity provider"
}

# The variables below were previously hardcoded as placeholder strings in main.tf,
# so the dev.auto.tfvars workflow the README describes never actually drove them.
# They are declared here so that workflow works as documented.

variable "connect_instance_id" {
  type        = string
  description = "The Amazon Connect instance id to manage users in."

  # Validated here as well as in the module: this is the boundary the operator
  # actually sets, and the value ends up in the provisioning role's resource ARNs.
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.connect_instance_id))
    error_message = "connect_instance_id must be an Amazon Connect instance UUID."
  }
}

variable "s3_bucket" {
  type        = string
  description = "S3 bucket holding the zipped Lambda code."
}

variable "s3_user_mgmt_object" {
  type        = string
  description = "S3 key of the user-management Lambda zip. Must contain user_management_lambda.py, handler_core.py, connect_directory.py and scim.py at the archive root."
}

variable "s3_lambda_auth_object" {
  type        = string
  description = "S3 key of the Lambda authorizer zip."
}

variable "swagger_file_path" {
  type        = string
  default     = "./modules/swaggerconnect.json"
  description = "Path to the OpenAPI document used to build the API."
}

variable "stage_name" {
  type        = string
  default     = "dev"
  description = "API Gateway stage name."
}

variable "default_routing_profile" {
  type        = string
  default     = "Basic Routing Profile"
  description = "Routing profile assigned when the IdP supplies none."
}

variable "default_security_profile" {
  type        = string
  default     = "Agent"
  description = "Security profile assigned when the IdP supplies no entitlements."
}

variable "api_token_length" {
  type        = number
  default     = 32
  description = "Length of the generated SCIM API bearer token."
}

variable "manage_apigw_account_settings" {
  type        = bool
  default     = true
  description = "Create the account-level API Gateway CloudWatch role. Set false when another deployment in the same account already owns it."
}
