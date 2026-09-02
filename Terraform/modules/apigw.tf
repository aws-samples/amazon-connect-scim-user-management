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

resource "aws_cloudwatch_log_group" "connect_access_log" {
  name = "ConnectUserMgmtApiAccessLog"
  # Access logs are the audit trail for authentication attempts against the SCIM
  # endpoint, so they are retained for a year.
  retention_in_days = 365
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
