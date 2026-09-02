"""Custom resource entry point for the CDK deployment.

Runs behind the CDK ``custom_resources.Provider`` framework, which owns the
CloudFormation response protocol. This handler therefore *returns* its result and
must not post to ``ResponseURL``; doing both races the framework's own response.
The CloudFormation deployment uses a raw custom resource with no framework, so it
has a different entry point that posts the response itself.

The token logic itself lives in :mod:`api_token`, shared by both.
"""

import logging

import api_token

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Create, retain, or delete the SCIM API token parameter."""
    # The raw event is not logged: it carries a pre-signed ResponseURL, which is a
    # capability anyone could use to send CloudFormation a forged response.
    LOGGER.info(
        "Received %s request for %s",
        event.get("RequestType"),
        event.get("LogicalResourceId"),
    )
    data = api_token.apply(event["RequestType"], event.get("ResourceProperties", {}))
    # The framework provider turns this into the CloudFormation response.
    return {
        "PhysicalResourceId": event.get("PhysicalResourceId") or api_token.PARAMETER_NAME,
        "Data": data,
    }
