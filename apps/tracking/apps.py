"""AppConfig for the tracking app.

Courier location pings and delivery tracking views. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class TrackingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tracking"
    label = "tracking"
    verbose_name = "Tracking"
