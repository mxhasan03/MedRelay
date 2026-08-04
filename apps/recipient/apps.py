"""AppConfig for the recipient app.

Phase 7 (docs/PRODUCT_REQUIREMENTS.md section 8 "Recipient experience"): a
short-lived, signed, anonymous tracking link for a delivery's recipient —
`GET/POST /recipient/<token>/`. New app, not an extension of
`apps.tracking` (which is Phase 5's *authenticated courier* GPS-ping
endpoint) — see `apps/recipient/tokens.py`'s module docstring for why these
are judged different enough concerns to warrant separate apps.
"""

from django.apps import AppConfig


class RecipientConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recipient"
    label = "recipient"
    verbose_name = "Recipient Tracking"
