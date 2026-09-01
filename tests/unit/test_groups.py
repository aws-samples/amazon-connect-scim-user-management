"""Tests for the /Groups endpoint, including group membership patching.

Every request to /Groups previously returned a fixed stub, so these cover the
membership behaviour that was absent: per-member add and remove, remove-all,
and whole-collection replace applied as a delta.
"""

import json

import okta
import scim
from conftest import AGENT_PROFILE, QA_PROFILE, SUPERVISOR_PROFILE, api_event


def call(method, path, body=None, query=None):
    """Invoke the Okta handler and return ``(status, parsed_body)``."""
    response = okta.lambda_handler(api_event(method, path, body, query), None)
    parsed = json.loads(response["body"]) if response["body"] else None
    return response["statusCode"], parsed


def patch_body(operations):
    return json.dumps(
        {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": operations}
    )


class TestListGroups:
    def test_lists_security_profiles_as_groups(self, connect):
        status, body = call("GET", "Groups")
        assert status == 200
        assert body["totalResults"] == 3
        assert {group["displayName"] for group in body["Resources"]} == {
            "Agent",
            "Supervisor",
            "QualityAnalyst",
        }
        assert all(
            group["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:Group"]
            for group in body["Resources"]
        )

    def test_list_groups_reports_empty_members(self, connect):
        # Membership is read via GET /Groups/{id} or a members.value filter, so a
        # list costs one call rather than a SearchUsers per group.
        connect.add_user("a@example.com", [AGENT_PROFILE])
        _, body = call("GET", "Groups")
        assert all(group["members"] == [] for group in body["Resources"])

    def test_filter_by_display_name(self, connect):
        status, body = call("GET", "Groups", query={"filter": 'displayName eq "Supervisor"'})
        assert status == 200
        assert body["totalResults"] == 1
        assert body["Resources"][0]["id"] == SUPERVISOR_PROFILE

    def test_filter_by_display_name_not_found_is_an_empty_list(self, connect):
        status, body = call("GET", "Groups", query={"filter": 'displayName eq "Nope"'})
        assert status == 200
        assert body["totalResults"] == 0
        assert body["Resources"] == []

    def test_filter_by_member_value_returns_that_users_groups(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, QA_PROFILE])
        status, body = call("GET", "Groups", query={"filter": f'members.value eq "{user_id}"'})
        assert status == 200
        assert {group["id"] for group in body["Resources"]} == {AGENT_PROFILE, QA_PROFILE}

    def test_unsupported_filter_attribute_is_rejected(self, connect):
        status, body = call("GET", "Groups", query={"filter": 'description eq "x"'})
        assert status == 400
        assert body["scimType"] == "invalidFilter"


class TestGetGroup:
    def test_returns_members(self, connect):
        alice = connect.add_user("alice@example.com", [AGENT_PROFILE])
        bob = connect.add_user("bob@example.com", [AGENT_PROFILE, QA_PROFILE])
        connect.add_user("carol@example.com", [QA_PROFILE])

        status, body = call("GET", f"Groups/{AGENT_PROFILE}")
        assert status == 200
        assert body["id"] == AGENT_PROFILE
        assert {member["value"] for member in body["members"]} == {alice, bob}

    def test_resolves_a_group_by_display_name_on_the_path(self, connect):
        status, body = call("GET", "Groups/Supervisor")
        assert status == 200
        assert body["id"] == SUPERVISOR_PROFILE

    def test_unknown_group_is_404(self, connect):
        status, body = call("GET", "Groups/does-not-exist")
        assert status == 404
        assert body["status"] == "404"


class TestPatchGroupAddMembers:
    def test_adds_a_member(self, connect):
        user_id = connect.add_user("alice@example.com", [AGENT_PROFILE])
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(user_id)
        # The response reports real membership, not just an acknowledgement.
        assert {member["value"] for member in body["members"]} == {user_id}

    def test_adds_several_members_in_one_request(self, connect):
        first = connect.add_user("a@example.com", [AGENT_PROFILE])
        second = connect.add_user("b@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {
                        "op": "add",
                        "path": "members",
                        "value": [{"value": first}, {"value": second}],
                    }
                ]
            ),
        )
        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(first)
        assert QA_PROFILE in connect.profile_ids_of(second)

    def test_adding_an_existing_member_is_a_no_op(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        call(
            "PATCH",
            f"Groups/{AGENT_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert connect.count("update_user_security_profiles") == 0
        assert connect.profile_ids_of(user_id) == [AGENT_PROFILE]

    def test_preserves_a_users_other_profiles(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, SUPERVISOR_PROFILE])
        call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert set(connect.profile_ids_of(user_id)) == {
            AGENT_PROFILE,
            SUPERVISOR_PROFILE,
            QA_PROFILE,
        }

    def test_unknown_member_is_skipped_rather_than_failing_the_batch(self, connect):
        good = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {
                        "op": "add",
                        "path": "members",
                        "value": [{"value": good}, {"value": "ghost-user"}],
                    }
                ]
            ),
        )
        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(good)
        assert {member["value"] for member in body["members"]} == {good}


class TestPatchGroupRemoveMembers:
    def test_removes_a_member(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, QA_PROFILE])
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [AGENT_PROFILE]
        assert body["members"] == []

    def test_removes_via_a_value_path_filter(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, QA_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": f'members[value eq "{user_id}"]'}]),
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [AGENT_PROFILE]

    def test_removing_a_non_member_is_a_no_op(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert status == 200
        assert connect.count("update_user_security_profiles") == 0

    def test_removing_a_users_last_profile_is_refused_and_reported(self, connect):
        # Amazon Connect requires every user to keep at least one security
        # profile, so this removal cannot be applied. The response must show the
        # user still in the group rather than claiming a clean success.
        user_id = connect.add_user("a@example.com", [QA_PROFILE])
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert status == 200
        assert connect.profile_ids_of(user_id) == [QA_PROFILE]
        assert {member["value"] for member in body["members"]} == {user_id}
        assert connect.count("update_user_security_profiles") == 0

    def test_remove_with_no_value_clears_every_member(self, connect):
        # RFC 7644 3.5.2.2: a remove with no value clears the attribute. An
        # identity provider sends this when a group is unassigned, so it has to
        # mean "remove all current members" rather than fail.
        alice = connect.add_user("alice@example.com", [AGENT_PROFILE, QA_PROFILE])
        bob = connect.add_user("bob@example.com", [AGENT_PROFILE, QA_PROFILE])
        outsider = connect.add_user("outsider@example.com", [AGENT_PROFILE])

        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members"}]),
        )

        assert status == 200
        assert body["members"] == []
        assert connect.profile_ids_of(alice) == [AGENT_PROFILE]
        assert connect.profile_ids_of(bob) == [AGENT_PROFILE]
        # A non-member is untouched.
        assert connect.profile_ids_of(outsider) == [AGENT_PROFILE]

    def test_remove_all_on_an_empty_group_is_a_no_op(self, connect):
        connect.add_user("outsider@example.com", [AGENT_PROFILE])
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members"}]),
        )
        assert status == 200
        assert body["members"] == []
        assert connect.count("update_user_security_profiles") == 0

    def test_remove_all_keeps_a_member_whose_only_profile_it_is(self, connect):
        # Connect requires every user to retain at least one security profile.
        only_qa = connect.add_user("onlyqa@example.com", [QA_PROFILE])
        removable = connect.add_user("removable@example.com", [AGENT_PROFILE, QA_PROFILE])

        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members"}]),
        )

        assert status == 200
        assert connect.profile_ids_of(only_qa) == [QA_PROFILE]
        assert connect.profile_ids_of(removable) == [AGENT_PROFILE]
        # The response tells the truth about who is still a member.
        assert {member["value"] for member in body["members"]} == {only_qa}


class TestPatchGroupReplaceMembers:
    def test_replace_is_applied_as_a_delta(self, connect):
        stays = connect.add_user("stays@example.com", [AGENT_PROFILE, QA_PROFILE])
        leaves = connect.add_user("leaves@example.com", [AGENT_PROFILE, QA_PROFILE])
        joins = connect.add_user("joins@example.com", [AGENT_PROFILE])

        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {
                        "op": "replace",
                        "path": "members",
                        "value": [{"value": stays}, {"value": joins}],
                    }
                ]
            ),
        )

        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(stays)
        assert QA_PROFILE in connect.profile_ids_of(joins)
        assert QA_PROFILE not in connect.profile_ids_of(leaves)
        assert {member["value"] for member in body["members"]} == {stays, joins}

    def test_replace_does_not_touch_members_that_stay(self, connect):
        stays = connect.add_user("stays@example.com", [AGENT_PROFILE, QA_PROFILE])
        call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "replace", "path": "members", "value": [{"value": stays}]}]),
        )
        # The group is never emptied and re-filled, so an unchanged member sees
        # no write at all.
        touched = [
            payload["UserId"]
            for name, payload in connect.calls
            if name == "update_user_security_profiles"
        ]
        assert stays not in touched


class TestPatchGroupOther:
    def test_display_name_change_is_acknowledged_without_acting(self, connect):
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "replace", "path": "displayName", "value": "Renamed"}]),
        )
        assert status == 200
        # Renaming a security profile is an IAM-authorised action, not a SCIM one.
        assert body["displayName"] == "QualityAnalyst"

    def test_unknown_group_is_404(self, connect):
        status, _ = call(
            "PATCH",
            "Groups/nope",
            patch_body([{"op": "add", "path": "members", "value": [{"value": "u1"}]}]),
        )
        assert status == 404

    def test_malformed_patch_body_is_a_400(self, connect):
        status, _ = call("PATCH", f"Groups/{QA_PROFILE}", "not json")
        assert status == 400

    def test_empty_operations_is_a_400(self, connect):
        status, _ = call("PATCH", f"Groups/{QA_PROFILE}", json.dumps({"Operations": []}))
        assert status == 400

    def test_exceeding_the_membership_change_cap_is_rejected(self, connect):
        # The cap bounds how many Connect API calls one invocation can make
        # against a 2 requests-per-second quota, so it fails fast rather than
        # timing out halfway through.
        members = [{"value": f"user-{index}"} for index in range(scim.MAX_MEMBERSHIP_CHANGES + 1)]
        status, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": members}]),
        )
        assert status == 400
        assert body["scimType"] == "tooMany"

    def test_a_request_at_the_cap_is_accepted(self, connect):
        members = [{"value": f"user-{index}"} for index in range(scim.MAX_MEMBERSHIP_CHANGES)]
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": members}]),
        )
        assert status == 200

    def test_remove_then_add_leaves_the_member_in_the_group(self, connect):
        # RFC 7644 applies operations in order, so the trailing add decides.
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {"op": "remove", "path": "members", "value": [{"value": user_id}]},
                    {"op": "add", "path": "members", "value": [{"value": user_id}]},
                ]
            ),
        )
        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(user_id)

    def test_add_then_remove_leaves_the_member_out_of_the_group(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {"op": "add", "path": "members", "value": [{"value": user_id}]},
                    {"op": "remove", "path": "members", "value": [{"value": user_id}]},
                ]
            ),
        )
        assert status == 200
        assert QA_PROFILE not in connect.profile_ids_of(user_id)

    def test_replace_supersedes_an_earlier_remove_of_a_retained_member(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, QA_PROFILE])
        status, _ = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body(
                [
                    {"op": "remove", "path": "members", "value": [{"value": user_id}]},
                    {"op": "replace", "path": "members", "value": [{"value": user_id}]},
                ]
            ),
        )
        assert status == 200
        assert QA_PROFILE in connect.profile_ids_of(user_id)
        # The member was already correct, so no write was needed at all.
        assert connect.count("update_user_security_profiles") == 0


class TestStaleSearchIndexReconciliation:
    """The patch response must reflect the write, not the lagging search index.

    Group membership can only be enumerated with SearchUsers, which is served from
    an index that lags a write by a few seconds. Reading it straight back after a
    patch reports the change as not applied, so the confirmed per-user results are
    folded over it.
    """

    def test_added_member_appears_despite_a_stale_index(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        _, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        # The index still shows the pre-patch state.
        assert QA_PROFILE not in connect._search_index[user_id]["SecurityProfileIds"]
        # The response nonetheless reports the real, written membership.
        assert {member["value"] for member in body["members"]} == {user_id}
        assert QA_PROFILE in connect.profile_ids_of(user_id)

    def test_removed_member_disappears_despite_a_stale_index(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE, QA_PROFILE])
        _, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "remove", "path": "members", "value": [{"value": user_id}]}]),
        )
        # The index still lists the user as a member.
        assert QA_PROFILE in connect._search_index[user_id]["SecurityProfileIds"]
        assert body["members"] == []

    def test_members_not_named_in_the_patch_are_preserved(self, connect):
        existing = connect.add_user("existing@example.com", [AGENT_PROFILE, QA_PROFILE])
        joining = connect.add_user("joining@example.com", [AGENT_PROFILE])
        _, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": joining}]}]),
        )
        # The untouched member comes from the index; the new one from the write.
        assert {member["value"] for member in body["members"]} == {existing, joining}

    def test_member_display_names_are_populated(self, connect):
        user_id = connect.add_user("named@example.com", [AGENT_PROFILE])
        _, body = call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        assert body["members"][0]["display"] == "named@example.com"

    def test_a_settled_index_gives_the_same_answer(self, connect):
        user_id = connect.add_user("a@example.com", [AGENT_PROFILE])
        call(
            "PATCH",
            f"Groups/{QA_PROFILE}",
            patch_body([{"op": "add", "path": "members", "value": [{"value": user_id}]}]),
        )
        connect.refresh_search_index()
        _, body = call("GET", f"Groups/{QA_PROFILE}")
        assert {member["value"] for member in body["members"]} == {user_id}


class TestGroupLinkAndDelete:
    def test_pushing_an_existing_group_links_to_it(self, connect):
        status, body = call("POST", "Groups", json.dumps({"displayName": "Supervisor"}))
        assert status == 200
        assert body["id"] == SUPERVISOR_PROFILE

    def test_pushing_an_unknown_group_explains_what_to_do(self, connect):
        status, body = call("POST", "Groups", json.dumps({"displayName": "Brand New"}))
        assert status == 400
        assert "security profile" in body["detail"].lower()

    def test_group_deletion_is_refused(self, connect):
        status, body = call("DELETE", f"Groups/{QA_PROFILE}")
        assert status == 403
        assert "security profile" in body["detail"].lower()

    def test_unsupported_method_is_405(self, connect):
        status, _ = call("HEAD", "Groups")
        assert status == 405
