output "Okta_Idp_Url" {
  value       = var.IsOKTAIdpType ? "${aws_api_gateway_stage.stage.invoke_url}/Users?filter=userName%20eq%20%22test.user" : null
  description = "URL to enter in OKTA SCIM provisioning"
}

output "Azure_Idp_Url" {
  value       = var.IsAzureIdpType ? "${aws_api_gateway_stage.stage.invoke_url}/scim/" : null
  description = "URL to enter in Azure SCIM provisioning"
}

output "IdPAPITokenSSMParameter" {
  value       = aws_ssm_parameter.apikey.arn
  description = "The Parameter store arn that contains the IDP Authorization API Key"
}
