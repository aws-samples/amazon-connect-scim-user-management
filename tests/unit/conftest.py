"""Shared fixtures for the SCIM handler unit tests.

The Amazon Connect client is replaced with an in-memory fake rather than ordered
``botocore`` stubs. The behaviour under test is mostly about pagination and
membership bookkeeping across several calls, which ordered stubs express poorly
and which a fake can assert on directly.
"""

import copy
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "cdk_source" / "lambdas"
for source_dir in ("user_management", "lambda_authorizer", "custom_resource"):
    sys.path.insert(0, str(LAMBDA_ROOT / source_dir))
# The CloudFormation custom-resource entry point speaks a different response
# protocol than the CDK one, so both are imported and tested.
sys.path.insert(0, str(LAMBDA_ROOT.parents[1] / "CloudFormation" / "lambdas" / "custom_resource"))

# The handler modules read their configuration at import time.
os.environ.setdefault("INSTANCE_ID", "11111111-2222-3333-4444-555555555555")
os.environ.setdefault("DEFAULT_ROUTING_PROFILE", "Basic Routing Profile")
os.environ.setdefault("DEFAULT_SECURITY_PROFILE", "Agent")
os.environ.setdefault("PARAMETER_NAME", "/connect/scim-integration/api-token")
os.environ.setdefault("KMS_KEY_ID", "alias/connect-scim-api-token")

AGENT_PROFILE = "sp-agent-0001"
SUPERVISOR_PROFILE = "sp-supervisor-002"
QA_PROFILE = "sp-qualityanalyst-3"
ROUTING_PROFILE = "rp-basic-0001"

FIXED_TIME = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": f"{operation} failed"}}, operation)


class _Paginator:
    """Minimal stand-in for a botocore paginator over a fake list operation."""

    def __init__(self, operation, page_key):
        self._operation = operation
        self._page_key = page_key

    def paginate(self, **kwargs):
        kwargs.pop("PaginationConfig", None)
        next_token = None
        while True:
            request = dict(kwargs)
            if next_token:
                request["NextToken"] = next_token
            page = self._operation(**request)
            yield page
            next_token = page.get("NextToken")
            if not next_token:
                return


class FakeConnect:
    """In-memory stand-in for the subset of the Amazon Connect API used here."""

    #: Deliberately small so every list operation pages, exercising pagination.
    page_size = 2

    def __init__(self):
        self.instance_id = os.environ["INSTANCE_ID"]
        self.security_profiles = [
            {"Id": AGENT_PROFILE, "Name": "Agent", "LastModifiedTime": FIXED_TIME},
            {
                "Id": SUPERVISOR_PROFILE,
                "Name": "Supervisor",
                "LastModifiedTime": FIXED_TIME,
            },
            {"Id": QA_PROFILE, "Name": "QualityAnalyst", "LastModifiedTime": FIXED_TIME},
        ]
        self.routing_profiles = [
            {
                "Id": ROUTING_PROFILE,
                "Name": "Basic Routing Profile",
                "LastModifiedTime": FIXED_TIME,
            },
            {
                "Id": "rp-priority-002",
                "Name": "Priority Routing",
                "LastModifiedTime": FIXED_TIME,
            },
        ]
        self.users = {}
        self.calls = []
        self._next_user = 0
        # SearchUsers is served from a search index that lags writes by a few
        # seconds in the real service. Modelling that here is deliberate: with an
        # instantly-consistent fake, a handler that reads membership back through
        # SearchUsers straight after a write looks correct in tests and reports
        # empty membership in production.
        self._search_index = {}

    # -- test helpers ----------------------------------------------------

    def add_user(self, username, security_profile_ids=None, user_id=None):
        self._next_user += 1
        user_id = user_id or f"user-{self._next_user:04d}"
        self.users[user_id] = {
            "Id": user_id,
            "Arn": (
                "arn:aws:connect:us-east-1:123456789012:instance/"
                f"{self.instance_id}/agent/{user_id}"
            ),
            "Username": username,
            "IdentityInfo": {"FirstName": "Test", "LastName": "User"},
            "SecurityProfileIds": list(security_profile_ids or [AGENT_PROFILE]),
            "RoutingProfileId": ROUTING_PROFILE,
            "LastModifiedTime": FIXED_TIME,
        }
        # Test setup represents state that has long since been indexed.
        self.refresh_search_index()
        return user_id

    def refresh_search_index(self):
        """Bring the simulated search index up to date with current state."""
        self._search_index = copy.deepcopy(self.users)

    def profile_ids_of(self, user_id):
        return list(self.users[user_id]["SecurityProfileIds"])

    def call_names(self):
        return [name for name, _ in self.calls]

    def count(self, name):
        return self.call_names().count(name)

    # -- paging ----------------------------------------------------------

    def _page(self, items, next_token, key):
        start = int(next_token) if next_token else 0
        window = items[start : start + self.page_size]
        result = {key: window}
        if start + self.page_size < len(items):
            result["NextToken"] = str(start + self.page_size)
        return result

    def _check_instance(self, instance_id):
        if instance_id != self.instance_id:
            raise _client_error("ResourceNotFoundException", "Operation")

    # -- API surface -----------------------------------------------------

    def get_paginator(self, name):
        operations = {
            "list_users": (self.list_users, "UserSummaryList"),
            "list_security_profiles": (
                self.list_security_profiles,
                "SecurityProfileSummaryList",
            ),
            "list_routing_profiles": (
                self.list_routing_profiles,
                "RoutingProfileSummaryList",
            ),
        }
        operation, key = operations[name]
        return _Paginator(operation, key)

    def list_users(self, InstanceId, MaxResults=None, NextToken=None):
        self.calls.append(("list_users", {"NextToken": NextToken}))
        self._check_instance(InstanceId)
        summaries = [
            {
                "Id": user["Id"],
                "Arn": user["Arn"],
                "Username": user["Username"],
                "LastModifiedTime": user["LastModifiedTime"],
            }
            for user in self.users.values()
        ]
        return self._page(summaries, NextToken, "UserSummaryList")

    def describe_user(self, UserId, InstanceId):
        self.calls.append(("describe_user", {"UserId": UserId}))
        self._check_instance(InstanceId)
        if UserId not in self.users:
            raise _client_error("ResourceNotFoundException", "DescribeUser")
        return {"User": copy.deepcopy(self.users[UserId])}

    def create_user(
        self,
        Username,
        IdentityInfo,
        PhoneConfig,
        SecurityProfileIds,
        RoutingProfileId,
        InstanceId,
    ):
        self.calls.append(("create_user", {"Username": Username}))
        self._check_instance(InstanceId)
        if any(user["Username"] == Username for user in self.users.values()):
            raise _client_error("DuplicateResourceException", "CreateUser")
        if not SecurityProfileIds:
            raise _client_error("InvalidParameterException", "CreateUser")
        user_id = self.add_user(Username, SecurityProfileIds)
        self.users[user_id]["IdentityInfo"] = IdentityInfo
        self.users[user_id]["RoutingProfileId"] = RoutingProfileId
        return {"UserId": user_id, "UserArn": self.users[user_id]["Arn"]}

    def delete_user(self, InstanceId, UserId):
        self.calls.append(("delete_user", {"UserId": UserId}))
        self._check_instance(InstanceId)
        if UserId not in self.users:
            raise _client_error("ResourceNotFoundException", "DeleteUser")
        del self.users[UserId]
        return {}

    def update_user_security_profiles(self, SecurityProfileIds, UserId, InstanceId):
        self.calls.append(
            (
                "update_user_security_profiles",
                {"UserId": UserId, "Ids": list(SecurityProfileIds)},
            )
        )
        self._check_instance(InstanceId)
        if UserId not in self.users:
            raise _client_error("ResourceNotFoundException", "UpdateUserSecurityProfiles")
        # Amazon Connect rejects an empty security profile list.
        if not SecurityProfileIds:
            raise _client_error("InvalidParameterException", "UpdateUserSecurityProfiles")
        self.users[UserId]["SecurityProfileIds"] = list(SecurityProfileIds)
        return {}

    def list_security_profiles(self, InstanceId, MaxResults=None, NextToken=None):
        self.calls.append(("list_security_profiles", {"NextToken": NextToken}))
        self._check_instance(InstanceId)
        return self._page(self.security_profiles, NextToken, "SecurityProfileSummaryList")

    def describe_security_profile(self, SecurityProfileId, InstanceId):
        self.calls.append(("describe_security_profile", {"Id": SecurityProfileId}))
        self._check_instance(InstanceId)
        for profile in self.security_profiles:
            if profile["Id"] == SecurityProfileId:
                return {
                    "SecurityProfile": {
                        "Id": profile["Id"],
                        "SecurityProfileName": profile["Name"],
                        "LastModifiedTime": profile["LastModifiedTime"],
                    }
                }
        raise _client_error("ResourceNotFoundException", "DescribeSecurityProfile")

    def list_routing_profiles(self, InstanceId, MaxResults=None, NextToken=None):
        self.calls.append(("list_routing_profiles", {"NextToken": NextToken}))
        self._check_instance(InstanceId)
        return self._page(self.routing_profiles, NextToken, "RoutingProfileSummaryList")

    def search_users(self, InstanceId, MaxResults=None, NextToken=None, SearchCriteria=None):
        self.calls.append(("search_users", {"SearchCriteria": SearchCriteria}))
        self._check_instance(InstanceId)
        condition = (SearchCriteria or {}).get("StringCondition") or {}
        field = condition.get("FieldName")
        value = condition.get("Value")
        matched = []
        # Reads the lagging index, not current state.
        for user in self._search_index.values():
            if field == "SecurityProfileId":
                if value in user["SecurityProfileIds"]:
                    matched.append(copy.deepcopy(user))
            elif field == "Username":
                if user["Username"] == value:
                    matched.append(copy.deepcopy(user))
            else:
                matched.append(copy.deepcopy(user))
        return self._page(matched, NextToken, "Users")


@pytest.fixture
def connect():
    """Provide a fresh fake Connect client wired into the directory adapter."""
    import connect_directory

    fake = FakeConnect()
    previous = connect_directory._CLIENT
    connect_directory._CLIENT = fake
    yield fake
    connect_directory._CLIENT = previous


def api_event(method, path, body=None, query=None):
    """Build an API Gateway REST proxy event for the '{Users+}' resource."""
    return {
        "httpMethod": method,
        "pathParameters": {"Users": path},
        "queryStringParameters": query,
        "body": body,
        "headers": {"Content-Type": "application/scim+json"},
    }
