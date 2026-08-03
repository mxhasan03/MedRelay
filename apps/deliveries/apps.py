"""AppConfig for the deliveries app.

Delivery requests, stops, the delivery state machine, pricing/quotes, and
recurring routes — see apps/deliveries/models.py (Phase 2,
docs/IMPLEMENTATION_ROADMAP.md).
"""

from django.apps import AppConfig


class DeliveriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.deliveries"
    label = "deliveries"
    verbose_name = "Deliveries"
