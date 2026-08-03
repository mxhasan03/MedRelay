"""AppConfig for the temperature app.

Temperature readings and excursion handling. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class TemperatureConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.temperature"
    label = "temperature"
    verbose_name = "Temperature"
