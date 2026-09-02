data "aws_caller_identity" "current" {}
data "aws_region" "current_region" {}
data "aws_partition" "current" {}

data "aws_iam_policy_document" "connect_user_managment" {
  statement {
    sid = "ConnectUserPermissions"

    actions = [
      "connect:CreateUser",
      "connect:DeleteUser",
      "connect:DescribeUser",
      "connect:ListRoutingProfiles",
      "connect:ListSecurityProfiles",
      "connect:ListUsers",
      "connect:SearchUsers",
      "connect:UpdateUserSecurityProfiles"
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:connect:${data.aws_region.current_region.region}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}",
      "arn:${data.aws_partition.current.partition}:connect:${data.aws_region.current_region.region}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/routing-profile/*",
      "arn:${data.aws_partition.current.partition}:connect:${data.aws_region.current_region.region}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/security-profile/*",
      "arn:${data.aws_partition.current.partition}:connect:${data.aws_region.current_region.region}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/agent/*"
    ]
  }
}

data "aws_iam_policy_document" "connect_auth_policy" {
  statement {
    sid = "ConnectParameterPermissions"

    actions = [
      "ssm:GetParameter"
    ]
    resources = [
      aws_ssm_parameter.apikey.arn
    ]
  }

  statement {
    sid = "ConnectParameterDecrypt"

    actions = [
      "kms:Decrypt"
    ]
    resources = [
      aws_kms_key.api_token.arn
    ]
  }
}

data "aws_iam_policy_document" "lambda-assume-role-policy" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "connect_policy" {
  name   = "connect-user-mgmt-policy"
  policy = data.aws_iam_policy_document.connect_user_managment.json
}

resource "aws_iam_policy" "lambda_auth_policy" {
  name   = "connect-lambda-auth-policy"
  policy = data.aws_iam_policy_document.connect_auth_policy.json
}

locals {
  lambda_basic_execution_policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role" "connect_user_management_role" {
  name               = "connect_user_management_lambda_role"
  assume_role_policy = data.aws_iam_policy_document.lambda-assume-role-policy.json
}

# managed_policy_arns on aws_iam_role is deprecated in AWS provider 6.x; explicit
# attachment resources are the replacement.
resource "aws_iam_role_policy_attachment" "connect_user_management_connect" {
  role       = aws_iam_role.connect_user_management_role.name
  policy_arn = aws_iam_policy.connect_policy.arn
}

resource "aws_iam_role_policy_attachment" "connect_user_management_basic" {
  role       = aws_iam_role.connect_user_management_role.name
  policy_arn = local.lambda_basic_execution_policy_arn
}

resource "aws_iam_role" "connect_lambda_authorizer_role" {
  name               = "connect_lambda_authorizer_role"
  assume_role_policy = data.aws_iam_policy_document.lambda-assume-role-policy.json
}

resource "aws_iam_role_policy_attachment" "connect_authorizer_ssm" {
  role       = aws_iam_role.connect_lambda_authorizer_role.name
  policy_arn = aws_iam_policy.lambda_auth_policy.arn
}

resource "aws_iam_role_policy_attachment" "connect_authorizer_basic" {
  role       = aws_iam_role.connect_lambda_authorizer_role.name
  policy_arn = local.lambda_basic_execution_policy_arn
}
