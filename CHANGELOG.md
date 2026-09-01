# Amazon Connect SCIM User Management Changelog

## [2.0.0] - 2026-09-01

Group membership never reached Amazon Connect, in any of the three deployments.
Every request to `/Groups` returned a fixed stub -- `totalResults: 1` with an
empty `Resources` array -- so an identity provider's group pushes and membership
changes were accepted and discarded. There was also no pagination: `itemsPerPage`
was hardcoded to 20, `totalResults` to 1, and `ListUsers` was called without
following `NextToken`, hiding every user past the first page.

The fixes are applied to all three deployments (`cdk_source/`,
`CloudFormation/`, `Terraform/`), which each package their own copy of the
handler code.

A note on the trigger: this was reported via Okta guidance about AWS retiring the
SCIM `PatchGroup` "Remove All" and "Replace" operations. That deprecation applies
to the **IAM Identity Center** SCIM endpoint. This solution is the SCIM server and
writes straight to the Amazon Connect user APIs, and Amazon Connect cannot use
Identity Center as an identity source (`SAML`, `CONNECT_MANAGED` and
`EXISTING_DIRECTORY` are the only options), so that deprecation never applied
here. The defects below were found in this codebase directly and verified against
a live Amazon Connect instance.

### Added

-   **SCIM `/Groups` endpoint.** An Amazon Connect security profile is now
    addressable as a SCIM Group whose members are the users holding that profile,
    so membership can be synchronised with `PATCH /Groups/{id}` instead of
    resending whole user records. `GET /Groups`, `GET /Groups/{id}`,
    `POST /Groups` (link an existing profile) and `PATCH`/`PUT /Groups/{id}` are
    supported. `DELETE /Groups/{id}` is refused, because deleting a security
    profile is an IAM-authorised action rather than a SCIM one.
-   **Pagination** per RFC 7644 section 3.4.2.4 -- `startIndex`/`count` in the
    request, a real `totalResults` in the response.
-   `DELETE /Users/{id}`, and SCIM-conformant error responses
    (`urn:ietf:params:scim:api:messages:2.0:Error` with `scimType`) in place of
    unhandled exceptions surfacing as HTTP 502.
-   Test suite: 204 Python tests and 35 CDK assertion tests, where there had been
    one commented-out stub. Two deliberate choices in there: the Amazon Connect
    fake models the `SearchUsers` index lag, because an instantly-consistent fake
    hides a real class of bug; and `tests/unit/test_handler_copies.py` compares
    every per-deployment copy of the handler modules against the canonical
    `cdk_source/lambdas` version and fails on any byte difference, so a fix cannot
    land in one deployment and be forgotten in the others.
-   cdk-nag acknowledgements with a written justification per rule. cdk-nag was
    already present but had no suppressions, so `cdk synth` reported findings with
    no recorded position on any of them.
-   API Gateway access logging, and a dedicated log group per Lambda with explicit
    retention.
-   A default security profile parameter for the profile assigned when the IdP
    sends no `entitlements`, in all three deployments, plus `api_token_length` in
    Terraform.

### Changed

-   **Lambda runtime `python3.9` → `python3.14`** in all three deployments.
    `python3.9` reached end of support on 15 December 2025.
-   **Group membership semantics.** All three `members` operations from RFC 7644
    section 3.5.2 are honoured. `add` and `remove` act on the named members; a
    `remove` carrying no value clears every current member, which is what an
    identity provider sends when a group is unassigned. A whole-collection
    `replace` is converted into the equivalent add/remove delta against current
    membership rather than emptying the group first, so no member is briefly
    dropped and a member that should stay is never rewritten.
-   `PATCH /Groups/{id}` returns the group's real post-write membership.
    Membership can only be enumerated with `SearchUsers`, which is index-backed and
    lags a write, so the strongly consistent per-user results are folded over the
    index result before responding.
-   **The Okta and Azure handlers now share one implementation** (`scim.py`,
    `connect_directory.py`, `handler_core.py`), with a thin per-provider entry
    point. They had drifted into separate ~380-line copies with different bugs.
-   **Node dependencies.** `aws-cdk-lib` 2.45.0 → 2.267.0, `aws-cdk` → 2.1139.0,
    `cdk-nag` ^2.21.61 → 3.0.2, TypeScript ~3.9.7 → 5.9.3, Jest 27 → 30,
    `ts-jest` 27 → 29, `@types/node` 10.17.27 → 26.4.0, `@types/jest` 27 → 30.
    Dropped `@types/prettier`, which existed only as an old `ts-jest` workaround.
    All versions are now exact rather than caret/tilde ranges. TypeScript 7 is not
    usable yet: `ts-jest` 29.4.12 declares a `typescript >=4.3 <7` peer range.
-   **Terraform providers.** `hashicorp/aws` ~> 4.30.0 → ~> 6.62,
    `hashicorp/random` ~> 3.4.3 → ~> 3.9, and a `required_version` floor. The 4.30
    provider did not recognise Lambda runtimes past `python3.9`, so the deprecated
    runtime could not be replaced without moving off it. Also replaced the
    deprecated `aws_region.name` attribute with `region`.
-   Deprecated CDK APIs replaced: `logRetention` → `logGroup`; `ManagedPolicy` ARNs
    built by hand → `fromAwsManagedPolicyName`; hand-built ARN strings →
    `Stack.formatArn`. The API Gateway stage moved to `deployOptions`, removing an
    ordering hazard where a new method could be left out of the deployment.
-   `dataTraceEnabled` is now `false`. It was `true` behind a "REMOVE AFTER
    TROUBLESHOOTING" comment, writing full request and response bodies -- including
    the bearer token -- to CloudWatch.
-   CloudFormation and Terraform IAM policies no longer hardcode the `aws`
    partition.
-   `connect_instance_id` is validated against a UUID pattern, and the token length
    is constrained to 32-256 in all three deployments.

### Fixed

-   **User lookup by `userName` never matched.** The handler extracted the username
    from the SCIM filter and then compared it against the Amazon Connect user
    *id*, so `userName eq` always reported "not found". Every sync therefore
    attempted to re-create existing users and failed on the duplicate. Lookups now
    resolve by id or username, case-insensitively.
-   **`PATCH /Users` crashed on one of the two payload shapes Okta sends.**
    `info['value']['active']` raised `TypeError` for
    `{"op":"replace","path":"active","value":false}`. Both shapes, and the
    stringified `"false"`/`"No"` variants, are now handled.
-   **Deactivation never happened via the Azure handler.** It required
    `value == 'No' and op == 'Replace' and path == 'active'`, a combination
    standard SCIM payloads never produce, so users were silently left active.
-   **`active: true` returned no response**, falling through every branch and
    returning `None`, which API Gateway surfaced as HTTP 502.
-   `UnboundLocalError`/`NameError` risks: `user_status` when `Operations` was
    empty, `user_list` when no filter branch matched, `get_exist_sg_id` when a user
    was absent from the first page of `list_users`, and
    `event.pathParameters.proxy` attribute access on a dict.
-   `GET /Users` with no query string raised `TypeError` dereferencing
    `queryStringParameters['filter']`.
-   An unknown security profile name in `entitlements` is now rejected with HTTP
    400 instead of being silently dropped, which had granted the user fewer
    permissions than the identity provider asked for.
-   A removal that would leave a user with no security profile is reported rather
    than attempted, since Amazon Connect requires at least one. The response shows
    the user still in the group.
-   `ServiceToken` is no longer passed inside custom resource `properties`, where
    CDK warned it would be overwritten.
-   The custom resource returns a result to the `Provider` framework instead of
    also `PUT`ing to `ResponseURL`, which raced the framework's own response.
-   Removed two always-true `CfnCondition`s that compared a synth-time context
    value, so CloudFormation reported `Fn::Equals` as always returning true.
-   The CloudFormation token-generator policy granted `ssm:DeleteParameters`
    (plural), which is not the API the handler calls.
-   README corrections: the routing profile default is set by the
    `defaultroutingprofile` parameter and the `DEFAULT_ROUTING_PROFILE` environment
    variable, not a `ROUTING_PROFILE` variable that no deployment reads.

### Security

-   **The authorizer logged the bearer token.** `LOGGER.info("Client token: " +
    event['authorizationToken'])` wrote the credential to CloudWatch on every
    request. Removed. The SCIM handler likewise logged the entire API Gateway
    event, whose `headers` carry the `Authorization` value and whose `body` carries
    user attributes; it now logs only routing metadata. The custom resource no
    longer logs its raw CloudFormation event either, which carries a pre-signed
    `ResponseURL`.
-   **API token generation is now cryptographically secure.** It used
    `random.sample` over a 36-character alphabet -- a non-CSPRNG, sampling
    *without* replacement, so no character repeated and any length above 36 raised.
    Now `secrets.choice` over 62 alphanumeric characters. Terraform moves from
    `random_string` (lowercase-only) to `random_password` over the same alphabet.
-   **The token is stored encrypted.** It was a plaintext `StringList` parameter; it
    is now a `SecureString` under a dedicated customer-managed KMS key with rotation
    enabled, in all three deployments. CloudFormation cannot create a `SecureString`
    parameter, so that deployment has its custom resource create it -- which also
    removes the window described next.
-   **Removed a window where the token was the literal string `default`.** The CDK
    and CloudFormation templates created the parameter with that placeholder and
    relied on the custom resource to overwrite it; if the custom resource failed,
    `default` was a valid bearer token. The parameter is now created only by the
    custom resource, so until it succeeds there is no token and the authorizer
    denies every request.
-   **Scoped the CDK authorizer's IAM policy from 24 actions to 2.** It granted
    `ssm:*`, `ec2messages:*`, `ds:CreateComputer`, `ds:DescribeDirectories`,
    `cloudwatch:PutMetricData`, `ec2:DescribeInstanceStatus`, the four
    `ssmmessages:*` channel actions and three `iam:*ServiceLinkedRole` actions,
    mostly on `Resource: "*"` -- an SSM managed-instance permission set the function
    never used. It now holds `ssm:GetParameter` on the one parameter and
    `kms:Decrypt` on the one key.
-   Token comparison is now constant-time (`secrets.compare_digest`), and the
    authorizer's returned policy is pinned to the API id and stage from
    `methodArn`, so a token cannot be replayed against another API in the account.
-   Dropped `connect:UpdateUserIdentityInfo` and `connect:DescribeSecurityProfile`
    from the provisioning role in all three deployments; the handler calls neither.
-   `npm audit`: 0 vulnerabilities. `cfn-lint` findings on the CloudFormation
    template: 6 → 3, with none introduced.

### Migration notes

-   **The Lambda packaging for CloudFormation and Terraform has changed.** The
    user-management function is no longer a single file; its zip must contain
    `user_management_lambda.py`, `handler_core.py`, `connect_directory.py` and
    `scim.py` at the archive root. A zip containing only the entry point fails at
    import with `No module named 'handler_core'`.
-   This release replaces the API Gateway stage and the token parameter, so
    redeploying issues a **new bearer token**. Read it with
    `aws ssm get-parameter --with-decryption --name /connect/scim-integration/api-token`
    and update the value in the identity provider's provisioning settings.
-   The `entitlements` attribute is returned as a multi-valued complex attribute
    (`[{"value": "Agent"}]`) rather than a list of plain strings. Both forms are
    accepted on input.
-   `POST /Users` now returns HTTP 201, and a deactivating `PATCH` returns 204.
-   There is no committed `package-lock.json`, so `npm ci` and `npm audit` are
    unavailable; use `npm install`, and `npm install --package-lock-only` first if
    you need an audit.
-   Deploying the CDK app with an `idp_type` other than `okta` or `azure` now fails
    at synth time instead of producing a Lambda whose handler cannot be imported.
-   Terraform requires `terraform init -upgrade` to pick up the new provider
    versions; the AWS provider 6.x line has its own upgrade notes.

## [1.0.0] - 2022-10-27

### Added

-   All files, initial version
