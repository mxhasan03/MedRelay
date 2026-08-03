"""AppConfig for the dispatch app.

Assignment recommendations, job offers, and dispatcher overrides. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class DispatchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dispatch"
    label = "dispatch"
    verbose_name = "Dispatch"
