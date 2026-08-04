"""AppConfig for the reporting app.

Phase 7: tenant-scoped CSV/HTML report exports (delivery summary, custody
timeline, pickup/delivery proof, incident summary, on-time performance,
invoice summary), the `ExportJob` audit log (with a short dedup window —
see `apps.reporting.services`), and the operational-metrics dashboard.
"""

from django.apps import AppConfig


class ReportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    label = "reporting"
    verbose_name = "Reporting"
