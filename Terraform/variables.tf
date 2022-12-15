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
