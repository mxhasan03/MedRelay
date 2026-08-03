"""Customer organizations and organization memberships (the multi-tenant boundary).

See docs/ARCHITECTURE_AND_DATA_MODEL.md section 2 ("Multi-tenancy") and
CLAUDE.md's "Architecture: modular Django monolith" section. Every
customer-owned entity elsewhere in the codebase (starting with
`apps.facilities.Facility`) must carry an `organization` foreign key and be
filtered through `apps.organizations.services`, never trusted directly from
a client-supplied ID.

See apps/accounts/models.py for the role-modeling write-up (why internal
operations roles are a separate model from `OrganizationMembership`).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class OrganizationType(models.TextChoices):
    """Customer organization types (docs/PRODUCT_REQUIREMENTS.md section 2)."""

    CLINIC = "clinic", "Clinic"
    URGENT_CARE = "urgent_care", "Urgent Care Center"
    DIAGNOSTIC_LAB = "diagnostic_lab", "Diagnostic Laboratory"
    PHARMACY = "pharmacy", "Pharmacy"
    HOSPITAL = "hospital", "Hospital / Health System"
    HOME_HEALTH = "home_health", "Home Health Organization"


class OrganizationQuerySet(models.QuerySet["Organization"]):
    def for_user(self, user: Any) -> Any:
        """Scope to organizations the given user may view.

        Deliberately delegates to `apps.organizations.services` (imported
        lazily here to avoid a module-level import cycle between this
        models module and the services module, which itself imports these
        models) so there is exactly one place that decides tenant
        visibility rules.
        """
        from apps.organizations.services import scope_organizations_to_user

        return scope_organizations_to_user(self, user)


class Organization(models.Model):
    """A customer organization (clinic, lab, pharmacy, hospital, etc.).

    This is the tenant root: every customer-owned entity elsewhere points
    back to an `Organization`, directly or transitively.
    """

    name = models.CharField(max_length=200, unique=True)
    org_type = models.CharField(max_length=32, choices=OrganizationType.choices)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(
        blank=True,
        help_text="Internal operational notes only. Never diagnosis/clinical/PHI content.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CustomerRole(models.TextChoices):
    """Customer organization roles (docs/PRODUCT_REQUIREMENTS.md section 4)."""

    OWNER = "owner", "Organization Owner"
    ADMINISTRATOR = "administrator", "Administrator"
    REQUESTER_DISPATCHER = "requester_dispatcher", "Requester / Dispatcher"
    BILLING_MANAGER = "billing_manager", "Billing Manager"
    COMPLIANCE_REVIEWER = "compliance_reviewer", "Compliance Reviewer"
    READ_ONLY_AUDITOR = "read_only_auditor", "Read-only Auditor"


# Roles that may manage (create/edit) their organization's profile,
# memberships, and facilities. Kept as a module-level constant so
# `apps.organizations.services` and tests share one definition.
ORG_MANAGING_ROLES = frozenset({CustomerRole.OWNER, CustomerRole.ADMINISTRATOR})


class OrganizationMembershipQuerySet(models.QuerySet["OrganizationMembership"]):
    def for_user(self, user: Any) -> Any:
        from apps.organizations.services import scope_queryset_to_user_orgs

        return scope_queryset_to_user_orgs(self, user)


class OrganizationMembership(models.Model):
    """Links a `User` to an `Organization` with a customer-org role."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=32, choices=CustomerRole.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationMembershipQuerySet.as_manager()

    class Meta:
        ordering = ["organization__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="unique_membership_per_user_per_org",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.organization} ({self.get_role_display()})"
