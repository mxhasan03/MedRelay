"""Django admin registrations for incidents, incident actions, and return
resolutions.

`IncidentAction` is read-only in the admin (append-only application data,
created only via `apps.incidents.services.add_incident_action`).
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.incidents.models import Incident, IncidentAction, ReturnResolution


class IncidentActionInline(admin.TabularInline):
    model = IncidentAction
    extra = 0
    fields = ["action_type", "note", "actor", "created_at"]
    readonly_fields = ["action_type", "note", "actor", "created_at"]
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "delivery_request",
        "category",
        "severity",
        "status",
        "placed_delivery_on_hold",
        "opened_at",
        "resolved_at",
    ]
    list_filter = ["category", "severity", "status", "placed_delivery_on_hold"]
    search_fields = ["delivery_request__id", "summary"]
    autocomplete_fields = ["delivery_request", "package", "opened_by", "resolved_by"]
    readonly_fields = ["opened_at", "delivery_status_before_hold"]
    inlines = [IncidentActionInline]


@admin.register(ReturnResolution)
class ReturnResolutionAdmin(admin.ModelAdmin):
    list_display = ["delivery_request", "status", "incident", "initiated_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["delivery_request__id", "reason"]
    autocomplete_fields = [
        "delivery_request",
        "incident",
        "return_facility",
        "initiated_by",
        "completed_by",
    ]
    readonly_fields = ["initiated_at", "completed_at"]
