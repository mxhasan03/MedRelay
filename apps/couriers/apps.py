"""AppConfig for the couriers app.

Courier profiles, credentials, vehicles, and eligibility data. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class CouriersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.couriers"
    label = "couriers"
    verbose_name = "Couriers"
