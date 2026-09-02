"""Lambda entry point for provisioning Amazon Connect users from Microsoft Entra ID.

This package is deployed as the ``user_management`` function for the CloudFormation
deployment of this solution. The SCIM behaviour lives in :mod:`handler_core`, and
``scim.py``, ``connect_directory.py`` and ``handler_core.py`` are byte-identical
copies of the canonical versions under ``cdk_source/lambdas/user_management``.
``tests/unit/test_handler_copies.py`` fails if any copy drifts.

Entra ID has no attribute mapping for the Amazon Connect routing profile, so
every user it creates gets the profile named by ``DEFAULT_ROUTING_PROFILE``.
"""

import handler_core


def lambda_handler(event, context):
    """Handle one SCIM request from the Microsoft Entra ID provisioning application."""
    return handler_core.handle(event, provider="azure")
