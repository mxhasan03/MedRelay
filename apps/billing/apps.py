"""AppConfig for the billing app.

Synthetic pricing, quotes, and invoice records. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    label = "billing"
    verbose_name = "Billing"
