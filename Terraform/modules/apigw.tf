resource "aws_api_gateway_rest_api" "connect_api" {
  name        = "UserManagementConnectAPI"
  description = "Amazon Connect User management API"
  body = templatefile(
    var.swagger_file_path,
    {
      "connect_user_management_lambda" = aws_lambda_function.connect_usermgmt_lambda.invoke_arn
      "auth_lambda_invoke_arn"         = aws_lambda_function.lambda_authorizer.invoke_arn
    }
  )
  endpoint_configuration {
    types = ["EDGE"]
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_deployment" "api_deployment" {
  rest_api_id = aws_api_gateway_rest_api.connect_api.id
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "stage" {
  depends_on = [aws_api_gateway_account.this]

  stage_name           = var.stage_name
  rest_api_id          = aws_api_gateway_rest_api.connect_api.id
  deployment_id        = aws_api_gateway_deployment.api_deployment.id
  xray_tracing_enabled = true

  # The log group below existed but was never referenced, so no access logs were
  # written. The format omits the caller identity and any header, so the bearer
  # token cannot reach the log.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.connect_access_log.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
    })
  }
}

# API Gateway needs an account-level role ARN before it will write execution or
# access logs. CloudFormation and CDK both create this; without it a fresh account
# fails on the stage's logging settings, and this module only appeared to work
# because the other two deployments had already set it account-wide.
#
# aws_api_gateway_account is an account-and-region singleton, so a second
# deployment in the same account would fight over it. Set
# manage_apigw_account_settings = false in that case and let the first own it.
resource "aws_iam_role" "apigw_cloudwatch" {
  count              = var.manage_apigw_account_settings ? 1 : 0
  name               = "connect-scim-apigw-cloudwatch"
  assume_role_policy = data.aws_iam_policy_document.apigw_assume_role.json
}

data "aws_iam_policy_document" "apigw_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "apigw_cloudwatch" {
  count      = var.manage_apigw_account_settings ? 1 : 0
  role       = aws_iam_role.apigw_cloudwatch[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  count               = var.manage_apigw_account_settings ? 1 : 0
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch[0].arn
}

resource "aws_cloudwatch_log_group" "connect_access_log" {
  name = "ConnectUserMgmtApiAccessLog"
  # Access logs are the audit trail for authentication attempts against the SCIM
  # endpoint, so they are retained for a year.
  retention_in_days = 365
}

# The Lambda functions otherwise write to implicit log groups that never expire.
# The names are distinct from the implicit /aws/lambda/<function> groups so that
# creating them cannot collide with logs an earlier deployment left behind.
resource "aws_cloudwatch_log_group" "usermgmt_lambda" {
  name              = "/aws/lambda/connect-scim-user-management-provisioning"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_cloudwatch_log_group" "authorizer_lambda" {
  name              = "/aws/lambda/connect-scim-user-management-authorizer"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_api_gateway_method_settings" "apisetttings" {
  rest_api_id = aws_api_gateway_rest_api.connect_api.id
  stage_name  = aws_api_gateway_stage.stage.stage_name
  method_path = "*/*"
  settings {
    metrics_enabled = true
    logging_level   = "ERROR"
    # Data tracing writes full request and response bodies, including the
    # Authorization header, to CloudWatch.
    data_trace_enabled = false
  }
}
