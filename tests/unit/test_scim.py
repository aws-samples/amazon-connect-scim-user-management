"""Unit tests for the SCIM protocol layer."""

import json

import pytest
import scim


class TestParsePath:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Users", ("Users", None)),
            ("Users/abc-123", ("Users", "abc-123")),
            ("Groups", ("Groups", None)),
            ("Groups/sp-1", ("Groups", "sp-1")),
            # The Azure base URL carries a scim/v2 prefix.
            ("scim/v2/Users", ("Users", None)),
            ("scim/v2/Groups/sp-1", ("Groups", "sp-1")),
            ("/Users/abc/", ("Users", "abc")),
            # Providers are inconsistent about casing.
            ("users/abc", ("Users", "abc")),
            ("", (None, None)),
            (None, (None, None)),
        ],
    )
    def test_splits_resource_and_id(self, raw, expected):
        assert scim.parse_path(raw) == expected


class TestParseFilter:
    def test_single_term(self):
        assert scim.parse_filter('userName eq "test.user"') == {"username": "test.user"}

    def test_is_case_insensitive_on_attribute_and_operator(self):
        assert scim.parse_filter('USERNAME EQ "a"') == {"username": "a"}

    def test_two_terms_joined_by_and(self):
        parsed = scim.parse_filter('id eq "g1" and members eq "u1"')
        assert parsed == {"id": "g1", "members": "u1"}

    def test_dotted_attribute(self):
        assert scim.parse_filter('members.value eq "u1"') == {"members.value": "u1"}

    def test_value_containing_at_and_dots(self):
        assert scim.parse_filter('userName eq "first.last@example.com"') == {
            "username": "first.last@example.com"
        }

    def test_empty_filter_is_no_filter(self):
        assert scim.parse_filter(None) == {}
        assert scim.parse_filter("") == {}

    def test_unsupported_operator_is_rejected(self):
        with pytest.raises(scim.ScimError) as excinfo:
            scim.parse_filter('userName co "test"')
        assert excinfo.value.status == 400
        assert excinfo.value.scim_type == "invalidFilter"


class TestPagination:
    def test_page_size_defaults_and_clamps(self):
        assert scim.page_size({"queryStringParameters": None}) == scim.DEFAULT_PAGE_SIZE
        assert scim.page_size({"queryStringParameters": {"count": "10"}}) == 10
        assert scim.page_size({"queryStringParameters": {"count": "5000"}}) == scim.MAX_PAGE_SIZE

    def test_page_size_rejects_bad_input(self):
        with pytest.raises(scim.ScimError):
            scim.page_size({"queryStringParameters": {"count": "abc"}})
        with pytest.raises(scim.ScimError):
            scim.page_size({"queryStringParameters": {"count": "0"}})

    def test_start_index_floors_at_one(self):
        assert scim.start_index({"queryStringParameters": None}) == 1
        assert scim.start_index({"queryStringParameters": {"startIndex": "7"}}) == 7
        # RFC 7644 3.4.2.4: a value below 1 is interpreted as 1.
        assert scim.start_index({"queryStringParameters": {"startIndex": "0"}}) == 1
        assert scim.start_index({"queryStringParameters": {"startIndex": "-5"}}) == 1

    def test_list_response_shape(self):
        body = scim.list_response([{"id": "1"}], 42, 1)
        assert body["schemas"] == [scim.LIST_RESPONSE_SCHEMA]
        assert body["totalResults"] == 42
        assert body["startIndex"] == 1
        assert body["itemsPerPage"] == 1
        assert body["Resources"] == [{"id": "1"}]

    def test_empty_list_response_reports_zero_total(self):
        # The old handler reported totalResults 1 with an empty Resources list.
        body = scim.list_response([], 0, 1)
        assert body["totalResults"] == 0
        assert body["Resources"] == []
        assert body["itemsPerPage"] == 0

    def test_items_per_page_counts_the_page_not_the_request(self):
        """RFC 7644 section 3.4.2: itemsPerPage is the number of resources returned.

        It used to echo the requested ``count``, so every short page over-reported:
        a five-resource tail of a 25-per-page listing claimed 25. A client using
        itemsPerPage to decide whether another page exists would keep asking.
        """
        tail = scim.list_response([{"id": str(n)} for n in range(5)], 30, 26)
        assert tail["itemsPerPage"] == 5
        assert tail["itemsPerPage"] != scim.DEFAULT_PAGE_SIZE
        # The invariant that makes it impossible to get wrong.
        for count in (0, 1, 7, scim.MAX_PAGE_SIZE):
            body = scim.list_response([{"id": str(n)} for n in range(count)], 999, 1)
            assert body["itemsPerPage"] == len(body["Resources"]) == count


class TestParsePatchOperations:
    def test_normalises_operation_case(self):
        # Entra ID has historically sent a capitalised "Replace".
        operations = scim.parse_patch_operations(
            json.dumps({"Operations": [{"op": "Replace", "path": "active", "value": False}]})
        )
        assert operations[0]["op"] == "replace"

    def test_accepts_a_dict_body(self):
        operations = scim.parse_patch_operations({"Operations": [{"op": "add"}]})
        assert operations[0]["op"] == "add"

    def test_accepts_lowercase_operations_key(self):
        operations = scim.parse_patch_operations({"operations": [{"op": "add"}]})
        assert len(operations) == 1

    @pytest.mark.parametrize(
        "body",
        ["not json", json.dumps([]), json.dumps({}), json.dumps({"Operations": []})],
    )
    def test_rejects_malformed_bodies(self, body):
        with pytest.raises(scim.ScimError) as excinfo:
            scim.parse_patch_operations(body)
        assert excinfo.value.status == 400

    def test_rejects_unknown_operation(self):
        with pytest.raises(scim.ScimError):
            scim.parse_patch_operations({"Operations": [{"op": "frobnicate"}]})


class TestMemberIds:
    def test_list_of_value_objects(self):
        operation = {"op": "add", "path": "members", "value": [{"value": "u1"}, {"value": "u2"}]}
        assert scim.member_ids(operation) == ["u1", "u2"]

    def test_single_value_object(self):
        assert scim.member_ids({"op": "add", "path": "members", "value": {"value": "u1"}}) == ["u1"]

    def test_bare_string_value(self):
        assert scim.member_ids({"op": "add", "path": "members", "value": "u1"}) == ["u1"]

    def test_value_path_filter(self):
        # Azure sends removals as a value-path filter with no value body.
        operation = {"op": "remove", "path": 'members[value eq "u9"]'}
        assert scim.member_ids(operation) == ["u9"]

    def test_deduplicates_and_preserves_order(self):
        operation = {
            "op": "add",
            "path": "members",
            "value": [{"value": "b"}, {"value": "a"}, {"value": "b"}],
        }
        assert scim.member_ids(operation) == ["b", "a"]

    def test_no_members_yields_empty_list(self):
        assert scim.member_ids({"op": "remove", "path": "members"}) == []

    def test_ignores_non_member_entries(self):
        assert scim.member_ids(
            {"op": "add", "path": "members", "value": [{}, {"value": "u1"}]}
        ) == ["u1"]


class TestTargetsMembers:
    @pytest.mark.parametrize(
        "path", ["members", "Members", 'members[value eq "u1"]', "members.value"]
    )
    def test_member_paths(self, path):
        assert scim.targets_members({"op": "add", "path": path})

    def test_pathless_operation_with_members_in_value(self):
        assert scim.targets_members({"op": "add", "value": {"members": [{"value": "u1"}]}})

    @pytest.mark.parametrize("path", ["displayName", "externalId"])
    def test_non_member_paths(self, path):
        assert not scim.targets_members({"op": "replace", "path": path})

    def test_pathless_operation_without_members(self):
        assert not scim.targets_members({"op": "replace", "value": {"displayName": "x"}})


class TestActiveFlag:
    @pytest.mark.parametrize(
        ("operations", "expected"),
        [
            # Okta's documented shape.
            ([{"op": "replace", "value": {"active": False}}], False),
            ([{"op": "replace", "value": {"active": True}}], True),
            # The path-based shape, which the old handler crashed on because it
            # indexed value['active'] on a bool.
            ([{"op": "replace", "path": "active", "value": False}], False),
            ([{"op": "replace", "path": "active", "value": True}], True),
            # String encodings some connectors emit.
            ([{"op": "replace", "path": "active", "value": "False"}], False),
            ([{"op": "replace", "path": "active", "value": "true"}], True),
            ([{"op": "replace", "value": {"active": "No"}}], False),
            # Nothing addressing active at all.
            ([{"op": "replace", "path": "displayName", "value": "x"}], None),
            ([], None),
        ],
    )
    def test_resolves_active(self, operations, expected):
        assert scim.active_flag(operations) is expected

    def test_case_insensitive_key(self):
        assert scim.active_flag([{"op": "replace", "value": {"Active": False}}]) is False

    def test_last_operation_wins(self):
        operations = [
            {"op": "replace", "path": "active", "value": True},
            {"op": "replace", "path": "active", "value": False},
        ]
        assert scim.active_flag(operations) is False


class TestResponses:
    def test_error_response_shape(self):
        response = scim.error_response(scim.ScimError(404, "gone", scim_type="invalidValue"))
        assert response["statusCode"] == 404
        assert response["headers"]["Content-Type"] == "application/scim+json"
        body = json.loads(response["body"])
        assert body["schemas"] == [scim.ERROR_SCHEMA]
        assert body["status"] == "404"
        assert body["scimType"] == "invalidValue"

    def test_no_content_response_has_an_empty_body(self):
        response = scim.json_response(204)
        assert response["statusCode"] == 204
        assert response["body"] == ""
