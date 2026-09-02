"""Custom resource entry point for the CloudFormation deployment.

Backs a raw ``AWS::CloudFormation::CustomResource``. There is no framework in
front of it, so this handler is responsible for posting the result to the
pre-signed ``ResponseURL``. If it does not, CloudFormation has nothing to wait on
and the stack sits in CREATE_IN_PROGRESS until the resource times out an hour
later.

That is the one thing this file does differently from the CDK entry point, which
runs behind ``custom_resources.Provider`` and must *not* post the response. The
token logic is shared through :mod:`api_token`.
"""

import json
import logging

import api_token
import urllib3

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

HTTP = urllib3.PoolManager()


def send_response(event, context, status, data=None, reason=None):
    """Post the resource outcome to the pre-signed CloudFormation ResponseURL."""
    body = {
        "Status": status,
        "Reason": reason or f"See CloudWatch log stream: {context.log_stream_name}",
        "PhysicalResourceId": event.get("PhysicalResourceId") or api_token.PARAMETER_NAME,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }
    encoded = json.dumps(body).encode("utf-8")
    # The URL is a capability and is deliberately not logged.
    LOGGER.info("Reporting %s for %s", status, event["LogicalResourceId"])
    response = HTTP.request(
        "PUT",
        event["ResponseURL"],
        body=encoded,
        headers={"content-type": "", "content-length": str(len(encoded))},
    )
    LOGGER.info("CloudFormation acknowledged the response with HTTP %s", response.status)


def lambda_handler(event, context):
    """Create, retain, or delete the SCIM API token parameter."""
    LOGGER.info(
        "Received %s request for %s",
        event.get("RequestType"),
        event.get("LogicalResourceId"),
    )
    try:
        data = api_token.apply(event["RequestType"], event.get("ResourceProperties", {}))
    except Exception as error:  # noqa: BLE001
        # A failure must still be reported, or the stack waits out the full
        # resource timeout instead of rolling back.
        LOGGER.exception("Token provisioning failed")
        send_response(event, context, "FAILED", reason=str(error))
        raise
    send_response(event, context, "SUCCESS", data=data)
