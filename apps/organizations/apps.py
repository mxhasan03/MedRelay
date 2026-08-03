"""AppConfig for the organizations app.

Customer organizations and memberships (multi-tenant boundary), plus the
tenant-scoped query/permission helpers in apps/organizations/services.py.
Phase 1.
"""

from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"
    label = "organizations"
    verbose_name = "Organizations"
