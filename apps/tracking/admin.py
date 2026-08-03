"""Django admin registration for courier location pings."""

from __future__ import annotations

from django.contrib import admin

from apps.tracking.models import CourierLocationPing


@admin.register(CourierLocationPing)
class CourierLocationPingAdmin(admin.ModelAdmin):
    list_display = ["courier", "assignment", "latitude", "longitude", "recorded_at"]
    list_filter = ["recorded_at"]
    search_fields = ["courier__user__username", "assignment__id"]
    autocomplete_fields = ["assignment", "courier"]
    readonly_fields = ["recorded_at"]
