# The token is generated here rather than by a Lambda, because Terraform can
# create a SecureString parameter directly (CloudFormation cannot, which is why
# the CloudFormation deployment uses a custom resource instead).
#
# Note: random_string keeps its value in Terraform state, so protect state as a
# secret store regardless of the encryption applied to the parameter itself.
resource "random_password" "api_token" {
  length  = var.api_token_length
  special = false
  # Mixed case and digits give the full 62-character alphanumeric alphabet, the
  # same set the CDK and CloudFormation token generators use.
  upper   = true
  lower   = true
  numeric = true
}

resource "aws_kms_key" "api_token" {
  description             = "Encrypts the Amazon Connect SCIM API bearer token in Parameter Store."
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "api_token" {
  name          = "alias/connect-scim-api-token"
  target_key_id = aws_kms_key.api_token.key_id
}

resource "aws_ssm_parameter" "apikey" {
  name        = "/connect/scim-integration/api-token"
  description = "Bearer token the IdP SCIM application presents to the SCIM API."
  type        = "SecureString"
  key_id      = aws_kms_key.api_token.arn
  value       = random_password.api_token.result
}
