"""Django admin registration for the export-job audit log (read-only —
created only via `apps.reporting.services.get_or_create_export_job`)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.reporting.models import ExportJob


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "organization",
        "report_type",
        "export_format",
        "requested_by",
        "requested_at",
    ]
    list_filter = ["report_type", "export_format"]
    search_fields = ["organization__name"]
    autocomplete_fields = ["organization", "requested_by"]
    readonly_fields = [f.name for f in ExportJob._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
