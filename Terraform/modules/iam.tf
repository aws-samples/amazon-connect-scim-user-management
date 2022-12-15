data "aws_caller_identity" "current" {}
data "aws_region" "current_region" {}

data "aws_iam_policy_document" "connect_user_managment" {
  statement {
    sid = "ConnectUserPermissions"

    actions = [
      "connect:UpdateUserIdentityInfo",
      "connect:DeleteUser",
      "connect:ListRoutingProfiles",
      "connect:ListUsers",
      "connect:CreateUser",
      "connect:DescribeUser",
      "connect:SearchUsers",
      "connect:ListSecurityProfiles",
      "connect:DescribeSecurityProfile",
      "connect:UpdateUserSecurityProfiles"
    ]
    resources = [
      "arn:aws:connect:${data.aws_region.current_region.name}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}",
      "arn:aws:connect:${data.aws_region.current_region.name}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/routing-profile/*",
      "arn:aws:connect:${data.aws_region.current_region.name}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/security-profile/*",
      "arn:aws:connect:${data.aws_region.current_region.name}:${data.aws_caller_identity.current.account_id}:instance/${var.connect_instance_id}/agent/*"
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

resource "aws_iam_role" "connect_user_management_role" {
  name               = "connect_user_management_lambda_role"
  assume_role_policy = data.aws_iam_policy_document.lambda-assume-role-policy.json
  managed_policy_arns = [
    aws_iam_policy.connect_policy.arn,
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
}

resource "aws_iam_role" "connect_lambda_authorizer_role" {
  name               = "connect_lambda_authorizer_role"
  assume_role_policy = data.aws_iam_policy_document.lambda-assume-role-policy.json
  managed_policy_arns = [
    aws_iam_policy.lambda_auth_policy.arn,
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
}
