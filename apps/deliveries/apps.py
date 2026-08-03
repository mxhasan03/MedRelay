"""AppConfig for the deliveries app.

Delivery requests, stops, and the delivery state machine. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class DeliveriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.deliveries"
    label = "deliveries"
    verbose_name = "Deliveries"
