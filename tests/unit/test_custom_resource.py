"""Tests for the API token custom resource.

The token logic lives in ``api_token`` and is shared. The two entry points around
it are not interchangeable, and both are exercised here:

* the CDK handler runs behind ``custom_resources.Provider`` and returns a result;
* the CloudFormation handler backs a raw ``AWS::CloudFormation::CustomResource``
  and must post its own result to ``ResponseURL``.

Getting that backwards leaves a CloudFormation stack in CREATE_IN_PROGRESS until
the resource times out, which is how it was originally caught.
"""

import json
import logging
import string

import api_token
import custom_resource
import custom_resource_lambda
import pytest
from botocore.exceptions import ClientError


class FakeSsm:
    """Minimal in-memory Parameter Store.

    Records the parameter Type as the real service does. Without it a plaintext
    parameter left by the previous release is indistinguishable from a current
    SecureString, which is the distinction the upgrade path turns on.
    """

    def __init__(self, existing=None):
        # name -> (value, type)
        self.parameters = {
            name: (value, "SecureString") for name, value in (existing or {}).items()
        }
        self.puts = []
        self.deletes = []

    def seed_legacy(self, name, value):
        """Store a parameter the way release 1.0.0 did: plaintext StringList."""
        self.parameters[name] = (value, "StringList")

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self.parameters:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "missing"}},
                "GetParameter",
            )
        value, param_type = self.parameters[Name]
        return {"Parameter": {"Name": Name, "Value": value, "Type": param_type}}

    def put_parameter(self, Name, Value, Type, KeyId=None, Overwrite=False, Description=None):
        self.puts.append(
            {"Name": Name, "Value": Value, "Type": Type, "KeyId": KeyId, "Overwrite": Overwrite}
        )
        self.parameters[Name] = (Value, Type)
        return {"Version": len(self.puts)}

    def delete_parameter(self, Name):
        if Name not in self.parameters:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "missing"}},
                "DeleteParameter",
            )
        self.deletes.append(Name)
        del self.parameters[Name]
        return {}


class FakeHttp:
    """Captures the PUT the CloudFormation entry point makes to ResponseURL."""

    def __init__(self):
        self.requests = []
        self.status = 200

    def request(self, method, url, body=None, headers=None):
        self.requests.append(
            {"method": method, "url": url, "body": json.loads(body), "headers": headers}
        )
        return type("Response", (), {"status": self.status})()


class FakeContext:
    log_stream_name = "2026/09/01/[$LATEST]abcdef"


@pytest.fixture
def ssm(monkeypatch):
    fake = FakeSsm()
    monkeypatch.setattr(api_token, "SSM_CLIENT", fake)
    return fake


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(custom_resource_lambda, "HTTP", fake)
    return fake


def event(request_type, api_length=32, physical_id=None):
    payload = {
        "RequestType": request_type,
        "ResourceProperties": {"ApiLength": api_length},
        "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/test/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "ApiKeyCustomResource",
        "ResponseURL": "https://cloudformation-custom-resource-responses.example/signed",
    }
    if physical_id:
        payload["PhysicalResourceId"] = physical_id
    return payload


class TestTokenGeneration:
    def test_token_has_the_requested_length(self):
        assert len(api_token.generate_token(32)) == 32
        assert len(api_token.generate_token(64)) == 64

    def test_token_is_alphanumeric(self):
        token = api_token.generate_token(128)
        assert set(token) <= set(string.ascii_letters + string.digits)

    def test_length_can_exceed_the_alphabet_size(self):
        # random.sample sampled without replacement, so it raised above 36 and
        # never repeated a character.
        assert len(api_token.generate_token(200)) == 200

    def test_characters_repeat_across_a_long_token(self):
        token = api_token.generate_token(200)
        assert len(set(token)) < len(token)

    def test_tokens_are_not_repeated(self):
        tokens = {api_token.generate_token(32) for _ in range(50)}
        assert len(tokens) == 50


class TestApply:
    def test_create_writes_a_securestring_with_the_configured_key(self, ssm):
        api_token.apply("Create", {"ApiLength": 32})
        assert len(ssm.puts) == 1
        put = ssm.puts[0]
        assert put["Type"] == "SecureString"
        assert put["Name"] == api_token.PARAMETER_NAME
        assert len(put["Value"]) == 32

    def test_create_honours_a_longer_length(self, ssm):
        api_token.apply("Create", {"ApiLength": 64})
        assert len(ssm.puts[0]["Value"]) == 64

    def test_create_accepts_a_stringified_length(self, ssm):
        api_token.apply("Create", {"ApiLength": "48"})
        assert len(ssm.puts[0]["Value"]) == 48

    @pytest.mark.parametrize("length", [8, 31, 257, 1000])
    def test_rejects_a_length_outside_the_supported_range(self, ssm, length):
        with pytest.raises(ValueError, match="ApiLength must be between"):
            api_token.apply("Create", {"ApiLength": length})
        assert ssm.puts == []

    def test_rejects_a_non_numeric_length(self, ssm):
        with pytest.raises(ValueError, match="must be an integer"):
            api_token.apply("Create", {"ApiLength": "thirty-two"})

    def test_update_retains_an_existing_securestring(self, ssm):
        ssm.parameters[api_token.PARAMETER_NAME] = ("already-configured", "SecureString")
        api_token.apply("Update", {"ApiLength": 32})
        # Rotating here would silently invalidate the value already entered in the
        # identity provider.
        assert ssm.puts == []
        assert ssm.parameters[api_token.PARAMETER_NAME][0] == "already-configured"

    def test_update_rewrites_a_plaintext_parameter_from_the_previous_release(self, ssm):
        # Release 1.0.0 stored the token as a plaintext StringList. Retaining that
        # would leave the bearer token in clear text.
        ssm.seed_legacy(api_token.PARAMETER_NAME, "plaintext-from-1.0.0")
        api_token.apply("Update", {"ApiLength": 32})
        assert len(ssm.puts) == 1
        assert ssm.puts[0]["Type"] == "SecureString"
        value, param_type = ssm.parameters[api_token.PARAMETER_NAME]
        assert param_type == "SecureString"
        assert value != "plaintext-from-1.0.0"

    def test_create_refuses_when_the_parameter_already_exists(self, ssm):
        # The name is not stack-scoped, so a second stack would otherwise overwrite
        # the first stack's live token and start returning 401s to its IdP.
        ssm.parameters[api_token.PARAMETER_NAME] = ("in-use-by-another-stack", "SecureString")
        with pytest.raises(ValueError, match="already exists"):
            api_token.apply("Create", {"ApiLength": 32})
        assert ssm.puts == []
        assert ssm.parameters[api_token.PARAMETER_NAME][0] == "in-use-by-another-stack"

    def test_create_refuses_over_a_legacy_plaintext_parameter_too(self, ssm):
        ssm.seed_legacy(api_token.PARAMETER_NAME, "plaintext-from-1.0.0")
        with pytest.raises(ValueError, match="already exists"):
            api_token.apply("Create", {"ApiLength": 32})
        assert ssm.puts == []

    def test_update_regenerates_a_missing_token(self, ssm):
        api_token.apply("Update", {"ApiLength": 32})
        assert len(ssm.puts) == 1

    def test_delete_removes_the_parameter(self, ssm):
        ssm.parameters[api_token.PARAMETER_NAME] = ("token", "SecureString")
        api_token.apply("Delete", {})
        assert ssm.deletes == [api_token.PARAMETER_NAME]

    def test_delete_tolerates_an_absent_parameter(self, ssm):
        api_token.apply("Delete", {})
        assert ssm.deletes == []

    def test_delete_propagates_other_errors(self, ssm, monkeypatch):
        def boom(Name):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "DeleteParameter",
            )

        monkeypatch.setattr(ssm, "delete_parameter", boom)
        with pytest.raises(ClientError):
            api_token.apply("Delete", {})

    def test_unknown_request_type_is_rejected(self, ssm):
        with pytest.raises(ValueError, match="Unsupported RequestType"):
            api_token.apply("Frobnicate", {})


class TestCdkEntryPoint:
    def test_returns_a_result_for_the_provider_framework(self, ssm):
        result = custom_resource.lambda_handler(event("Create"), FakeContext())
        assert set(result) == {"PhysicalResourceId", "Data"}
        assert result["Data"]["ParameterName"] == api_token.PARAMETER_NAME
        assert len(ssm.puts) == 1

    def test_preserves_the_physical_resource_id(self, ssm):
        result = custom_resource.lambda_handler(
            event("Update", physical_id="existing-id"), FakeContext()
        )
        assert result["PhysicalResourceId"] == "existing-id"

    def test_does_not_post_to_response_url(self, ssm, monkeypatch):
        # The framework owns the response; posting here would race it.
        assert not hasattr(custom_resource, "HTTP")


class TestCloudFormationEntryPoint:
    def test_posts_success_to_response_url(self, ssm, http):
        custom_resource_lambda.lambda_handler(event("Create"), FakeContext())
        assert len(http.requests) == 1
        request = http.requests[0]
        assert request["method"] == "PUT"
        assert request["url"].startswith("https://")
        assert request["body"]["Status"] == "SUCCESS"
        assert request["body"]["LogicalResourceId"] == "ApiKeyCustomResource"
        assert request["body"]["Data"]["ParameterName"] == api_token.PARAMETER_NAME

    def test_writes_the_token(self, ssm, http):
        custom_resource_lambda.lambda_handler(event("Create"), FakeContext())
        assert len(ssm.puts) == 1
        assert ssm.puts[0]["Type"] == "SecureString"

    def test_reports_failure_rather_than_hanging_the_stack(self, ssm, http):
        # Without a response CloudFormation waits out the resource timeout, so a
        # failure has to be posted too.
        with pytest.raises(ValueError):
            custom_resource_lambda.lambda_handler(event("Create", api_length=1), FakeContext())
        assert len(http.requests) == 1
        assert http.requests[0]["body"]["Status"] == "FAILED"
        assert "ApiLength" in http.requests[0]["body"]["Reason"]

    def test_delete_posts_success(self, ssm, http):
        ssm.parameters[api_token.PARAMETER_NAME] = ("token", "SecureString")
        custom_resource_lambda.lambda_handler(
            event("Delete", physical_id=api_token.PARAMETER_NAME), FakeContext()
        )
        assert http.requests[0]["body"]["Status"] == "SUCCESS"
        assert ssm.deletes == [api_token.PARAMETER_NAME]

    def test_response_body_carries_the_required_correlation_fields(self, ssm, http):
        custom_resource_lambda.lambda_handler(event("Create"), FakeContext())
        body = http.requests[0]["body"]
        for field in ("Status", "PhysicalResourceId", "StackId", "RequestId", "LogicalResourceId"):
            assert field in body, f"CloudFormation requires {field} in the response"

    def test_the_signed_url_is_not_logged(self, ssm, http, caplog):
        with caplog.at_level(logging.INFO):
            custom_resource_lambda.lambda_handler(event("Create"), FakeContext())
        # The ResponseURL is a capability: anyone holding it can forge a response.
        assert "signed" not in caplog.text
