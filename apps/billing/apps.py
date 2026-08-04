"""AppConfig for the billing app.

Phase 7: synthetic `Invoice`/`InvoiceLine` records generated from
`apps.deliveries`'s existing quote engine, a payment-status mock field (no
real payment processor), and CSV/HTML invoice export — see
`apps.billing.services` and docs/CURRENT_STATUS.md "Phase 7".
"""

from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    label = "billing"
    verbose_name = "Billing"
