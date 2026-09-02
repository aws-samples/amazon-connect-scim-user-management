"""Generates and stores the SCIM API bearer token.

This module holds the token logic only. How the result is reported back to
CloudFormation differs per deployment and lives in the entry point beside it:

* the CDK deployment runs behind ``custom_resources.Provider``, whose framework
  function owns the CloudFormation response protocol, so its handler returns a
  result object and must *not* post to ``ResponseURL``;
* the CloudFormation deployment uses a raw ``AWS::CloudFormation::CustomResource``
  with no framework in front of it, so its handler has to post the response
  itself or the stack waits until the resource times out.

Both entry points call :func:`apply` for the actual work.
"""

import logging
import os
import secrets
import string

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()

SSM_CLIENT = boto3.client("ssm")

PARAMETER_NAME = os.getenv("PARAMETER_NAME")
KMS_KEY_ID = os.getenv("KMS_KEY_ID")

# Okta and Entra ID send the token in an Authorization header, so restrict it to
# characters that need no encoding.
TOKEN_ALPHABET = string.ascii_letters + string.digits

MIN_TOKEN_LENGTH = 32
MAX_TOKEN_LENGTH = 256


def generate_token(length):
    """Generate a cryptographically secure alphanumeric token.

    ``secrets.choice`` samples with replacement, unlike ``random.sample``, so the
    full alphabet is available at every position and the token length is not
    capped by the alphabet size.
    """
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def requested_length(properties):
    """Read and validate the requested token length."""
    raw = properties.get("ApiLength", MIN_TOKEN_LENGTH)
    try:
        length = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ApiLength must be an integer, got {raw!r}") from exc
    if not MIN_TOKEN_LENGTH <= length <= MAX_TOKEN_LENGTH:
        raise ValueError(
            f"ApiLength must be between {MIN_TOKEN_LENGTH} and {MAX_TOKEN_LENGTH}, got {length}"
        )
    return length


def parameter_exists():
    """Return True when the token parameter is already present."""
    try:
        SSM_CLIENT.get_parameter(Name=PARAMETER_NAME, WithDecryption=False)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ParameterNotFound":
            return False
        raise
    return True


def put_token(length):
    """Write a freshly generated token to Parameter Store."""
    request = {
        "Name": PARAMETER_NAME,
        "Description": "Bearer token the IdP SCIM application presents to the SCIM API.",
        "Value": generate_token(length),
        "Type": "SecureString",
        "Overwrite": True,
    }
    if KMS_KEY_ID:
        request["KeyId"] = KMS_KEY_ID
    SSM_CLIENT.put_parameter(**request)
    LOGGER.info("Wrote a %s-character API token to %s", length, PARAMETER_NAME)


def apply(request_type, properties):
    """Create, retain or delete the token for the given CloudFormation request."""
    if request_type == "Create":
        put_token(requested_length(properties))

    elif request_type == "Update":
        # The token is deliberately not rotated on stack update: doing so would
        # silently invalidate the value already configured in the identity
        # provider. Rotate by deleting the parameter and updating the stack, then
        # re-entering the new token in the IdP.
        if parameter_exists():
            LOGGER.info(
                "Retaining the existing API token in %s. Delete the parameter and "
                "update the stack to rotate it.",
                PARAMETER_NAME,
            )
        else:
            LOGGER.info("No existing token found in %s; generating one.", PARAMETER_NAME)
            put_token(requested_length(properties))

    elif request_type == "Delete":
        try:
            SSM_CLIENT.delete_parameter(Name=PARAMETER_NAME)
            LOGGER.info("Deleted %s", PARAMETER_NAME)
        except ClientError as error:
            # A missing parameter means the delete has already happened; that must
            # not block stack deletion.
            if error.response["Error"]["Code"] != "ParameterNotFound":
                raise
            LOGGER.info("%s was already absent", PARAMETER_NAME)

    else:
        raise ValueError(f"Unsupported RequestType {request_type}")

    return {"ParameterName": PARAMETER_NAME}
