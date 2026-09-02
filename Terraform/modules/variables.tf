variable "connect_instance_id" {
  type        = string
  description = "The Connect Instance Id for user management"

  # Interpolated into the provisioning role's resource ARNs, so an unconstrained
  # value ("*") would widen the role to every Connect instance in the account.
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.connect_instance_id))
    error_message = "connect_instance_id must be an Amazon Connect instance UUID."
  }
}

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

variable "s3_bucket" {
  type        = string
  description = "The s3 bucket that contains all the lambda code"
}

variable "s3_user_mgmt_object" {
  type        = string
  description = "The s3 object key for the user management lambda"
}

variable "s3_lambda_auth_object" {
  type        = string
  description = "The s3 object key for the Authorizer lambda"
}

variable "stage_name" {
  type        = string
  description = "The stage to be created for specific api"
  default     = "dev"
}

variable "swagger_file_path" {
  type = string
}

variable "default_routing_profile" {
  type    = string
  default = "Basic Routing Profile"
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

  validation {
    condition     = var.api_token_length >= 32 && var.api_token_length <= 256
    error_message = "api_token_length must be between 32 and 256."
  }
}
