from __future__ import annotations

from django.contrib import admin

from apps.audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "event_type", "actor_label", "organization", "summary")
    list_filter = ("event_type", "organization")
    search_fields = ("actor_label", "summary")
    date_hierarchy = "occurred_at"
    readonly_fields = (
        "event_type",
        "actor",
        "actor_label",
        "organization",
        "summary",
        "metadata",
        "occurred_at",
    )

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_change_permission(self, request: object, obj: object | None = None) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object | None = None) -> bool:
        return False
