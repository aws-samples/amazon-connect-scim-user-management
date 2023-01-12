resource "random_string" "instance_alias" {
  length  = 32
  special = false
  numeric = false
  upper   = false
}

resource "aws_ssm_parameter" "apikey" {
  name  = "/connect/scim-integration/api-token"
  type  = "StringList"
  value = random_string.instance_alias.result
}
