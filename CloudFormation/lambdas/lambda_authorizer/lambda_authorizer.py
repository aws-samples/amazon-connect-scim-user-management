"""API Gateway token authorizer for the Amazon Connect SCIM API.

Compares the bearer token presented by the identity provider against the token
held in AWS Systems Manager Parameter Store, and returns an IAM policy scoped to
the API and stage the request arrived on.

Notes on the comparison:

* ``secrets.compare_digest`` is used so the check does not leak the token through
  response timing.
* An unrecognised token raises ``Unauthorized``, which API Gateway renders as
  HTTP 401. That is what an identity provider expects for a bad credential, and
  what Okta's "Test API Credentials" check looks for. The previous version indexed
  past the end of a split list on any non-matching token, turning every failed
  authorization into an unhandled 500.
* The token is cached in the execution environment for a short period so a burst
  of provisioning calls does not make one ``GetParameter`` request each.
"""

import logging
import os
import re
import secrets
import time

import boto3

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

SSM_CLIENT = boto3.client("ssm")

PARAMETER_NAME = os.getenv("PARAMETER_NAME")
CACHE_TTL_SECONDS = int(os.getenv("TOKEN_CACHE_TTL_SECONDS", "300"))

# methodArn looks like:
#   arn:aws:execute-api:<region>:<account>:<apiId>/<stage>/<verb>/<resource path>
_METHOD_ARN = re.compile(
    r"^arn:(?P<partition>[^:]+):execute-api:(?P<region>[^:]*):(?P<account>[^:]*):"
    r"(?P<api_id>[^/]+)/(?P<stage>[^/]+)/"
)

_cache = {"token": None, "expires_at": 0.0}


def expected_token():
    """Return the configured API token, using a short-lived warm cache."""
    now = time.monotonic()
    if _cache["token"] is not None and now < _cache["expires_at"]:
        return _cache["token"]

    parameter = SSM_CLIENT.get_parameter(Name=PARAMETER_NAME, WithDecryption=True)
    token = parameter["Parameter"]["Value"]
    _cache["token"] = token
    _cache["expires_at"] = now + CACHE_TTL_SECONDS
    return token


def policy(effect, method_arn):
    """Build an authorizer policy covering every method of the calling API stage.

    The resource is pinned to the API id and stage taken from ``methodArn``, so a
    token valid for this deployment cannot be replayed against another API in the
    account.
    """
    match = _METHOD_ARN.match(method_arn)
    if not match:
        raise ValueError(f"Unrecognised methodArn: {method_arn}")
    parts = match.groupdict()
    resource = "arn:{partition}:execute-api:{region}:{account}:{api_id}/{stage}/*/*".format(**parts)
    return {
        "principalId": "connect-scim-provisioner",
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": [resource],
                }
            ],
        },
    }


def lambda_handler(event, context):
    """Authorize one SCIM request, or raise Unauthorized."""
    method_arn = event["methodArn"]
    # The presented token is never logged.
    LOGGER.info("Authorizing request for %s", method_arn)

    presented = event.get("authorizationToken") or ""
    # Okta and Entra ID both send 'Bearer <token>'. The bare token is also
    # accepted because the SCIM 2.0 Test App has sent it without the scheme.
    if presented.lower().startswith("bearer "):
        presented = presented[len("bearer ") :].strip()

    if not presented or not secrets.compare_digest(presented, expected_token()):
        LOGGER.warning("Rejected a request to %s: token did not match", method_arn)
        # API Gateway maps this exact message to HTTP 401.
        raise Exception("Unauthorized")

    LOGGER.info("Authorized request for %s", method_arn)
    return policy("Allow", method_arn)
