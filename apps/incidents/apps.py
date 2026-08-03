"""AppConfig for the incidents app.

Incident reporting, categories, and resolutions. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.incidents"
    label = "incidents"
    verbose_name = "Incidents"
