"""AppConfig for the organizations app.

Customer organizations and memberships (multi-tenant boundary). No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"
    label = "organizations"
    verbose_name = "Organizations"
