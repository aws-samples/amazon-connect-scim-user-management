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
}

resource "aws_cloudwatch_log_group" "connect_access_log" {
  name              = "ConnectUserMgmtApiAccessLog"
  retention_in_days = 7
}

resource "aws_api_gateway_method_settings" "apisetttings" {
  rest_api_id = aws_api_gateway_rest_api.connect_api.id
  stage_name  = aws_api_gateway_stage.stage.stage_name
  method_path = "*/*"
  settings {
    metrics_enabled = true
    logging_level   = "INFO"
  }
}
