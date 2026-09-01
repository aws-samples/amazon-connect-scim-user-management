"""Provider-agnostic SCIM 2.0 endpoint over an Amazon Connect instance.

``okta.py`` and ``azure.py`` are thin entry points onto this module; the SCIM
surface they expose is identical, and the small provider differences are handled
by normalising the payload shapes each one sends.

Endpoints
---------
==========================  ==================================================
``GET    /Users``           List/filter users (``userName``, ``externalId``, ``id``)
``GET    /Users/{id}``      Read one user
``POST   /Users``           Create a user
``PUT    /Users/{id}``      Replace a user's entitlements
``PATCH  /Users/{id}``      Toggle ``active``, update entitlements
``DELETE /Users/{id}``      Delete a user
``GET    /Groups``          List/filter groups (``displayName``, ``members.value``)
``GET    /Groups/{id}``     Read one group, with members
``POST   /Groups``          Link an existing security profile as a group
``PUT    /Groups/{id}``     Replace group membership
``PATCH  /Groups/{id}``     Add/remove/replace group members
==========================  ==================================================

Group membership is the part that was previously missing. Every request to
``/Groups`` used to return a fixed stub, so an identity provider's group pushes
and membership changes never reached Amazon Connect. All three ``members``
operations RFC 7644 defines are now honoured:

* ``add`` attaches the group's security profile to the named users.
* ``remove`` detaches it from the named users, or from every current member when
  the operation names none.
* ``replace`` is applied as the add/remove delta between current and requested
  membership, so the group is never emptied on the way to its new state and an
  unchanged member is never rewritten.
"""

import json
import logging

import connect_directory as directory
import scim

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def _request_summary(event, provider):
    """Describe a request for the log without copying anything sensitive into it.

    The raw proxy event must never be logged: ``headers`` carries the
    ``Authorization`` bearer token, and ``body`` carries user attributes such as
    names and email addresses. Only the routing metadata and the shape of the
    request go to CloudWatch.
    """
    parameters = scim.query_params(event)
    summary = {
        "provider": provider,
        "method": event.get("httpMethod"),
        "path": (event.get("pathParameters") or {}).get("Users"),
        "requestId": (event.get("requestContext") or {}).get("requestId"),
        # The filter names an attribute and a user/group identifier, both of which
        # are needed to debug provisioning and neither of which is a credential.
        "filter": parameters.get("filter"),
        "paginated": "startIndex" in parameters or "count" in parameters,
        "bodyBytes": len(event.get("body") or ""),
    }
    return {key: value for key, value in summary.items() if value not in (None, "")}


def handle(event, provider):
    """Route one API Gateway proxy event to the matching SCIM operation."""
    LOGGER.info("Received SCIM request: %s", json.dumps(_request_summary(event, provider)))
    try:
        return _route(event, provider)
    except scim.ScimError as error:
        LOGGER.warning(
            "Returning SCIM error %s (%s): %s",
            error.status,
            error.scim_type,
            error.detail,
        )
        return scim.error_response(error)
    except Exception:  # noqa: BLE001 - a SCIM endpoint must not return a bare 502.
        LOGGER.exception("Unhandled error while processing the SCIM request")
        return scim.error_response(
            scim.ScimError(500, "Internal error while processing the SCIM request.")
        )


def _route(event, provider):
    """Dispatch on HTTP method and resource, or fail explicitly."""
    method = (event.get("httpMethod") or "").upper()
    path_parameters = event.get("pathParameters") or {}
    # The API Gateway resource is '{Users+}', so the greedy path arrives under the
    # 'Users' key regardless of whether it addresses Users or Groups.
    raw_path = path_parameters.get("Users") or path_parameters.get("proxy") or ""
    resource, resource_id = scim.parse_path(raw_path)

    if resource == "Users":
        return _handle_users(event, method, resource_id, provider)
    if resource == "Groups":
        return _handle_groups(event, method, resource_id)
    raise scim.ScimError(
        404,
        f"Unsupported SCIM resource '{raw_path}'. This endpoint serves /Users and /Groups.",
    )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def _handle_users(event, method, user_id, provider):
    if method == "GET" and user_id:
        return _get_user(user_id)
    if method == "GET":
        return _list_users(event)
    if method == "POST":
        return _create_user(event, provider)
    if method in ("PUT", "PATCH"):
        return _update_user(event, method, user_id)
    if method == "DELETE":
        return _delete_user(user_id)
    raise scim.ScimError(405, f"Method {method} is not supported on /Users.")


def _get_user(identifier):
    summary = directory.find_user(identifier)
    if not summary:
        raise scim.ScimError(404, f"User '{identifier}' not found.")
    return scim.json_response(200, _user_resource(summary))


def _list_users(event):
    """List users, honouring a lookup filter and ``startIndex``/``count``."""
    parameters = scim.query_params(event)
    terms = scim.parse_filter(parameters.get("filter"))
    lookup = terms.get("username") or terms.get("externalid") or terms.get("id")

    if terms and not lookup:
        raise scim.ScimError(
            400,
            "Unsupported user filter attribute. Supported attributes are "
            "userName, externalId and id.",
            scim_type="invalidFilter",
        )

    if lookup:
        # A lookup resolves to at most one user, so it is inherently single-page.
        summary = directory.find_user(lookup)
        resources = [_user_resource(summary)] if summary else []
        return scim.json_response(
            200,
            scim.list_response(
                resources, len(resources), scim.start_index(event), scim.page_size(event)
            ),
        )

    size = scim.page_size(event)
    # A ListResponse has to report a real totalResults, which means counting the
    # full set before slicing the requested window out of it.
    all_summaries = list(directory.iter_users())
    start = scim.start_index(event)
    window = all_summaries[start - 1 : start - 1 + size]
    resources = [_user_resource(summary) for summary in window]
    return scim.json_response(200, scim.list_response(resources, len(all_summaries), start, size))


def _user_resource(summary):
    """Build a SCIM User for a summary, resolving its entitlements."""
    detail = directory.describe_user(summary["Id"])
    return directory.to_scim_user(
        {
            "Id": detail["Id"],
            "Username": detail.get("Username", ""),
            "LastModifiedTime": detail.get("LastModifiedTime"),
        },
        security_profile_ids=detail.get("SecurityProfileIds", []),
    )


def _create_user(event, provider):
    payload = _json_body(event)
    user_name = payload.get("userName")
    if not user_name:
        raise scim.ScimError(400, "'userName' is required.", scim_type="invalidValue")

    existing = directory.find_user(user_name)
    if existing:
        raise scim.ScimError(
            409,
            f"User '{user_name}' already exists in the Amazon Connect instance.",
            scim_type="uniqueness",
        )

    name = payload.get("name") or {}
    entitlements = _entitlement_names(payload)
    profile_ids = directory.security_profile_ids_for_names(entitlements)
    routing_profile_name = _routing_profile_name(payload, provider)
    routing_profile_id = directory.routing_profile_id_for_name(routing_profile_name)

    LOGGER.info(
        "Creating user %s with security profiles %s and routing profile '%s'",
        user_name,
        entitlements,
        routing_profile_name,
    )
    user_id = directory.create_user(
        user_name=user_name,
        first_name=name.get("givenName") or user_name,
        last_name=name.get("familyName") or user_name,
        security_profile_ids=profile_ids,
        routing_profile_id=routing_profile_id,
    )
    resource = directory.to_scim_user(
        {"Id": user_id, "Username": user_name},
        security_profile_ids=profile_ids,
        routing_profile_name=routing_profile_name,
    )
    if payload.get("externalId"):
        resource["externalId"] = payload["externalId"]
    return scim.json_response(201, resource)


def _update_user(event, method, user_id):
    """Apply a PUT replacement or PATCH modification to a user."""
    identifier = user_id or _identifier_from_filter(event)
    summary = directory.find_user(identifier)
    if not summary:
        raise scim.ScimError(404, f"User '{identifier}' not found.")
    resolved_id = summary["Id"]
    payload = _json_body(event)

    if method == "PATCH":
        operations = scim.parse_patch_operations(payload)
        active = scim.active_flag(operations)
        if active is False:
            LOGGER.info(
                "Deactivating Connect user %s. Amazon Connect has no disabled "
                "state, so the user is deleted.",
                resolved_id,
            )
            directory.delete_user(resolved_id)
            return scim.json_response(204)
        entitlements = _entitlements_from_operations(operations)
    else:
        entitlements = _entitlement_names(payload, required=False)
        if payload.get("active") is False:
            LOGGER.info(
                "PUT set active=false for Connect user %s; deleting the user.",
                resolved_id,
            )
            directory.delete_user(resolved_id)
            return scim.json_response(204)

    if entitlements is not None:
        profile_ids = directory.security_profile_ids_for_names(entitlements)
        if profile_ids:
            directory.set_user_security_profiles(resolved_id, profile_ids)
        else:
            LOGGER.warning(
                "Ignoring an empty entitlements set for Connect user %s: Amazon "
                "Connect requires at least one security profile per user.",
                resolved_id,
            )

    detail = directory.describe_user(resolved_id)
    return scim.json_response(
        200,
        directory.to_scim_user(
            {
                "Id": detail["Id"],
                "Username": detail.get("Username", ""),
                "LastModifiedTime": detail.get("LastModifiedTime"),
            },
            security_profile_ids=detail.get("SecurityProfileIds", []),
        ),
    )


def _delete_user(user_id):
    if not user_id:
        raise scim.ScimError(400, "A user id is required to delete a user.")
    summary = directory.find_user(user_id)
    if not summary:
        raise scim.ScimError(404, f"User '{user_id}' not found.")
    directory.delete_user(summary["Id"])
    return scim.json_response(204)


# --------------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------------


def _handle_groups(event, method, group_id):
    if method == "GET" and group_id:
        return _get_group(group_id)
    if method == "GET":
        return _list_groups(event)
    if method == "POST":
        return _link_group(event)
    if method in ("PATCH", "PUT"):
        return _patch_group(event, group_id)
    if method == "DELETE":
        raise scim.ScimError(
            403,
            "Deleting a group is not supported: a group maps to an Amazon Connect "
            "security profile, which must be managed by an authorised IAM "
            "principal rather than through SCIM.",
        )
    raise scim.ScimError(405, f"Method {method} is not supported on /Groups.")


def _get_group(identifier):
    profile = directory.find_security_profile(identifier)
    if not profile:
        raise scim.ScimError(404, f"Group '{identifier}' not found.")
    members = directory.users_with_security_profile(profile["Id"])
    return scim.json_response(200, directory.to_scim_group(profile, members))


def _list_groups(event):
    """List groups, honouring ``displayName``/``members.value`` filters."""
    parameters = scim.query_params(event)
    terms = scim.parse_filter(parameters.get("filter"))
    size = scim.page_size(event)

    display_name = terms.get("displayname")
    member_value = terms.get("members.value") or terms.get("members")

    if display_name:
        profile = directory.find_security_profile(display_name)
        # A list response reports an empty member list; membership is read
        # through GET /Groups/{id} or a members.value filter, so listing groups
        # costs one call rather than one SearchUsers per group.
        resources = [directory.to_scim_group(profile)] if profile else []
        return scim.json_response(200, _group_list_body(event, resources, size))

    if member_value:
        summary = directory.find_user(member_value)
        resources = []
        if summary:
            held = set(directory.describe_user(summary["Id"]).get("SecurityProfileIds", []))
            resources = [
                directory.to_scim_group(profile)
                for profile in directory.iter_security_profiles()
                if profile["Id"] in held
            ]
        return scim.json_response(200, _group_list_body(event, resources, size))

    if terms:
        raise scim.ScimError(
            400,
            "Unsupported group filter attribute. Supported attributes are "
            "displayName and members.value.",
            scim_type="invalidFilter",
        )

    all_profiles = list(directory.iter_security_profiles())
    start = scim.start_index(event)
    window = all_profiles[start - 1 : start - 1 + size]
    resources = [directory.to_scim_group(profile) for profile in window]
    return scim.json_response(200, scim.list_response(resources, len(all_profiles), start, size))


def _group_list_body(event, resources, size):
    """Wrap group resources in a ListResponse."""
    return scim.list_response(resources, len(resources), scim.start_index(event), size)


def _link_group(event):
    """Link an existing security profile to a pushed group.

    Creating a security profile grants Amazon Connect permissions, so it stays an
    IAM-authorised administrative action rather than something an identity
    provider can do over SCIM. A push for a profile that already exists succeeds
    and returns it, which is what lets Okta's Push Groups link to it.
    """
    payload = _json_body(event)
    display_name = payload.get("displayName")
    if not display_name:
        raise scim.ScimError(400, "'displayName' is required.", scim_type="invalidValue")
    profile = directory.find_security_profile(display_name)
    if not profile:
        raise scim.ScimError(
            400,
            f"No Amazon Connect security profile named '{display_name}' exists. Create the "
            "security profile in the instance first, then push this group.",
            scim_type="invalidValue",
        )
    members = directory.users_with_security_profile(profile["Id"])
    return scim.json_response(200, directory.to_scim_group(profile, members))


def _current_member_ids(profile_id):
    """Return the ids of the users currently holding a security profile."""
    return {member["value"] for member in directory.users_with_security_profile(profile_id)}


def _fold_member_operations(operations, profile_id):
    """Reduce group patch operations to one final intent per member.

    RFC 7644 applies operations in order, so the last operation naming a member
    decides its fate; folding to a single intent per member preserves that while
    still letting the changes be applied as one write per affected user.

    Returns a ``{member_id: "add"|"remove"}`` mapping, or ``None`` when no
    operation addressed membership at all.
    """
    intents = {}
    saw_member_operation = False
    # Read lazily and once: only remove-all and replace need current membership,
    # and it must reflect the state before any of these operations were applied.
    members_before = None

    for operation in operations:
        if not scim.targets_members(operation):
            # A displayName change would rename a security profile, which is an
            # IAM-authorised action; acknowledge it without acting.
            LOGGER.info(
                "Ignoring group patch operation on '%s'; only 'members' is actionable.",
                operation.get("path"),
            )
            continue
        saw_member_operation = True
        identifiers = scim.member_ids(operation)
        op = operation["op"]

        if op == "add":
            for member in identifiers:
                intents[member] = "add"
        elif op == "remove" and identifiers:
            for member in identifiers:
                intents[member] = "remove"
        else:
            # Remove-all and replace are both expressed against current
            # membership, so they share one read of it.
            if members_before is None:
                members_before = _current_member_ids(profile_id)
            _apply_collection_operation(intents, op, identifiers, members_before, profile_id)

    return intents if saw_member_operation else None


def _apply_collection_operation(intents, op, identifiers, members_before, profile_id):
    """Fold a whole-collection ``remove`` or ``replace`` into per-member intents."""
    if op == "remove":
        # RFC 7644 section 3.5.2.2: a remove with no value clears the attribute,
        # so this means "remove every current member". An identity provider sends
        # it when a group is unassigned, and rejecting it would stall the sync.
        LOGGER.info(
            "Removing all %d current member(s) from group %s.",
            len(members_before),
            profile_id,
        )
        for member in sorted(members_before):
            intents[member] = "remove"
        return

    wanted = set(identifiers)
    LOGGER.info(
        "Converting a whole-collection members replace on group %s into a delta: "
        "%d current, %d requested.",
        profile_id,
        len(members_before),
        len(wanted),
    )
    for member in sorted(wanted - members_before):
        intents[member] = "add"
    for member in sorted(members_before - wanted):
        intents[member] = "remove"
    # Members that should stay need no write, and a replace supersedes any earlier
    # operation that named them.
    for member in members_before & wanted:
        intents.pop(member, None)


def _patch_group(event, group_id):
    """Apply group membership changes to the group's security profile.

    Granular ``add``/``remove`` operations are applied directly. A whole
    collection ``replace`` is turned into the equivalent add/remove delta against
    current membership rather than clearing the group first, so no member is
    briefly dropped and a member that should stay is never rewritten.
    """
    identifier = group_id or _identifier_from_filter(event)
    profile = directory.find_security_profile(identifier)
    if not profile:
        raise scim.ScimError(404, f"Group '{identifier}' not found.")
    profile_id = profile["Id"]

    payload = _json_body(event)
    operations = scim.parse_patch_operations(payload)
    intents = _fold_member_operations(operations, profile_id)

    if intents is None:
        members = directory.users_with_security_profile(profile_id)
        return scim.json_response(200, directory.to_scim_group(profile, members))

    add_ids = [member for member, intent in intents.items() if intent == "add"]
    remove_ids = [member for member, intent in intents.items() if intent == "remove"]

    outcome = directory.apply_membership_changes(profile_id, add_ids, remove_ids)
    LOGGER.info(
        "Group %s membership patch written for %d user(s); %d skipped: %s",
        profile_id,
        len(outcome.applied),
        len(outcome.skipped),
        outcome.skipped,
    )

    # Report the group as it actually is now, so a partially applied change is
    # visible to the provider instead of being reported as a clean success.
    # SearchUsers alone would not do: it is index-backed and lags the writes just
    # made, so the confirmed per-user results are folded back over it.
    members = directory.reconcile_members(
        directory.users_with_security_profile(profile_id), outcome
    )
    return scim.json_response(200, directory.to_scim_group(profile, members))


# --------------------------------------------------------------------------
# Payload normalisation
# --------------------------------------------------------------------------


def _json_body(event):
    """Parse the request body, tolerating an absent one."""
    body = event.get("body")
    if body in (None, ""):
        return {}
    if isinstance(body, dict):
        return body
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise scim.ScimError(
            400, "Request body is not valid JSON.", scim_type="invalidSyntax"
        ) from exc
    if not isinstance(parsed, dict):
        raise scim.ScimError(400, "Request body must be a JSON object.", scim_type="invalidSyntax")
    return parsed


def _multi_valued(raw):
    """Normalise a SCIM multi-valued attribute to a list of plain strings.

    Providers send these either as plain strings (``["Agent"]``) or as complex
    values (``[{"value": "Agent"}]``); both have to be accepted.
    """
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return [raw]
    values = []
    for item in raw if isinstance(raw, list) else [raw]:
        if isinstance(item, dict):
            value = item.get("value") or item.get("display")
            if value:
                values.append(str(value))
        elif item:
            values.append(str(item))
    return values


def _entitlement_names(payload, required=True):
    """Resolve the security profile names a payload asks for."""
    if "entitlements" not in payload:
        if not required:
            return None
        LOGGER.info(
            "No 'entitlements' in the payload; falling back to the default security profile '%s'.",
            directory.DEFAULT_SECURITY_PROFILE,
        )
        return [directory.DEFAULT_SECURITY_PROFILE]
    names = _multi_valued(payload.get("entitlements"))
    if not names:
        if not required:
            return []
        return [directory.DEFAULT_SECURITY_PROFILE]
    return names


def _entitlements_from_operations(operations):
    """Pull an entitlements replacement out of a user PatchOp, if it carries one."""
    resolved = None
    for operation in operations:
        path = (operation.get("path") or "").strip().lower()
        value = operation.get("value")
        if path.startswith("entitlements"):
            resolved = _multi_valued(value)
        elif isinstance(value, dict):
            for key, candidate in value.items():
                if key.lower() == "entitlements":
                    resolved = _multi_valued(candidate)
    return resolved


def _routing_profile_name(payload, provider):
    """Resolve the routing profile for a new user.

    The Okta application maps the routing profile onto the SCIM ``roles``
    attribute; the Azure application has no equivalent mapping and always uses
    the configured default.
    """
    if provider == "okta":
        roles = _multi_valued(payload.get("roles"))
        if roles:
            return roles[0]
    return directory.DEFAULT_ROUTING_PROFILE


def _identifier_from_filter(event):
    """Extract a resource identifier from a filter when the path carries none."""
    terms = scim.parse_filter(scim.query_params(event).get("filter"))
    identifier = (
        terms.get("username")
        or terms.get("externalid")
        or terms.get("displayname")
        or terms.get("id")
    )
    if not identifier:
        raise scim.ScimError(
            400,
            "The request must address a resource by path id or by an 'eq' filter.",
            scim_type="invalidValue",
        )
    return identifier
