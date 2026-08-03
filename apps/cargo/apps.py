"""AppConfig for the cargo app.

Cargo classes, policies, and temperature profiles. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class CargoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cargo"
    label = "cargo"
    verbose_name = "Cargo"
