"""Django admin registrations for notifications and the email/SMS/webhook
delivery logs. Everything here is read-only application data (created only
via `apps.notifications.services`), matching the append-only-log admin
convention already used for `IncidentAction`/`CustodyEvent`."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.notifications.models import (
    EmailLogEntry,
    Notification,
    SmsLogEntry,
    WebhookDelivery,
    WebhookEndpoint,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "recipient", "notification_type", "is_read", "created_at"]
    list_filter = ["notification_type", "is_read"]
    search_fields = ["recipient__username", "dedupe_key"]
    autocomplete_fields = ["recipient"]
    readonly_fields = ["id", "payload", "created_at", "read_at"]


@admin.register(EmailLogEntry)
class EmailLogEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "recipient", "notification_type", "success", "created_at"]
    list_filter = ["notification_type", "success", "mode"]
    search_fields = ["recipient__username", "correlation_id"]
    readonly_fields = [f.name for f in EmailLogEntry._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(SmsLogEntry)
class SmsLogEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "recipient_label", "notification_type", "success", "created_at"]
    list_filter = ["notification_type", "success", "mode"]
    search_fields = ["recipient__username", "recipient_label", "correlation_id"]
    readonly_fields = [f.name for f in SmsLogEntry._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ["id", "organization", "target_url", "is_active", "created_at"]
    list_filter = ["is_active"]
    autocomplete_fields = ["organization"]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["id", "endpoint", "notification_type", "simulated", "success", "attempted_at"]
    list_filter = ["notification_type", "simulated", "success"]
    readonly_fields = [f.name for f in WebhookDelivery._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
