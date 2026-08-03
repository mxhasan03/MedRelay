"""AppConfig for the couriers app.

Courier profiles, credentials, training, vehicles, equipment, cargo
authorizations, availability, and the hard-eligibility engine (Phase 3 —
see docs/CURRENT_STATUS.md "Phase 3" section).
"""

from django.apps import AppConfig


class CouriersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.couriers"
    label = "couriers"
    verbose_name = "Couriers"
