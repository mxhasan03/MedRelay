"""Django admin registrations for custody events and proof-of-pickup/delivery.

`CustodyEvent` is read-only in the admin (no add/change/delete) — it is
append-only application data, exactly like
`apps.deliveries.admin`'s treatment of `DeliveryStatusTransition`; the only
supported way to create one is `apps.custody.services.record_event`.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.custody.models import CustodyEvent, ProofOfDelivery, ProofOfPickup, RecipientVerification


@admin.register(CustodyEvent)
class CustodyEventAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "sequence",
        "event_type",
        "actor_type",
        "actor_user",
        "occurred_at",
        "recorded_at",
    ]
    list_filter = ["event_type", "actor_type"]
    search_fields = ["delivery_request__id", "actor_label"]
    autocomplete_fields = ["delivery_request", "package", "actor_user", "correction_of"]
    readonly_fields = [f.name for f in CustodyEvent._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(ProofOfPickup)
class ProofOfPickupAdmin(admin.ModelAdmin):
    list_display = ["delivery_request", "sender_name", "sender_role", "captured_by", "captured_at"]
    search_fields = ["delivery_request__id", "sender_name"]
    autocomplete_fields = ["delivery_request", "captured_by", "custody_event"]
    readonly_fields = ["captured_at"]


@admin.register(RecipientVerification)
class RecipientVerificationAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "method",
        "recipient_name",
        "pin_generated_at",
        "pin_verified_at",
    ]
    search_fields = ["delivery_request__id", "recipient_name"]
    autocomplete_fields = ["delivery_request", "verified_by"]
    readonly_fields = [
        "pin_hash",
        "pin_generated_at",
        "pin_verified_at",
        "created_at",
        "updated_at",
    ]


@admin.register(ProofOfDelivery)
class ProofOfDeliveryAdmin(admin.ModelAdmin):
    list_display = ["delivery_request", "delivered_to_name", "captured_by", "captured_at"]
    search_fields = ["delivery_request__id", "delivered_to_name"]
    autocomplete_fields = [
        "delivery_request",
        "recipient_verification",
        "captured_by",
        "custody_event",
    ]
    readonly_fields = ["captured_at"]
