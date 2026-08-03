"""Django admin registrations for facilities, contacts, receiving rules, and service zones."""

from __future__ import annotations

from django.contrib import admin

from apps.facilities.models import (
    Facility,
    FacilityContact,
    FacilityReceivingRule,
    ServiceZone,
)


class FacilityContactInline(admin.TabularInline):
    model = FacilityContact
    extra = 0
    fields = ["name", "title", "phone", "email", "is_primary"]


class FacilityReceivingRuleInline(admin.TabularInline):
    model = FacilityReceivingRule
    extra = 0
    fields = ["day_of_week", "is_closed", "opens_at", "closes_at", "same_day_cutoff_time"]


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organization",
        "facility_type",
        "borough",
        "is_active",
        "service_zone",
    ]
    list_filter = ["organization", "facility_type", "borough", "is_active", "service_zone"]
    search_fields = ["name", "organization__name", "address_line1", "postal_code"]
    autocomplete_fields = ["organization", "service_zone"]
    inlines = [FacilityContactInline, FacilityReceivingRuleInline]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(FacilityContact)
class FacilityContactAdmin(admin.ModelAdmin):
    list_display = ["name", "facility", "title", "phone", "email", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["name", "facility__name", "email"]
    autocomplete_fields = ["facility"]


@admin.register(FacilityReceivingRule)
class FacilityReceivingRuleAdmin(admin.ModelAdmin):
    list_display = ["facility", "day_of_week", "is_closed", "opens_at", "closes_at"]
    list_filter = ["day_of_week", "is_closed"]
    search_fields = ["facility__name"]
    autocomplete_fields = ["facility"]


@admin.register(ServiceZone)
class ServiceZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "borough", "is_active"]
    list_filter = ["borough", "is_active"]
    search_fields = ["name"]
