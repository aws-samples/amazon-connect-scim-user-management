output "Okta_Url" {
  value       = var.IsOKTAIdpType ? "${module.amazon_connect_user_mgmgt.Okta_Idp_Url}" : null
  description = "URL to enter in OKTA SCIM provisioning"
}

output "Azure_Url" {
  value       = var.IsAzureIdpType ? "${module.amazon_connect_user_mgmgt.Azure_Idp_Url}" : null
  description = "URL to enter in Azure SCIM provisioning"
}

output "APITokenSSMParameter" {
  value       = module.amazon_connect_user_mgmgt.IdPAPITokenSSMParameter
  description = "The Parameter store arn that contains the IDP Authorization API Key"
}
