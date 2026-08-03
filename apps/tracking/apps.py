"""AppConfig for the tracking app.

Phase 5: `CourierLocationPing` (browser Geolocation pings, tied to a
`DeliveryAssignment`) and the location-ping endpoint/terminal-state cutoff.
See `apps/tracking/models.py` and `apps/tracking/services.py`.
"""

from django.apps import AppConfig


class TrackingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tracking"
    label = "tracking"
    verbose_name = "Tracking"
