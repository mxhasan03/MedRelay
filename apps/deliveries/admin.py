"""Django admin registrations for delivery requests, pricing, and recurring routes."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.deliveries.models import (
    DeliveryRequest,
    DeliveryStatusTransition,
    DeliveryStop,
    PricingRule,
    Quote,
    RecurringRoute,
    RecurringRouteStop,
)


class DeliveryStopInline(admin.TabularInline):
    model = DeliveryStop
    extra = 0
    autocomplete_fields = ["facility"]


class DeliveryStatusTransitionInline(admin.TabularInline):
    """Read-only: `DeliveryStatusTransition` is append-only (see its model docstring) —
    the admin must not offer add/edit/delete on existing rows."""

    model = DeliveryStatusTransition
    extra = 0
    fields = ["from_status", "to_status", "actor", "reason", "occurred_at"]
    readonly_fields = ["from_status", "to_status", "actor", "reason", "occurred_at"]
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class QuoteInline(admin.StackedInline):
    model = Quote
    extra = 0
    max_num = 1
    readonly_fields = [
        "base_fee",
        "distance_km",
        "distance_time_fee",
        "service_level_surcharge",
        "cargo_equipment_surcharge",
        "toll_estimate",
        "wait_time_fee",
        "after_hours_surcharge",
        "return_trip_fee",
        "total_price",
        "computed_at",
    ]

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(DeliveryRequest)
class DeliveryRequestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "organization",
        "service_level",
        "status",
        "cargo_class",
        "temperature_profile",
        "estimated_price",
        "created_at",
    ]
    list_filter = ["status", "service_level", "cargo_class", "temperature_profile"]
    search_fields = ["id", "organization__name", "sender_contact_name", "recipient_contact_name"]
    autocomplete_fields = ["organization", "created_by", "cargo_class", "temperature_profile"]
    readonly_fields = ["id", "version", "created_at", "updated_at"]
    inlines = [DeliveryStopInline, QuoteInline, DeliveryStatusTransitionInline]


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = ["key", "amount", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["key", "description"]


class RecurringRouteStopInline(admin.TabularInline):
    model = RecurringRouteStop
    extra = 0
    autocomplete_fields = ["facility"]


@admin.register(RecurringRoute)
class RecurringRouteAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organization",
        "frequency",
        "service_level",
        "is_approved",
        "is_paused",
        "start_date",
        "end_date",
    ]
    list_filter = ["frequency", "service_level", "is_approved", "is_paused"]
    search_fields = ["name", "organization__name"]
    autocomplete_fields = ["organization", "cargo_class", "temperature_profile", "created_by"]
    inlines = [RecurringRouteStopInline]
