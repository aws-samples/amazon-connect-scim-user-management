"""SCIM 2.0 protocol helpers shared by the Okta and Azure user-management handlers.

This module is deliberately free of boto3 so the protocol behaviour can be unit
tested without AWS credentials. Anything that talks to Amazon Connect lives in
``connect_directory``.

Pagination is the index-based form defined by RFC 7644 section 3.4.2.4
(``startIndex``/``count`` in the request, ``totalResults``/``startIndex``/
``itemsPerPage`` in the response), which is what the Okta SCIM 2.0 application and
Microsoft Entra ID both send.
"""

import json
import logging
import re

LOGGER = logging.getLogger()

LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_USER_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"

# Page sizes are bounded by how many Amazon Connect calls a page costs, not by
# taste. Building a SCIM user resource needs one DescribeUser per user, so a page
# of N users costs N+2 calls; Connect throttles them at 2 requests per second and
# API Gateway cuts the integration off at 29 seconds. That puts the hard ceiling
# near 56 users, so the cap leaves headroom for retries and the default sits well
# inside it. RFC 7644 section 3.4.2.4 permits returning fewer items than requested,
# so a client asking for more simply paginates.
MAX_PAGE_SIZE = 40
DEFAULT_PAGE_SIZE = 25

# Each membership change costs a DescribeUser plus an UpdateUserSecurityProfiles,
# and Amazon Connect throttles those at 2 requests per second. The binding
# constraint is API Gateway's 29 second integration timeout, which every
# deployment inherits because none of them configure one -- past that the caller
# has a 504 while the function keeps mutating users. Lambda timeouts differ per
# deployment (CDK 900s, Terraform 600s, CloudFormation 30s), so they are not the
# ceiling to reason from. This bound fails fast with an explanation rather than
# timing out, but it is above what 29 seconds can actually complete; see the
# open item on harmonising the timeouts.
MAX_MEMBERSHIP_CHANGES = 250

# ``<attr> eq "<value>"`` is the only comparison Okta and Entra ID emit for
# user/group lookup.
_FILTER_TERM = re.compile(
    r'(?P<attr>[\w.:$-]+)\s+eq\s+"(?P<value>[^"]*)"',
    re.IGNORECASE,
)

# A members path that names specific members, e.g. members[value eq "abc"].
_MEMBER_PATH_FILTER = re.compile(r'\[\s*[\w.]+\s+eq\s+"', re.IGNORECASE)


class ScimError(Exception):
    """A SCIM error that maps onto a specific HTTP status and ``scimType``."""

    def __init__(self, status, detail, scim_type=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


def json_response(status, body=None, headers=None):
    """Build an API Gateway proxy response carrying a SCIM JSON body."""
    response_headers = {"Content-Type": "application/scim+json"}
    if headers:
        response_headers.update(headers)
    response = {"statusCode": status, "headers": response_headers}
    # 204 must not carry a body; API Gateway forwards an empty string as-is.
    response["body"] = "" if body is None else json.dumps(body)
    return response


def error_response(error):
    """Render a :class:`ScimError` as an RFC 7644 error object."""
    body = {
        "schemas": [ERROR_SCHEMA],
        "status": str(error.status),
        "detail": error.detail,
    }
    if error.scim_type:
        body["scimType"] = error.scim_type
    return json_response(error.status, body)


def parse_path(path_value):
    """Split the greedy API Gateway path into a ``(resource, resource_id)`` pair.

    Handles the shapes both providers produce, including the ``scim/v2`` prefix
    that the Azure base URL carries:

    ``Users`` -> ``("Users", None)``
    ``Users/9f2a`` -> ``("Users", "9f2a")``
    ``scim/v2/Groups/abc`` -> ``("Groups", "abc")``
    """
    segments = [segment for segment in (path_value or "").split("/") if segment]
    # Drop any endpoint prefix ahead of the resource name.
    while segments and segments[0].lower() in ("scim", "v2"):
        segments.pop(0)
    if not segments:
        return None, None
    resource = segments[0]
    for canonical in ("Users", "Groups"):
        if resource.lower() == canonical.lower():
            resource = canonical
            break
    resource_id = segments[1] if len(segments) > 1 else None
    return resource, resource_id


def parse_filter(filter_expression):
    """Parse a SCIM filter into a dict of lower-cased attribute -> value.

    Only the ``eq`` operator is supported, optionally joined by ``and``, which
    covers every filter Okta and Entra ID send for provisioning.
    """
    if not filter_expression:
        return {}
    terms = {
        match.group("attr").lower(): match.group("value")
        for match in _FILTER_TERM.finditer(filter_expression)
    }
    if not terms:
        raise ScimError(
            400,
            f"Unsupported filter '{filter_expression}'. Only "
            "'<attribute> eq \"<value>\"' is supported.",
            scim_type="invalidFilter",
        )
    return terms


def query_params(event):
    """Return the request's query string parameters, tolerating ``None``."""
    return event.get("queryStringParameters") or {}


def page_size(event):
    """Resolve the requested page size, clamped to the supported range."""
    raw = query_params(event).get("count")
    if raw in (None, ""):
        return DEFAULT_PAGE_SIZE
    try:
        requested = int(raw)
    except (TypeError, ValueError) as exc:
        raise ScimError(400, "'count' must be an integer.", scim_type="invalidValue") from exc
    if requested < 1:
        raise ScimError(400, "'count' must be 1 or greater.", scim_type="invalidValue")
    return min(requested, MAX_PAGE_SIZE)


def start_index(event):
    """Resolve the 1-based ``startIndex`` used by index-based pagination."""
    raw = query_params(event).get("startIndex")
    if raw in (None, ""):
        return 1
    try:
        requested = int(raw)
    except (TypeError, ValueError) as exc:
        raise ScimError(400, "'startIndex' must be an integer.", scim_type="invalidValue") from exc
    # RFC 7644 section 3.4.2.4: values less than 1 are interpreted as 1.
    return max(requested, 1)


def list_response(resources, total_results, start):
    """Build a ``ListResponse`` per RFC 7644 section 3.4.2.

    ``itemsPerPage`` is derived from the page rather than passed in. RFC 7644
    section 3.4.2 defines it as the number of resources returned, so echoing the
    requested ``count`` over-reports every short page -- the last page of a set, a
    filter matching one user, or a filter matching none. A client that trusts it to
    decide whether more pages exist would keep paging past the end.
    """
    return {
        "schemas": [LIST_RESPONSE_SCHEMA],
        "totalResults": total_results,
        "startIndex": start,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def parse_patch_operations(body):
    """Validate a SCIM PatchOp body and return its normalised operations.

    ``op`` is lower-cased because providers are inconsistent about case (Entra ID
    has historically sent ``Replace``), while RFC 7644 specifies lower case.
    """
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError) as exc:
        raise ScimError(400, "Request body is not valid JSON.", scim_type="invalidSyntax") from exc
    if not isinstance(payload, dict):
        raise ScimError(400, "Request body must be a JSON object.", scim_type="invalidSyntax")

    operations = payload.get("Operations") or payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ScimError(
            400,
            "PatchOp requires a non-empty 'Operations' array.",
            scim_type="invalidValue",
        )

    normalised = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimError(
                400, "Each PatchOp operation must be an object.", scim_type="invalidSyntax"
            )
        op_name = str(operation.get("op", "")).lower()
        if op_name not in ("add", "remove", "replace"):
            raise ScimError(
                400,
                "Unsupported PatchOp operation '{}'.".format(operation.get("op")),
                scim_type="invalidValue",
            )
        normalised.append(
            {
                "op": op_name,
                "path": operation.get("path"),
                "value": operation.get("value"),
            }
        )
    return normalised


def carries_member_value(operation):
    """True when the operation supplies members, whatever shape it uses.

    This is what separates "clear the whole collection" from "act on the members
    I named". RFC 7644 section 3.5.2.2 gives a ``remove`` with no value the former
    meaning, so a valueless remove must reach the clear-all path -- but an
    operation that *did* supply members and simply could not be parsed must not,
    or a targeted removal silently empties the group.
    """
    # Only a genuinely absent value means "clear the collection". An empty value
    # ({}, [], "") is ambiguous, and the destructive reading should require the
    # unambiguous form -- so an empty one counts as supplied and will be refused
    # once extraction yields nothing. parse_patch_operations normalises an absent
    # value to None, which is what distinguishes the two here.
    if operation.get("value") is not None:
        return True
    # An id can also arrive in a value-path filter, e.g. members[value eq "x"].
    return bool(_MEMBER_PATH_FILTER.search(operation.get("path") or ""))


def carries_value(operation):
    """True when the operation supplies a value in any shape. See above."""
    return carries_member_value(operation)


def _referenced_values(operation, attribute):
    """Extract the values a PatchOp operation references for a multi-valued attribute.

    Providers send five shapes, and all five have to work or a change is silently
    dropped::

        {"path": "<attr>", "value": [{"value": "<v>"}]}   list of complex values
        {"path": "<attr>", "value": {"value": "<v>"}}     single complex value
        {"path": "<attr>", "value": "<v>"}                bare string
        {"path": "<attr>[value eq \"<v>\"]"}                value-path filter
        {"value": {"<attr>": [{"value": "<v>"}]}}         pathless, target in value

    Members and entitlements share this because they are the same SCIM construct;
    handling them in one place is what keeps a fix to one from missing the other.
    """
    values = []
    path = operation.get("path") or ""

    # A value carried in a value-path filter, e.g. members[value eq "abc"].
    for match in _FILTER_TERM.finditer(path):
        if match.group("attr").lower() in ("value", f"{attribute}.value", "display"):
            values.append(match.group("value"))

    value = operation.get("value")
    # Pathless form: unwrap value[<attribute>] before looking for values.
    if not path.strip() and isinstance(value, dict) and attribute in value:
        value = value[attribute]

    candidates = value if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, dict):
            found = candidate.get("value") or candidate.get("id") or candidate.get("display")
            if found:
                values.append(str(found))
        elif isinstance(candidate, str) and candidate:
            values.append(candidate)

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(values))


def member_ids(operation):
    """Extract the member identifiers a group PatchOp operation references."""
    return _referenced_values(operation, "members")


def entitlement_names(operation):
    """Extract the security profile names a user PatchOp operation references."""
    return _referenced_values(operation, "entitlements")


def targets_attribute(operation, attribute):
    """True when the operation acts on the named multi-valued attribute."""
    path = (operation.get("path") or "").strip()
    if not path:
        # A pathless op carries its target in the value object.
        value = operation.get("value")
        return isinstance(value, dict) and attribute in value
    return path.lower().startswith(attribute)


def targets_members(operation):
    """True when a group PatchOp operation acts on the ``members`` attribute."""
    return targets_attribute(operation, "members")


def active_flag(operations):
    """Resolve the requested ``active`` state from a user PatchOp, if present.

    Handles both layouts providers send::

        {"op": "replace", "path": "active", "value": false}
        {"op": "replace", "value": {"active": false}}

    and the string forms ("false", "False", "No") that some connectors emit.
    Returns ``None`` when no operation addresses ``active``.
    """
    resolved = None
    for operation in operations:
        path = (operation.get("path") or "").strip().lower()
        value = operation.get("value")
        if path == "active":
            resolved = _coerce_bool(value)
        elif isinstance(value, dict):
            for key, candidate in value.items():
                if key.lower() == "active":
                    resolved = _coerce_bool(candidate)
    return resolved


def _coerce_bool(value):
    """Coerce the truthy/falsey encodings providers send into a bool.

    Raises :class:`ScimError` when an ``active`` value is present but not
    recognised. Returning ``None`` here would make it indistinguishable from
    "no operation addressed active", and the caller would treat a deactivation it
    could not read as a no-op and answer 200.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in ("true", "yes", "1"):
            return True
        if normalised in ("false", "no", "0"):
            return False
    raise ScimError(
        400,
        f"Unsupported value for 'active': {value!r}. Expected a boolean or one of "
        "true/false/yes/no/1/0.",
        scim_type="invalidValue",
    )
