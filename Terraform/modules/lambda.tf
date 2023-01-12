resource "aws_lambda_function" "connect_usermgmt_lambda" {
  s3_bucket     = var.s3_bucket
  s3_key        = var.s3_user_mgmt_object
  function_name = "connect_user_provisioning_lambda"
  role          = aws_iam_role.connect_user_management_role.arn
  handler       = "user_management_lambda.lambda_handler"
  runtime       = "python3.9"
  tracing_config {
    mode = "PassThrough"
  }
  reserved_concurrent_executions = -1
  timeout                        = 600
  environment {
    variables = {
      INSTANCE_ID             = var.connect_instance_id
      DEFAULT_ROUTING_PROFILE = var.default_routing_profile
    }
  }
}

resource "aws_lambda_function" "lambda_authorizer" {
  s3_bucket                      = var.s3_bucket
  s3_key                         = var.s3_lambda_auth_object
  function_name                  = "connect_lambda_authorizer"
  role                           = aws_iam_role.connect_lambda_authorizer_role.arn
  handler                        = "lambda_authorizer.lambda_handler"
  runtime                        = "python3.9"
  reserved_concurrent_executions = -1
  timeout                        = 600
  tracing_config {
    mode = "PassThrough"
  }
  environment {
    variables = {
      PARAMETER_NAME = aws_ssm_parameter.apikey.name
    }
  }
}

resource "aws_lambda_permission" "connect_lambda_permission" {
  statement_id  = "AllowApiInvokeConnect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.connect_usermgmt_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_stage.stage.execution_arn}/*/*"
}

resource "aws_lambda_permission" "Authorizer_lambda_permission" {
  statement_id  = "AllowApiInvokeConnectAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.lambda_authorizer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.connect_api.execution_arn}/authorizers/*"
}
