"""Lambda entry point for provisioning Amazon Connect users from Okta.

The SCIM behaviour lives in :mod:`handler_core`; this module only selects the
Okta payload conventions, the notable one being that the Amazon Connect routing
profile arrives in the SCIM ``roles`` attribute.
"""

import handler_core


def lambda_handler(event, context):
    """Handle one SCIM request from the Okta provisioning application."""
    return handler_core.handle(event, provider="okta")
