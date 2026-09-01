"""Tests for the /Users endpoint.

Several cases here are regression tests for defects in the previous handler that
would surface as provisioning failures: username lookups that never matched, a
PATCH that crashed on one of the two shapes Okta sends, and a reactivation path
that returned no response at all.
"""

import json
import logging

import azure
import handler_core
import okta
import pytest
from conftest import AGENT_PROFILE, QA_PROFILE, SUPERVISOR_PROFILE, api_event


def call(method, path, body=None, query=None, handler=okta):
    response = handler.lambda_handler(api_event(method, path, body, query), None)
    parsed = json.loads(response["body"]) if response["body"] else None
    return response["statusCode"], parsed


def patch_body(operations):
    return json.dumps(
        {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": operations}
    )


class TestUserLookup:
    def test_finds_a_user_by_username_filter(self, connect):
        # Regression: the old handler pulled the username out of the filter and
        # then compared it against the Connect user *Id*, so this never matched
        # and every sync tried to re-create the user.
        user_id = connect.add_user("alice@example.com", [AGENT_PROFILE])
        status, body = call("GET", "Users", query={"filter": 'userName eq "alice@example.com"'})
        assert status == 200
        assert body["totalResults"] == 1
        assert body["Resources"][0]["id"] == user_id
        assert body["Resources"][0]["userName"] == "alice@example.com"

    def test_finds_a_user_by_external_id_filter(self, connect):
        user_id = connect.add_user("bob@example.com", [AGENT_PROFILE])
        status, body = call("GET", "Users", query={"filter": 'externalId eq "bob@example.com"'})
        assert status == 200
        assert body["Resources"][0]["id"] == user_id

    def test_username_match_is_case_insensitive(self, connect):
        user_id = connect.add_user("Alice@Example.com", [AGENT_PROFILE])
        _, body = call("GET", "Users", query={"filter": 'userName eq "alice@example.com"'})
        assert body["Resources"][0]["id"] == user_id

    def test_missing_user_is_an_empty_list_not_an_error(self, connect):
        # Okta probes for a user before creating it and expects totalResults 0.
        status, body = call("GET", "Users", query={"filter": 'userName eq "nobody@example.com"'})
        assert status == 200
        assert body["totalResults"] == 0
        assert body["Resources"] == []

    def test_get_by_path_id(self, connect):
        user_id = connect.add_user("alice@example.com", [AGENT_PROFILE, QA_PROFILE])
        status, body = call("GET", f"Users/{user_id}")
        assert status == 200
        assert body["id"] == user_id
        assert {item["value"] for item in body["entitlements"]} == {"Agent", "QualityAnalyst"}

    def test_get_by_path_id_unknown_is_404(self, connect):
        status, _ = call("GET", "Users/ghost")
        assert status == 404

    def test_get_users_with_no_query_string_does_not_crash(self, connect):
        # The old handler dereferenced queryStringParameters['filter']
        # unconditionally and raised a TypeError when there was no query string.
        connect.add_user("alice@example.com", [AGENT_PROFILE])
        status, body = call("GET", "Users")
        assert status == 200
        assert body["totalResults"] == 1

    def test_unsupported_user_filter_is_rejected(self, connect):
        status, body = call("GET", "Users", query={"filter": 'nickName eq "x"'})
        assert status == 400
        assert body["scimType"] == "invalidFilter"


class TestUserPagination:
    def test_index_pagination_reports_real_total(self, connect):
        for index in range(5):
            connect.add_user(f"user{index}@example.com", [AGENT_PROFILE])
        status, body = call("GET", "Users", query={"startIndex": "1", "count": "2"})
        assert status == 200
        assert body["totalResults"] == 5
        assert body["startIndex"] == 1
        assert len(body["Resources"]) == 2

    def test_index_pagination_second_window(self, connect):
        ids = [connect.add_user(f"u{i}@example.com", [AGENT_PROFILE]) for i in range(5)]
        _, first = call("GET", "Users", query={"startIndex": "1", "count": "2"})
        _, second = call("GET", "Users", query={"startIndex": "3", "count": "2"})
        first_ids = [resource["id"] for resource in first["Resources"]]
        second_ids = [resource["id"] for resource in second["Resources"]]
        assert not set(first_ids) & set(second_ids)
        assert set(first_ids) | set(second_ids) <= set(ids)


class TestCreateUser:
    def test_creates_a_user_with_entitlements_and_role(self, connect):
        body = json.dumps(
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "new@example.com",
                "name": {"givenName": "New", "familyName": "User"},
                "entitlements": ["Agent", "QualityAnalyst"],
                "roles": ["Priority Routing"],
            }
        )
        status, response = call("POST", "Users", body)
        assert status == 201
        created = connect.users[response["id"]]
        assert created["Username"] == "new@example.com"
        assert set(created["SecurityProfileIds"]) == {AGENT_PROFILE, QA_PROFILE}
        assert created["RoutingProfileId"] == "rp-priority-002"

    def test_accepts_complex_valued_entitlements(self, connect):
        # Okta can send either plain strings or {"value": ...} objects.
        body = json.dumps(
            {
                "userName": "new@example.com",
                "name": {"givenName": "New", "familyName": "User"},
                "entitlements": [{"value": "Supervisor"}],
            }
        )
        status, response = call("POST", "Users", body)
        assert status == 201
        assert connect.users[response["id"]]["SecurityProfileIds"] == [SUPERVISOR_PROFILE]

    def test_falls_back_to_the_default_security_profile(self, connect):
        body = json.dumps(
            {"userName": "new@example.com", "name": {"givenName": "N", "familyName": "U"}}
        )
        status, response = call("POST", "Users", body)
        assert status == 201
        assert connect.users[response["id"]]["SecurityProfileIds"] == [AGENT_PROFILE]

    def test_falls_back_to_the_default_routing_profile(self, connect):
        body = json.dumps(
            {"userName": "new@example.com", "name": {"givenName": "N", "familyName": "U"}}
        )
        _, response = call("POST", "Users", body)
        assert connect.users[response["id"]]["RoutingProfileId"] == "rp-basic-0001"

    def test_azure_ignores_roles_and_uses_the_default_routing_profile(self, connect):
        body = json.dumps(
            {
                "userName": "new@example.com",
                "name": {"givenName": "N", "familyName": "U"},
                "roles": ["Priority Routing"],
            }
        )
        _, response = call("POST", "Users", body, handler=azure)
        assert connect.users[response["id"]]["RoutingProfileId"] == "rp-basic-0001"

    def test_duplicate_user_is_a_409(self, connect):
        connect.add_user("dupe@example.com", [AGENT_PROFILE])
        body = json.dumps(
            {"userName": "dupe@example.com", "name": {"givenName": "D", "familyName": "U"}}
        )
        status, response = call("POST", "Users", body)
        assert status == 409
        assert response["scimType"] == "uniqueness"

    def test_missing_username_is_a_400(self, connect):
        status, _ = call("POST", "Users", json.dumps({"name": {"givenName": "N"}}))
        assert status == 400

    def test_unknown_entitlement_is_rejected_rather_than_dropped(self, connect):
        # Silently dropping it would give the user fewer permissions than the
        # identity provider asked for, with nothing in the response saying so.
        body = json.dumps(
            {
                "userName": "new@example.com",
                "name": {"givenName": "N", "familyName": "U"},
                "entitlements": ["Agent", "NoSuchProfile"],
            }
        )
        status, response = call("POST", "Users", body)
        assert status == 400
        assert "NoSuchProfile" in response["detail"]
        assert connect.count("create_user") == 0

    def test_unknown_routing_profile_is_rejected(self, connect):
        body = json.dumps(
            {
                "userName": "new@example.com",
                "name": {"givenName": "N", "familyName": "U"},
                "roles": ["No Such Routing"],
            }
        )
        status, _ = call("POST", "Users", body)
        assert status == 400
        assert connect.count("create_user") == 0


class TestDeactivateUser:
    @pytest.mark.parametrize(
        "operations",
        [
            # Okta's documented payload.
            [{"op": "replace", "value": {"active": False}}],
            # The path-based form; the old handler raised TypeError indexing a bool.
            [{"op": "replace", "path": "active", "value": False}],
            # Capitalised op and stringified value, as some connectors send.
            [{"op": "Replace", "path": "active", "value": "False"}],
            [{"op": "Replace", "path": "active", "value": "No"}],
        ],
    )
    def test_deactivation_deletes_the_user(self, connect, operations):
        user_id = connect.add_user("bye@example.com", [AGENT_PROFILE])
        status, _ = call("PATCH", f"Users/{user_id}", patch_body(operations))
        assert status == 204
        assert user_id not in connect.users

    def test_deactivation_via_azure_handler(self, connect):
        # Regression: the old azure handler compared value == 'No' and
        # op == 'Replace', so a standard payload never matched and the user was
        # silently left active.
        user_id = connect.add_user("bye@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Users/{user_id}",
            patch_body([{"op": "replace", "path": "active", "value": False}]),
            handler=azure,
        )
        assert status == 204
        assert user_id not in connect.users

    def test_reactivation_returns_the_user_not_an_empty_response(self, connect):
        # Regression: active=true fell through every branch and returned None,
        # which API Gateway surfaces as a 502.
        user_id = connect.add_user("stay@example.com", [AGENT_PROFILE])
        status, body = call(
            "PATCH",
            f"Users/{user_id}",
            patch_body([{"op": "replace", "path": "active", "value": True}]),
        )
        assert status == 200
        assert body["id"] == user_id
        assert user_id in connect.users

    def test_patch_with_no_active_operation_returns_the_user(self, connect):
        user_id = connect.add_user("stay@example.com", [AGENT_PROFILE])
        status, body = call(
            "PATCH",
            f"Users/{user_id}",
            patch_body([{"op": "replace", "path": "name.givenName", "value": "X"}]),
        )
        assert status == 200
        assert body["id"] == user_id

    def test_delete_method_removes_the_user(self, connect):
        user_id = connect.add_user("bye@example.com", [AGENT_PROFILE])
        status, _ = call("DELETE", f"Users/{user_id}")
        assert status == 204
        assert user_id not in connect.users

    def test_deleting_an_unknown_user_is_404(self, connect):
        status, _ = call("DELETE", "Users/ghost")
        assert status == 404


class TestUpdateUserEntitlements:
    def test_put_replaces_entitlements(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        body = json.dumps(
            {
                "userName": "a@example.com",
                "entitlements": ["Supervisor", "QualityAnalyst"],
            }
        )
        status, response = call("PUT", f"Users/{user_id}", body)
        assert status == 200
        assert set(connect.profile_ids_of(user_id)) == {SUPERVISOR_PROFILE, QA_PROFILE}
        assert {item["value"] for item in response["entitlements"]} == {
            "Supervisor",
            "QualityAnalyst",
        }

    def test_patch_replaces_entitlements(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Users/{user_id}",
            patch_body([{"op": "replace", "path": "entitlements", "value": ["Supervisor"]}]),
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [SUPERVISOR_PROFILE]

    def test_put_with_empty_entitlements_leaves_profiles_alone(self, connect):
        # Connect requires at least one security profile, so an empty set cannot
        # be applied; it must not be sent as an empty list either.
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PUT",
            f"Users/{user_id}",
            json.dumps({"userName": "a@example.com", "entitlements": []}),
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [AGENT_PROFILE]
        assert connect.count("update_user_security_profiles") == 0

    def test_put_resolves_the_user_from_a_filter_when_the_path_has_no_id(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PUT",
            "Users",
            json.dumps({"userName": "a@example.com", "entitlements": ["Supervisor"]}),
            query={"filter": 'userName eq "a@example.com"'},
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [SUPERVISOR_PROFILE]

    def test_update_of_an_unknown_user_is_404(self, connect):
        status, _ = call("PUT", "Users/ghost", json.dumps({"entitlements": ["Agent"]}))
        assert status == 404


class TestRequestLogging:
    """The request log must never carry the bearer token or user attributes.

    API Gateway forwards the Authorization header in event['headers'] and the SCIM
    payload in event['body'], so logging the raw proxy event writes the credential
    and user identity data to CloudWatch on every request.
    """

    def _event_with_secrets(self):
        event = api_event(
            "POST",
            "Users",
            json.dumps(
                {
                    "userName": "sensitive.person@example.com",
                    "name": {"givenName": "Sensitive", "familyName": "Person"},
                    "entitlements": ["Agent"],
                }
            ),
        )
        event["headers"] = {
            "Authorization": "Bearer SuperSecretBearerTokenValue",
            "Content-Type": "application/scim+json",
        }
        event["requestContext"] = {"requestId": "req-abc-123"}
        return event

    def test_bearer_token_is_not_logged(self, connect, caplog):
        with caplog.at_level(logging.INFO):
            okta.lambda_handler(self._event_with_secrets(), None)
        assert "SuperSecretBearerTokenValue" not in caplog.text
        assert "Authorization" not in caplog.text

    def test_request_body_is_not_dumped(self, connect, caplog):
        with caplog.at_level(logging.INFO):
            okta.lambda_handler(self._event_with_secrets(), None)
        # No raw payload, and no personal-name attributes.
        assert "givenName" not in caplog.text
        assert "familyName" not in caplog.text
        assert "Sensitive" not in caplog.text
        assert "Person" not in caplog.text

    def test_username_is_logged_deliberately(self, connect, caplog):
        # The username is the SCIM resource identifier. It is the only way to tell
        # from CloudWatch which user a provisioning failure concerned, so it is
        # logged on purpose -- unlike the bearer token or the name attributes.
        with caplog.at_level(logging.INFO):
            okta.lambda_handler(self._event_with_secrets(), None)
        assert "sensitive.person@example.com" in caplog.text
        # It still must not arrive via a dump of the request body.
        assert "Received SCIM request" in caplog.text
        summary_line = next(
            line for line in caplog.text.splitlines() if "Received SCIM request" in line
        )
        assert "sensitive.person@example.com" not in summary_line

    def test_useful_routing_metadata_is_logged(self, connect, caplog):
        with caplog.at_level(logging.INFO):
            okta.lambda_handler(self._event_with_secrets(), None)
        assert "req-abc-123" in caplog.text
        assert "POST" in caplog.text

    def test_filter_is_logged_for_debuggability(self, connect, caplog):
        connect.add_user("alice@example.com", [AGENT_PROFILE])
        with caplog.at_level(logging.INFO):
            okta.lambda_handler(
                api_event("GET", "Users", query={"filter": 'userName eq "alice@example.com"'}),
                None,
            )
        # The filter names an attribute and an identifier, not a credential.
        assert "userName eq" in caplog.text

    def test_summary_omits_absent_fields(self, connect):
        summary = handler_core._request_summary(api_event("GET", "Users"), "okta")
        assert "filter" not in summary
        assert summary["method"] == "GET"


class TestRoutingAndErrors:
    def test_unknown_resource_is_404(self, connect):
        status, body = call("GET", "Widgets")
        assert status == 404
        assert "Widgets" in body["detail"]

    def test_unsupported_method_on_users_is_405(self, connect):
        status, _ = call("OPTIONS", "Users")
        assert status == 405

    def test_responses_use_the_scim_content_type(self, connect):
        connect.add_user("a@example.com", [AGENT_PROFILE])
        response = okta.lambda_handler(api_event("GET", "Users"), None)
        assert response["headers"]["Content-Type"] == "application/scim+json"

    def test_azure_scim_prefixed_path_is_routed(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, body = call("GET", f"scim/v2/Users/{user_id}", handler=azure)
        assert status == 200
        assert body["id"] == user_id
