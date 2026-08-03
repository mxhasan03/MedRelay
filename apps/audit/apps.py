"""AppConfig for the audit app.

Append-only audit events and the zero-cost dependency audit command.
"""

from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit"
