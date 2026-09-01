"""Tests for the API token custom resource."""

import string

import custom_resource
import pytest
from botocore.exceptions import ClientError


class FakeSsm:
    """Minimal in-memory Parameter Store."""

    def __init__(self, existing=None):
        self.parameters = dict(existing or {})
        self.puts = []
        self.deletes = []

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self.parameters:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "missing"}},
                "GetParameter",
            )
        return {"Parameter": {"Name": Name, "Value": self.parameters[Name]}}

    def put_parameter(self, Name, Value, Type, KeyId=None, Overwrite=False, Description=None):
        self.puts.append(
            {"Name": Name, "Value": Value, "Type": Type, "KeyId": KeyId, "Overwrite": Overwrite}
        )
        self.parameters[Name] = Value
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


@pytest.fixture
def ssm(monkeypatch):
    fake = FakeSsm()
    monkeypatch.setattr(custom_resource, "SSM_CLIENT", fake)
    return fake


def invoke(request_type, api_length=32, physical_id=None):
    event = {
        "RequestType": request_type,
        "ResourceProperties": {"ApiLength": api_length},
        "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/test/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "api_key_custom_action",
    }
    if physical_id:
        event["PhysicalResourceId"] = physical_id
    return custom_resource.lambda_handler(event, None)


class TestTokenGeneration:
    def test_token_has_the_requested_length(self):
        assert len(custom_resource.generate_token(32)) == 32
        assert len(custom_resource.generate_token(64)) == 64

    def test_token_is_alphanumeric(self):
        token = custom_resource.generate_token(128)
        assert set(token) <= set(string.ascii_letters + string.digits)

    def test_length_can_exceed_the_alphabet_size(self):
        # random.sample sampled without replacement, so it raised above 36 and
        # never repeated a character.
        assert len(custom_resource.generate_token(200)) == 200

    def test_characters_repeat_across_a_long_token(self):
        token = custom_resource.generate_token(200)
        assert len(set(token)) < len(token)

    def test_tokens_are_not_repeated(self):
        tokens = {custom_resource.generate_token(32) for _ in range(50)}
        assert len(tokens) == 50


class TestCreate:
    def test_writes_a_securestring_with_the_configured_key(self, ssm):
        result = invoke("Create")
        assert len(ssm.puts) == 1
        put = ssm.puts[0]
        assert put["Type"] == "SecureString"
        assert put["KeyId"] == custom_resource.KMS_KEY_ID
        assert put["Name"] == custom_resource.PARAMETER_NAME
        assert len(put["Value"]) == 32
        assert result["PhysicalResourceId"] == custom_resource.PARAMETER_NAME

    def test_honours_a_longer_requested_length(self, ssm):
        invoke("Create", api_length=64)
        assert len(ssm.puts[0]["Value"]) == 64

    def test_accepts_a_stringified_length(self, ssm):
        invoke("Create", api_length="48")
        assert len(ssm.puts[0]["Value"]) == 48

    @pytest.mark.parametrize("length", [8, 31, 257, 1000])
    def test_rejects_a_length_outside_the_supported_range(self, ssm, length):
        with pytest.raises(ValueError, match="ApiLength must be between"):
            invoke("Create", api_length=length)
        assert ssm.puts == []

    def test_rejects_a_non_numeric_length(self, ssm):
        with pytest.raises(ValueError, match="must be an integer"):
            invoke("Create", api_length="thirty-two")


class TestUpdate:
    def test_existing_token_is_retained(self, ssm):
        ssm.parameters[custom_resource.PARAMETER_NAME] = "already-configured-token"
        invoke("Update", physical_id=custom_resource.PARAMETER_NAME)
        # Rotating here would silently invalidate the token already entered in
        # the identity provider.
        assert ssm.puts == []
        assert ssm.parameters[custom_resource.PARAMETER_NAME] == "already-configured-token"

    def test_a_missing_token_is_regenerated(self, ssm):
        invoke("Update", physical_id=custom_resource.PARAMETER_NAME)
        assert len(ssm.puts) == 1


class TestDelete:
    def test_deletes_the_parameter(self, ssm):
        ssm.parameters[custom_resource.PARAMETER_NAME] = "token"
        invoke("Delete", physical_id=custom_resource.PARAMETER_NAME)
        assert ssm.deletes == [custom_resource.PARAMETER_NAME]

    def test_an_already_absent_parameter_does_not_block_deletion(self, ssm):
        result = invoke("Delete", physical_id=custom_resource.PARAMETER_NAME)
        assert result["PhysicalResourceId"] == custom_resource.PARAMETER_NAME

    def test_other_errors_still_propagate(self, ssm, monkeypatch):
        def boom(Name):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "DeleteParameter",
            )

        monkeypatch.setattr(ssm, "delete_parameter", boom)
        with pytest.raises(ClientError):
            invoke("Delete", physical_id=custom_resource.PARAMETER_NAME)


class TestProviderProtocol:
    def test_returns_a_result_object_for_the_framework(self, ssm):
        # The CDK Provider framework owns the CloudFormation response protocol;
        # the handler must return a result rather than posting to ResponseURL.
        result = invoke("Create")
        assert set(result) == {"PhysicalResourceId", "Data"}
        assert result["Data"]["ParameterName"] == custom_resource.PARAMETER_NAME

    def test_unknown_request_type_is_rejected(self, ssm):
        with pytest.raises(ValueError, match="Unsupported RequestType"):
            invoke("Frobnicate")
