"""Lambda entry point for provisioning Amazon Connect users from Microsoft Entra ID.

The SCIM behaviour lives in :mod:`handler_core`. Entra ID has no attribute
mapping for the Amazon Connect routing profile, so every user it creates gets the
routing profile named by the ``DEFAULT_ROUTING_PROFILE`` environment variable.
"""

import handler_core


def lambda_handler(event, context):
    """Handle one SCIM request from the Entra ID provisioning application."""
    return handler_core.handle(event, provider="azure")
