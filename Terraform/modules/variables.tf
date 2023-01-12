variable "connect_instance_id" {
  type        = string
  description = "The Connect Instance Id for user management"
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

variable "use_import_from_swagger" {
  type    = bool
  default = true
}

variable "swagger_file_path" {
  type = string
}

variable "default_routing_profile" {
  type    = string
  default = "Basic Routing Profile"
}
