"""Django admin registrations for simulated temperature readings/excursions."""

from __future__ import annotations

from django.contrib import admin

from apps.temperature.models import TemperatureExcursion, TemperatureReading


@admin.register(TemperatureReading)
class TemperatureReadingAdmin(admin.ModelAdmin):
    list_display = ["delivery_request", "package", "temperature_c", "recorded_at", "source"]
    list_filter = ["source"]
    search_fields = ["delivery_request__id"]
    autocomplete_fields = ["delivery_request", "package", "recorded_by"]
    readonly_fields = ["created_at"]


@admin.register(TemperatureExcursion)
class TemperatureExcursionAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "package",
        "temperature_c",
        "threshold_min_c",
        "threshold_max_c",
        "incident",
        "detected_at",
    ]
    search_fields = ["delivery_request__id"]
    autocomplete_fields = ["delivery_request", "package", "reading", "incident"]
    readonly_fields = ["detected_at"]
