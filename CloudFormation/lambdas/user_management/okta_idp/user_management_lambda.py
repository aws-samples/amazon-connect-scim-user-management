"""Lambda entry point for provisioning Amazon Connect users from Okta.

This package is deployed as the ``user_management`` function for the CloudFormation
deployment of this solution. The SCIM behaviour lives in :mod:`handler_core`, and
``scim.py``, ``connect_directory.py`` and ``handler_core.py`` are byte-identical
copies of the canonical versions under ``cdk_source/lambdas/user_management``.
``tests/unit/test_handler_copies.py`` fails if any copy drifts.

The Amazon Connect routing profile arrives in the SCIM ``roles`` attribute.
"""

import handler_core


def lambda_handler(event, context):
    """Handle one SCIM request from the Okta provisioning application."""
    return handler_core.handle(event, provider="okta")
