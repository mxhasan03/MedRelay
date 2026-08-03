"""Django admin registrations for the custom user model and internal roles."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.accounts.models import InternalRoleAssignment, User


class InternalRoleAssignmentInline(admin.StackedInline):
    model = InternalRoleAssignment
    extra = 0
    max_num = 1
    can_delete = True


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Standard `UserAdmin` behavior, extended with the internal-staff flag/role."""

    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "is_internal_staff",
        "is_staff",
        "is_active",
    ]
    list_filter = [*DjangoUserAdmin.list_filter, "is_internal_staff"]
    search_fields = ["username", "email", "first_name", "last_name"]
    inlines = [InternalRoleAssignmentInline]
    fieldsets = (
        *(DjangoUserAdmin.fieldsets or ()),
        ("MedRelay", {"fields": ("is_internal_staff",)}),
    )


@admin.register(InternalRoleAssignment)
class InternalRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "created_at"]
    list_filter = ["role"]
    search_fields = ["user__username", "user__email"]
    autocomplete_fields = ["user"]
