"""Django admin registration for the recipient-link access log (read-only —
created only via `apps.recipient.services.log_access`)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.recipient.models import RecipientLinkAccessLog


@admin.register(RecipientLinkAccessLog)
class RecipientLinkAccessLogAdmin(admin.ModelAdmin):
    list_display = ["id", "delivery_request", "outcome", "occurred_at"]
    list_filter = ["outcome"]
    readonly_fields = ["id", "delivery_request", "outcome", "occurred_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
