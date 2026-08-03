"""Django admin registrations for dispatch recommendations, offers,
assignments, overrides, route plans, and SLA profiles."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.dispatch.models import (
    DeliveryAssignment,
    DispatchOverride,
    DispatchRecommendation,
    DispatchRecommendationCandidate,
    JobOffer,
    RouteLeg,
    RoutePlan,
    SLAProfile,
)


class DispatchRecommendationCandidateInline(admin.TabularInline):
    """Read-only: a recommendation run's candidate rows are an audit
    snapshot, not editable data."""

    model = DispatchRecommendationCandidate
    extra = 0
    fields = [
        "rank",
        "courier",
        "eligible",
        "total_score",
        "sla_feasibility",
        "eta_to_pickup_minutes",
    ]
    readonly_fields = [
        "rank",
        "courier",
        "eligible",
        "total_score",
        "sla_feasibility",
        "eta_to_pickup_minutes",
    ]
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(DispatchRecommendation)
class DispatchRecommendationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "delivery_request",
        "computed_at",
        "computed_by",
        "candidate_count",
        "eligible_count",
    ]
    list_filter = ["computed_at"]
    search_fields = ["delivery_request__id"]
    autocomplete_fields = ["delivery_request", "computed_by"]
    readonly_fields = ["id", "computed_at"]
    inlines = [DispatchRecommendationCandidateInline]


@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "courier",
        "status",
        "offered_at",
        "expires_at",
        "responded_at",
    ]
    list_filter = ["status"]
    search_fields = ["delivery_request__id", "courier__user__username", "decline_reason"]
    autocomplete_fields = ["delivery_request", "courier", "created_by"]
    readonly_fields = ["offered_at", "responded_at"]


@admin.register(DeliveryAssignment)
class DeliveryAssignmentAdmin(admin.ModelAdmin):
    list_display = ["delivery_request", "courier", "status", "assigned_at", "unassigned_at"]
    list_filter = ["status"]
    search_fields = ["delivery_request__id", "courier__user__username"]
    autocomplete_fields = ["delivery_request", "courier", "assigned_by"]
    readonly_fields = ["assigned_at", "created_at", "updated_at"]


@admin.register(DispatchOverride)
class DispatchOverrideAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "override_type",
        "chosen_courier",
        "previous_courier",
        "actor",
        "created_at",
    ]
    list_filter = ["override_type"]
    search_fields = ["delivery_request__id", "reason"]
    autocomplete_fields = [
        "delivery_request",
        "actor",
        "chosen_courier",
        "previous_courier",
        "recommendation",
    ]
    readonly_fields = ["created_at"]


class RouteLegInline(admin.TabularInline):
    model = RouteLeg
    extra = 0
    autocomplete_fields = ["from_facility", "to_facility"]


@admin.register(RoutePlan)
class RoutePlanAdmin(admin.ModelAdmin):
    list_display = [
        "delivery_request",
        "total_distance_km",
        "total_duration_minutes",
        "computed_at",
    ]
    search_fields = ["delivery_request__id"]
    autocomplete_fields = ["delivery_request", "assignment"]
    readonly_fields = ["computed_at"]
    inlines = [RouteLegInline]


@admin.register(SLAProfile)
class SLAProfileAdmin(admin.ModelAdmin):
    list_display = ["service_level", "min_slack_minutes", "description", "updated_at"]
    search_fields = ["service_level", "description"]
