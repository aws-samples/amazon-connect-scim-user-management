"""Tests for the API Gateway token authorizer."""

import logging
import secrets
import string
import time

import lambda_authorizer
import pytest
from botocore.exceptions import ClientError


def _token(length=32):
    """Build a throwaway token for the tests.

    Generated rather than written as a literal so the file contains nothing that
    reads as a real credential to a secret scanner or to someone skimming it.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


VALID_TOKEN = _token()
ROTATED_TOKEN = _token()
METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123def4/dev/POST/Users"


class FakeSsm:
    """Records GetParameter calls and returns a configurable token."""

    def __init__(self, value=VALID_TOKEN):
        self.value = value
        self.calls = []

    def get_parameter(self, Name, WithDecryption=False):
        self.calls.append({"Name": Name, "WithDecryption": WithDecryption})
        if self.value is None:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "missing"}},
                "GetParameter",
            )
        return {"Parameter": {"Name": Name, "Value": self.value}}


@pytest.fixture
def ssm(monkeypatch):
    fake = FakeSsm()
    monkeypatch.setattr(lambda_authorizer, "SSM_CLIENT", fake)
    # Start every test with a cold cache.
    monkeypatch.setattr(lambda_authorizer, "_cache", {"token": None, "expires_at": 0.0})
    return fake


def authorize(token, method_arn=METHOD_ARN):
    return lambda_authorizer.lambda_handler(
        {"authorizationToken": token, "methodArn": method_arn, "type": "TOKEN"}, None
    )


class TestAuthorization:
    def test_valid_bare_token_is_allowed(self, ssm):
        response = authorize(VALID_TOKEN)
        statement = response["policyDocument"]["Statement"][0]
        assert statement["Effect"] == "Allow"
        assert statement["Action"] == "execute-api:Invoke"

    def test_valid_bearer_prefixed_token_is_allowed(self, ssm):
        assert (
            authorize(f"Bearer {VALID_TOKEN}")["policyDocument"]["Statement"][0]["Effect"]
            == "Allow"
        )

    def test_bearer_prefix_is_case_insensitive(self, ssm):
        assert (
            authorize(f"bearer {VALID_TOKEN}")["policyDocument"]["Statement"][0]["Effect"]
            == "Allow"
        )

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "wrong-token",
            VALID_TOKEN[:-1],
            VALID_TOKEN + "x",
            "Bearer ",
            # Non-ASCII: secrets.compare_digest rejects str outside ASCII, so this
            # raised TypeError and surfaced as a 500 instead of a 401.
            "tökén",
            "Bearer \u5bc6\u7801",
        ],
    )
    def test_invalid_token_is_unauthorized(self, ssm, token):
        with pytest.raises(Exception, match="Unauthorized"):
            authorize(token)

    def test_missing_token_key_is_unauthorized(self, ssm):
        with pytest.raises(Exception, match="Unauthorized"):
            lambda_authorizer.lambda_handler({"methodArn": METHOD_ARN}, None)

    def test_parameter_is_read_with_decryption(self, ssm):
        authorize(VALID_TOKEN)
        assert ssm.calls[0]["WithDecryption"] is True

    def test_the_token_is_never_logged(self, ssm, caplog):
        # The published version logged it outright:
        #   LOGGER.info("Client token: " + event['authorizationToken'])
        # Nothing guarded that here, while the SCIM handler and custom resource
        # both had the equivalent assertion.
        with caplog.at_level(logging.INFO):
            authorize(f"Bearer {VALID_TOKEN}")
        assert VALID_TOKEN not in caplog.text
        assert "Client token" not in caplog.text

    def test_a_rejected_token_is_not_logged_either(self, ssm, caplog):
        with caplog.at_level(logging.INFO), pytest.raises(Exception, match="Unauthorized"):
            authorize("Bearer some-wrong-but-still-secret-value")
        assert "some-wrong-but-still-secret-value" not in caplog.text


class TestPolicyScope:
    def test_resource_is_pinned_to_the_calling_api_and_stage(self, ssm):
        response = authorize(VALID_TOKEN)
        resources = response["policyDocument"]["Statement"][0]["Resource"]
        assert resources == ["arn:aws:execute-api:us-east-1:123456789012:abc123def4/dev/*/*"]

    def test_partition_is_taken_from_the_method_arn(self, ssm):
        response = authorize(
            VALID_TOKEN,
            "arn:aws-us-gov:execute-api:us-gov-west-1:123456789012:xyz/prod/GET/Users",
        )
        assert response["policyDocument"]["Statement"][0]["Resource"] == [
            "arn:aws-us-gov:execute-api:us-gov-west-1:123456789012:xyz/prod/*/*"
        ]

    def test_malformed_method_arn_is_rejected(self, ssm):
        with pytest.raises(ValueError, match="Unrecognised methodArn"):
            authorize(VALID_TOKEN, "not-an-arn")


class TestTokenCache:
    def test_token_is_cached_across_invocations(self, ssm):
        authorize(VALID_TOKEN)
        authorize(VALID_TOKEN)
        authorize(VALID_TOKEN)
        assert len(ssm.calls) == 1

    def test_cache_expires(self, ssm, monkeypatch):
        authorize(VALID_TOKEN)
        assert len(ssm.calls) == 1
        # Jump past the TTL.
        real_monotonic = time.monotonic
        monkeypatch.setattr(
            lambda_authorizer.time,
            "monotonic",
            lambda: real_monotonic() + lambda_authorizer.CACHE_TTL_SECONDS + 1,
        )
        authorize(VALID_TOKEN)
        assert len(ssm.calls) == 2

    def test_a_rotated_token_is_picked_up_after_expiry(self, ssm, monkeypatch):
        authorize(VALID_TOKEN)
        ssm.value = ROTATED_TOKEN
        # Still cached, so the old token keeps working briefly.
        authorize(VALID_TOKEN)
        real_monotonic = time.monotonic
        monkeypatch.setattr(
            lambda_authorizer.time,
            "monotonic",
            lambda: real_monotonic() + lambda_authorizer.CACHE_TTL_SECONDS + 1,
        )
        with pytest.raises(Exception, match="Unauthorized"):
            authorize(VALID_TOKEN)
        assert authorize(ROTATED_TOKEN)["policyDocument"]["Statement"][0]["Effect"] == "Allow"


class TestMissingParameter:
    def test_absent_parameter_surfaces_rather_than_allowing(self, monkeypatch):
        # A missing token must never fail open.
        fake = FakeSsm(value=None)
        monkeypatch.setattr(lambda_authorizer, "SSM_CLIENT", fake)
        monkeypatch.setattr(lambda_authorizer, "_cache", {"token": None, "expires_at": 0.0})
        with pytest.raises(ClientError):
            authorize(VALID_TOKEN)
