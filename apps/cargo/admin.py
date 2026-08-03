"""Django admin registrations for cargo classes, policies, packages, and attestations."""

from __future__ import annotations

from django.contrib import admin

from apps.cargo.models import (
    CargoClass,
    CargoPolicy,
    Package,
    PackageIdentifier,
    PackagingAttestation,
    TemperatureProfile,
)


class CargoPolicyInline(admin.StackedInline):
    model = CargoPolicy
    extra = 0
    max_num = 1


@admin.register(CargoClass)
class CargoClassAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]
    inlines = [CargoPolicyInline]


@admin.register(TemperatureProfile)
class TemperatureProfileAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]


class PackageIdentifierInline(admin.StackedInline):
    model = PackageIdentifier
    extra = 0
    max_num = 1
    readonly_fields = ["code", "created_at"]


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "sequence_number",
        "cargo_class",
        "temperature_profile",
        "approximate_weight_kg",
        "scanned_at",
    ]
    list_filter = ["cargo_class", "temperature_profile"]
    search_fields = ["delivery_request__id", "description"]
    autocomplete_fields = ["cargo_class", "temperature_profile", "scanned_by"]
    readonly_fields = ["scanned_at", "scanned_by"]
    inlines = [PackageIdentifierInline]


@admin.register(PackagingAttestation)
class PackagingAttestationAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "attested_by",
        "packaging_confirmed",
        "classification_confirmed",
        "attested_at",
    ]
    list_filter = ["packaging_confirmed", "classification_confirmed"]
    search_fields = ["delivery_request__id"]
    autocomplete_fields = ["attested_by"]
