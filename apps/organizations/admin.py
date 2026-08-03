"""Django admin registrations for organizations and memberships."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.organizations.models import Organization, OrganizationMembership


class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ["user"]
    fields = ["user", "role", "is_active"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "org_type", "is_active", "member_count", "created_at"]
    list_filter = ["org_type", "is_active"]
    search_fields = ["name"]
    inlines = [OrganizationMembershipInline]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Members")
    def member_count(self, obj: Organization) -> int:
        return obj.memberships.count()

    def get_queryset(self, request: HttpRequest) -> Any:
        return super().get_queryset(request).prefetch_related("memberships")


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "organization", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "organization"]
    search_fields = ["user__username", "user__email", "organization__name"]
    autocomplete_fields = ["user", "organization"]
