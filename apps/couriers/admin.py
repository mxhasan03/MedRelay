"""Django admin registrations for courier profiles, credentials, training,
vehicles, equipment, cargo authorizations, and availability."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.couriers.models import (
    CargoAuthorization,
    CourierActionIdempotencyKey,
    CourierAvailability,
    CourierCredential,
    CourierProfile,
    Equipment,
    TrainingRecord,
    Vehicle,
)


class CourierCredentialInline(admin.TabularInline):
    model = CourierCredential
    extra = 0
    fields = ["credential_type", "status", "issued_on", "expires_on", "reviewed_by"]


class TrainingRecordInline(admin.TabularInline):
    model = TrainingRecord
    extra = 0
    fields = ["training_type", "completed_on", "expires_on"]


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0
    fields = ["vehicle_type", "plate_number", "supports_refrigeration", "is_active"]


class EquipmentInline(admin.TabularInline):
    model = Equipment
    extra = 0
    fields = ["equipment_type", "supports_refrigeration", "is_active"]


class CargoAuthorizationInline(admin.TabularInline):
    model = CargoAuthorization
    extra = 0
    fields = ["cargo_class", "supports_refrigeration", "is_active", "authorized_by"]
    autocomplete_fields = ["cargo_class", "authorized_by"]


class CourierAvailabilityInline(admin.StackedInline):
    model = CourierAvailability
    extra = 0
    max_num = 1
    fields = [
        "is_online",
        "shift_start",
        "shift_end",
        "current_service_zone",
        "max_concurrent_deliveries",
    ]


@admin.register(CourierProfile)
class CourierProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "status",
        "identity_review_status",
        "driver_license_status",
        "insurance_status",
        "home_service_zone",
    ]
    list_filter = ["status", "identity_review_status", "driver_license_status", "insurance_status"]
    search_fields = ["user__username", "user__email", "user__first_name", "user__last_name"]
    autocomplete_fields = ["user", "home_service_zone"]
    readonly_fields = ["applied_at", "created_at", "updated_at"]
    inlines = [
        CourierCredentialInline,
        TrainingRecordInline,
        VehicleInline,
        EquipmentInline,
        CargoAuthorizationInline,
        CourierAvailabilityInline,
    ]


@admin.register(CourierCredential)
class CourierCredentialAdmin(admin.ModelAdmin):
    list_display = ["courier", "credential_type", "status", "issued_on", "expires_on", "is_expired"]
    list_filter = ["credential_type", "status"]
    search_fields = ["courier__user__username"]
    autocomplete_fields = ["courier", "reviewed_by"]

    @admin.display(boolean=True, description="Expired")
    def is_expired(self, obj: CourierCredential) -> bool:
        return obj.is_expired


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = [
        "courier",
        "vehicle_type",
        "plate_number",
        "supports_refrigeration",
        "is_active",
    ]
    list_filter = ["vehicle_type", "supports_refrigeration", "is_active"]
    search_fields = ["courier__user__username", "plate_number"]
    autocomplete_fields = ["courier"]


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ["courier", "equipment_type", "supports_refrigeration", "is_active"]
    list_filter = ["equipment_type", "supports_refrigeration", "is_active"]
    search_fields = ["courier__user__username"]
    autocomplete_fields = ["courier"]


@admin.register(CargoAuthorization)
class CargoAuthorizationAdmin(admin.ModelAdmin):
    list_display = ["courier", "cargo_class", "supports_refrigeration", "is_active"]
    list_filter = ["cargo_class", "supports_refrigeration", "is_active"]
    search_fields = ["courier__user__username"]
    autocomplete_fields = ["courier", "cargo_class", "authorized_by"]


@admin.register(CourierAvailability)
class CourierAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["courier", "is_online", "current_service_zone", "max_concurrent_deliveries"]
    list_filter = ["is_online", "current_service_zone"]
    search_fields = ["courier__user__username"]
    autocomplete_fields = ["courier", "current_service_zone"]


@admin.register(CourierActionIdempotencyKey)
class CourierActionIdempotencyKeyAdmin(admin.ModelAdmin):
    """Read-only: these rows are an internal replay-protection record, not
    something an operator should ever hand-edit."""

    list_display = ["courier", "endpoint", "key", "status_code", "created_at"]
    list_filter = ["endpoint", "status_code"]
    search_fields = ["courier__user__username", "endpoint", "key"]
    autocomplete_fields = ["courier"]
    readonly_fields = ["courier", "endpoint", "key", "response_data", "status_code", "created_at"]

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False
