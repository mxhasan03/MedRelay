"""AppConfig for the reporting app.

Operational reports and CSV/HTML exports. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class ReportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    label = "reporting"
    verbose_name = "Reporting"
