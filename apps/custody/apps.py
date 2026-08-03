"""AppConfig for the custody app.

Append-only chain-of-custody events and proof of pickup/delivery. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class CustodyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.custody"
    label = "custody"
    verbose_name = "Custody"
