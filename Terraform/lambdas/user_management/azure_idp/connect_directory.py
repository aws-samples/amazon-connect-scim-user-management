"""Amazon Connect adapter that backs the SCIM resources exposed by the handlers.

Resource mapping
----------------
SCIM Users map onto Amazon Connect users. SCIM Groups map onto Amazon Connect
*security profiles*, and a group's membership is the set of users that have that
security profile attached. That is the same relationship the solution has always
expressed through the ``entitlements`` attribute, now addressable through the
``/Groups`` endpoint so an identity provider can synchronise membership with
``PATCH /Groups/{id}`` instead of re-sending the whole user record.

Consistency
-----------
Writes read the affected user with ``DescribeUser``, which is strongly
consistent, so a membership change is always computed against the user's real
current profile set. Enumerating a whole group's membership uses ``SearchUsers``
because it is the only call that returns ``SecurityProfileIds`` inline; it is
served from a search index and can lag a write by a short period.
"""

import logging
import os

import boto3
import botocore
from botocore.config import Config
from scim import (
    ENTERPRISE_USER_SCHEMA,
    GROUP_SCHEMA,
    MAX_MEMBERSHIP_CHANGES,
    USER_SCHEMA,
    ScimError,
)

LOGGER = logging.getLogger()

INSTANCE_ID = os.getenv("INSTANCE_ID")
DEFAULT_ROUTING_PROFILE = os.getenv("DEFAULT_ROUTING_PROFILE", "Basic Routing Profile")
DEFAULT_SECURITY_PROFILE = os.getenv("DEFAULT_SECURITY_PROFILE", "Agent")

# Amazon Connect throttles the user-management APIs at 2 requests per second with
# a burst of 5, and one group patch can touch many users. Adaptive retries let the
# client absorb that instead of surfacing throttling to the IdP, where it would
# show up as a provisioning failure.
_CLIENT_CONFIG = Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
    user_agent_extra="connect-scim-user-management",
)

_CLIENT = None


def client():
    """Return the shared Amazon Connect client, created on first use."""
    # A module-level singleton keeps the client (and its connection pool)
    # alive across warm invocations, and lets tests swap in a fake.
    global _CLIENT  # noqa: PLW0603
    if _CLIENT is None:
        _CLIENT = boto3.client("connect", config=_CLIENT_CONFIG)
    return _CLIENT


def _fail(operation, error):
    """Log a boto3 client error and re-raise it as an equivalent SCIM error."""
    code = error.response.get("Error", {}).get("Code", "Unknown")
    LOGGER.error(
        "Connect User Management Failure - %s failed with %s: %s",
        operation,
        code,
        error.response.get("Error", {}).get("Message", ""),
    )
    if code in ("ResourceNotFoundException",):
        raise ScimError(404, "Resource not found in the Amazon Connect instance.")
    if code in ("DuplicateResourceException",):
        raise ScimError(409, "Resource already exists.", scim_type="uniqueness")
    if code in ("InvalidParameterException", "InvalidRequestException"):
        raise ScimError(
            400,
            f"Amazon Connect rejected the request: {code}",
            scim_type="invalidValue",
        )
    if code in ("ThrottlingException", "TooManyRequestsException", "LimitExceededException"):
        raise ScimError(429, "Amazon Connect throttled the request. Retry later.")
    if code in ("AccessDeniedException",):
        raise ScimError(403, "Not authorised to perform this Amazon Connect operation.")
    raise ScimError(500, f"Amazon Connect error: {code}")


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def iter_users():
    """Yield every user summary in the instance, following pagination.

    ``ListUsers`` is authoritative, unlike the ``SearchUsers`` index, so it is
    what user lookup relies on.
    """
    try:
        paginator = client().get_paginator("list_users")
        for page in paginator.paginate(InstanceId=INSTANCE_ID):
            yield from page.get("UserSummaryList", [])
    except botocore.exceptions.ClientError as error:
        _fail("ListUsers", error)


def find_user(identifier):
    """Look up a user by Amazon Connect id or by username.

    The identity provider addresses a user by SCIM ``id`` (the Connect user id)
    on a path, but by ``userName`` in a filter, so both have to resolve. Matching
    only on id was why ``userName eq`` lookups never found an existing user.
    """
    if not identifier:
        return None
    wanted = identifier.casefold()
    for summary in iter_users():
        if summary.get("Id") == identifier or summary.get("Username", "").casefold() == wanted:
            LOGGER.info(
                "Resolved '%s' to Connect user %s in instance %s",
                identifier,
                summary.get("Id"),
                INSTANCE_ID,
            )
            return summary
    LOGGER.info("No Connect user matched '%s' in instance %s", identifier, INSTANCE_ID)
    return None


def describe_user(user_id):
    """Return the full, strongly consistent record for a single user."""
    try:
        return client().describe_user(UserId=user_id, InstanceId=INSTANCE_ID)["User"]
    except botocore.exceptions.ClientError as error:
        _fail("DescribeUser", error)


def create_user(user_name, first_name, last_name, security_profile_ids, routing_profile_id):
    """Create an Amazon Connect user and return its new id."""
    try:
        output = client().create_user(
            Username=user_name,
            IdentityInfo={"FirstName": first_name, "LastName": last_name},
            PhoneConfig={
                "PhoneType": "SOFT_PHONE",
                "AutoAccept": False,
                "AfterContactWorkTimeLimit": 30,
            },
            SecurityProfileIds=security_profile_ids,
            RoutingProfileId=routing_profile_id,
            InstanceId=INSTANCE_ID,
        )
        LOGGER.info("Created Connect user %s (%s)", user_name, output["UserId"])
        return output["UserId"]
    except botocore.exceptions.ClientError as error:
        _fail("CreateUser", error)


def delete_user(user_id):
    """Delete an Amazon Connect user.

    Amazon Connect has no notion of a disabled user, so a SCIM
    ``active: false`` deactivation is necessarily destructive here.
    """
    try:
        client().delete_user(InstanceId=INSTANCE_ID, UserId=user_id)
        LOGGER.info("Deleted Connect user %s", user_id)
    except botocore.exceptions.ClientError as error:
        _fail("DeleteUser", error)


def set_user_security_profiles(user_id, security_profile_ids):
    """Replace a user's security profile associations."""
    try:
        client().update_user_security_profiles(
            SecurityProfileIds=security_profile_ids,
            UserId=user_id,
            InstanceId=INSTANCE_ID,
        )
        LOGGER.info(
            "Updated security profiles for Connect user %s to %s",
            user_id,
            security_profile_ids,
        )
    except botocore.exceptions.ClientError as error:
        _fail("UpdateUserSecurityProfiles", error)


# --------------------------------------------------------------------------
# Security profiles (SCIM Groups) and routing profiles
# --------------------------------------------------------------------------


def iter_security_profiles():
    """Yield every security profile summary in the instance."""
    try:
        paginator = client().get_paginator("list_security_profiles")
        for page in paginator.paginate(InstanceId=INSTANCE_ID):
            yield from page.get("SecurityProfileSummaryList", [])
    except botocore.exceptions.ClientError as error:
        _fail("ListSecurityProfiles", error)


def find_security_profile(identifier):
    """Look up a security profile by id or by name."""
    if not identifier:
        return None
    wanted = identifier.casefold()
    for summary in iter_security_profiles():
        if summary.get("Id") == identifier or summary.get("Name", "").casefold() == wanted:
            return summary
    return None


def security_profile_ids_for_names(names):
    """Resolve security profile names to ids, reporting any that do not exist.

    A name the instance does not have is an error rather than a silent omission:
    dropping it would hand the user a narrower permission set than the identity
    provider asked for, with nothing in the response to say so.
    """
    requested = [name for name in (names or []) if name]
    if not requested:
        return []
    by_name = {summary["Name"].casefold(): summary["Id"] for summary in iter_security_profiles()}
    resolved = []
    missing = []
    for name in requested:
        profile_id = by_name.get(name.casefold())
        if profile_id:
            resolved.append(profile_id)
        else:
            missing.append(name)
    if missing:
        raise ScimError(
            400,
            "Unknown Amazon Connect security profile(s): {}. Create them in the "
            "instance, or correct the 'entitlements' values sent by the identity "
            "provider.".format(", ".join(missing)),
            scim_type="invalidValue",
        )
    # De-duplicate while preserving the requested order.
    return list(dict.fromkeys(resolved))


def security_profile_names_for_ids(profile_ids):
    """Resolve security profile ids to names for the SCIM ``entitlements`` value."""
    if not profile_ids:
        return []
    wanted = set(profile_ids)
    by_id = {
        summary["Id"]: summary["Name"]
        for summary in iter_security_profiles()
        if summary["Id"] in wanted
    }
    return [by_id[profile_id] for profile_id in profile_ids if profile_id in by_id]


def routing_profile_id_for_name(routing_profile_name):
    """Resolve a routing profile name to its id."""
    if not routing_profile_name:
        raise ScimError(
            400,
            "A routing profile is required to create an Amazon Connect user.",
            scim_type="invalidValue",
        )
    wanted = routing_profile_name.casefold()
    try:
        paginator = client().get_paginator("list_routing_profiles")
        for page in paginator.paginate(InstanceId=INSTANCE_ID):
            for summary in page.get("RoutingProfileSummaryList", []):
                if summary.get("Name", "").casefold() == wanted:
                    return summary["Id"]
    except botocore.exceptions.ClientError as error:
        _fail("ListRoutingProfiles", error)
    raise ScimError(
        400,
        f"Unknown Amazon Connect routing profile '{routing_profile_name}'.",
        scim_type="invalidValue",
    )


def users_with_security_profile(security_profile_id, limit=None):
    """Return users holding a security profile, newest search index state.

    ``SearchUsers`` is the only user API that returns ``SecurityProfileIds``
    inline, which keeps membership enumeration to one paginated sweep instead of
    a ``DescribeUser`` per user against a 2 requests-per-second quota.
    """
    members = []
    next_token = None
    try:
        while True:
            request = {
                "InstanceId": INSTANCE_ID,
                "MaxResults": 100,
                "SearchCriteria": {
                    "StringCondition": {
                        "FieldName": "SecurityProfileId",
                        "Value": security_profile_id,
                        "ComparisonType": "EXACT",
                    }
                },
            }
            if next_token:
                request["NextToken"] = next_token
            response = client().search_users(**request)
            for user in response.get("Users", []):
                members.append({"value": user["Id"], "display": user.get("Username", "")})
                if limit is not None and len(members) >= limit:
                    return members
            next_token = response.get("NextToken")
            if not next_token:
                break
    except botocore.exceptions.ClientError as error:
        _fail("SearchUsers", error)
    return members


# --------------------------------------------------------------------------
# Group membership changes
# --------------------------------------------------------------------------


class MembershipOutcome:
    """The confirmed result of applying membership changes to one group.

    ``members`` and ``non_members`` are derived from strongly consistent reads, so
    they can be trusted to correct a stale ``SearchUsers`` result.
    """

    def __init__(self):
        #: Users confirmed to hold the security profile afterwards.
        self.members = []
        #: Ids of users confirmed not to hold it afterwards.
        self.non_members = []
        #: Ids of users whose membership was actually written.
        self.applied = []
        #: Users deliberately left unchanged, with the reason.
        self.skipped = []


def apply_membership_changes(security_profile_id, add_ids, remove_ids):
    """Attach or detach one security profile across a set of users.

    Returns a :class:`MembershipOutcome`. Every decision here is made against a
    strongly consistent ``DescribeUser`` read, so the outcome records the true
    post-write membership of each user the request named. The caller needs that
    because the only way to enumerate a whole group is ``SearchUsers``, which is
    index-backed and lags a write by a few seconds -- reading it straight after a
    patch would report the change as not applied.

    Amazon Connect requires every user to keep at least one security profile, so
    a removal that would empty a user's profile set is reported as skipped and the
    user stays a member rather than the removal silently appearing to succeed.
    """
    changes = list(dict.fromkeys(add_ids)) + list(dict.fromkeys(remove_ids))
    if len(changes) > MAX_MEMBERSHIP_CHANGES:
        raise ScimError(
            400,
            f"A single group patch may change at most {MAX_MEMBERSHIP_CHANGES} "
            f"memberships; received {len(changes)}.",
            scim_type="tooMany",
        )

    outcome = MembershipOutcome()

    for user_id in dict.fromkeys(add_ids):
        user = _resolve_member(user_id, outcome)
        if user is None:
            continue
        current = list(user.get("SecurityProfileIds", []))
        member = {"value": user["Id"], "display": user.get("Username", "")}
        if security_profile_id in current:
            # Already a member; nothing to write, but it is still a member.
            outcome.members.append(member)
            continue
        set_user_security_profiles(user["Id"], current + [security_profile_id])
        outcome.applied.append(user["Id"])
        outcome.members.append(member)

    for user_id in dict.fromkeys(remove_ids):
        user = _resolve_member(user_id, outcome)
        if user is None:
            continue
        current = list(user.get("SecurityProfileIds", []))
        member = {"value": user["Id"], "display": user.get("Username", "")}
        if security_profile_id not in current:
            # Not a member to begin with.
            outcome.non_members.append(user["Id"])
            continue
        remaining = [profile for profile in current if profile != security_profile_id]
        if not remaining:
            LOGGER.warning(
                "Not removing security profile %s from Connect user %s: Amazon "
                "Connect requires each user to retain at least one security "
                "profile. The user keeps this profile and remains a group member.",
                security_profile_id,
                user["Id"],
            )
            outcome.skipped.append(
                {
                    "id": user["Id"],
                    "reason": "would leave the user with no security profile",
                }
            )
            outcome.members.append(member)
            continue
        set_user_security_profiles(user["Id"], remaining)
        outcome.applied.append(user["Id"])
        outcome.non_members.append(user["Id"])

    return outcome


def _resolve_member(user_id, outcome):
    """Fetch a member's authoritative record, recording unresolvable members."""
    try:
        return describe_user(user_id)
    except ScimError as error:
        if error.status == 404:
            LOGGER.warning(
                "Group member %s does not exist in Connect instance %s; skipping.",
                user_id,
                INSTANCE_ID,
            )
            outcome.skipped.append({"id": user_id, "reason": "user not found"})
            outcome.non_members.append(user_id)
            return None
        raise


def reconcile_members(searched, outcome):
    """Merge index-backed membership with the writes just confirmed.

    ``SearchUsers`` can be seconds behind a write, so a group read immediately
    after a patch would omit users just added and still list users just removed.
    Folding in the authoritative per-user results gives the caller the real
    post-write membership.
    """
    merged = {member["value"]: member for member in searched}
    for member in outcome.members:
        merged[member["value"]] = member
    for user_id in outcome.non_members:
        merged.pop(user_id, None)
    return list(merged.values())


# --------------------------------------------------------------------------
# SCIM representations
# --------------------------------------------------------------------------


def to_scim_user(user_summary, security_profile_ids=None, routing_profile_name=None):
    """Render an Amazon Connect user as a SCIM User resource."""
    entitlements = security_profile_names_for_ids(security_profile_ids or [])
    resource = {
        "schemas": [USER_SCHEMA, ENTERPRISE_USER_SCHEMA],
        "id": user_summary["Id"],
        "externalId": user_summary.get("Username", ""),
        "userName": user_summary.get("Username", ""),
        "active": True,
        "meta": {"resourceType": "User"},
        "entitlements": [{"value": name} for name in entitlements],
        "roles": [{"value": routing_profile_name}] if routing_profile_name else [],
    }
    last_modified = user_summary.get("LastModifiedTime")
    if last_modified is not None:
        resource["meta"]["lastModified"] = _iso(last_modified)
    return resource


def to_scim_group(profile_summary, members=None):
    """Render an Amazon Connect security profile as a SCIM Group resource."""
    resource = {
        "schemas": [GROUP_SCHEMA],
        "id": profile_summary["Id"],
        "displayName": profile_summary.get("Name")
        or profile_summary.get("SecurityProfileName", ""),
        "meta": {"resourceType": "Group"},
        "members": members or [],
    }
    last_modified = profile_summary.get("LastModifiedTime")
    if last_modified is not None:
        resource["meta"]["lastModified"] = _iso(last_modified)
    return resource


def _iso(timestamp):
    """Format a boto3 datetime as a SCIM/ISO 8601 UTC string."""
    try:
        return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    except AttributeError:
        return str(timestamp)
