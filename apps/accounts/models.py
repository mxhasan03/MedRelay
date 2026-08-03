"""Custom user model and internal-operations role assignment.

MedRelay is a synthetic-data-only prototype (see CLAUDE.md). This module
introduces the custom user model at the first point migrations exist for
any app, per docs/IMPLEMENTATION_ROADMAP.md Phase 1 — Django's own docs
strongly recommend starting a project with a custom user model rather than
swapping `AUTH_USER_MODEL` mid-project.

Role modeling decision (see docs/CURRENT_STATUS.md "Phase 1" section for the
full write-up): internal MedRelay operations staff (dispatcher, operations
manager, courier onboarding reviewer, compliance reviewer, customer support,
finance, system administrator — docs/PRODUCT_REQUIREMENTS.md section 4) are
modeled as a *separate* one-role-per-user assignment here in `apps.accounts`,
distinct from `apps.organizations.OrganizationMembership` (which models the
customer-organization roles: owner, administrator, requester/dispatcher,
billing manager, compliance reviewer, read-only auditor). Internal staff are
not scoped to a single customer organization the way a membership is — they
access customer organizations only through explicit permission checks in
`apps.organizations.services`, never implicitly by virtue of being staff.
Folding both role families into one polymorphic "membership" table (a
`membership_type` discriminator plus a nullable `organization` FK) was
considered and rejected: it would make it too easy for a future query to
treat an internal user's row as if it were scoped to one organization (e.g.
by forgetting to check `membership_type`), which directly risks the
cross-tenant isolation guarantee this project must hold per
docs/ARCHITECTURE_AND_DATA_MODEL.md section 2. Two small, explicit models are
safer than one implicit one.

Phase 3 (docs/CURRENT_STATUS.md "Phase 3" section) adds a third such flag,
`User.is_courier`, following the exact same pattern as `is_internal_staff`
above: couriers are `User` rows too (they need to log into a future courier
PWA, per docs/PRODUCT_REQUIREMENTS.md section 6), distinguished by a cheap,
index-friendly boolean kept in sync by `apps.couriers.models.CourierProfile.save()`,
with the real onboarding/eligibility data living in `apps.couriers` — never a
third parallel "membership" table. `is_courier` alone grants no access,
exactly like `is_internal_staff`.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """MedRelay's custom user model.

    Subclasses `AbstractUser` (not `AbstractBaseUser`) because nothing about
    this prototype needs a non-username-based identity scheme or custom
    manager logic — the standard Django username/password/email fields are
    sufficient, and subclassing `AbstractUser` keeps `django.contrib.admin`,
    `django.contrib.auth` forms/views, and DRF's session authentication
    working with zero extra code.
    """

    is_internal_staff = models.BooleanField(
        default=False,
        help_text=(
            "True for MedRelay internal operations staff (see InternalRoleAssignment). "
            "False for customer-organization users, whose roles live in "
            "apps.organizations.OrganizationMembership. This flag alone grants no access — "
            "see apps.organizations.services for the explicit permission checks."
        ),
    )
    is_courier = models.BooleanField(
        default=False,
        help_text=(
            "True for MedRelay couriers (see apps.couriers.CourierProfile). This flag alone "
            "grants no access and carries no onboarding/eligibility data itself — see "
            "apps.couriers.models and apps.couriers.eligibility."
        ),
    )

    def __str__(self) -> str:
        return self.get_full_name() or self.username


class InternalRole(models.TextChoices):
    """Internal MedRelay operations roles (docs/PRODUCT_REQUIREMENTS.md section 4)."""

    DISPATCHER = "dispatcher", "Dispatcher"
    OPERATIONS_MANAGER = "operations_manager", "Operations Manager"
    COURIER_ONBOARDING_REVIEWER = "courier_onboarding_reviewer", "Courier Onboarding Reviewer"
    COMPLIANCE_REVIEWER = "compliance_reviewer", "Compliance Reviewer"
    CUSTOMER_SUPPORT = "customer_support", "Customer Support"
    FINANCE = "finance", "Finance"
    SYSTEM_ADMINISTRATOR = "system_administrator", "System Administrator"


class InternalRoleAssignment(models.Model):
    """A MedRelay internal-operations role held by a staff user.

    Phase 1 models one internal role per user (`OneToOneField`). This is not
    an org membership: internal staff reach into customer-organization data
    only through the explicit cross-org permission checks in
    `apps.organizations.services`, never by default.
    """

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="internal_role_assignment",
    )
    role = models.CharField(max_length=32, choices=InternalRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]
        verbose_name = "Internal role assignment"
        verbose_name_plural = "Internal role assignments"

    def __str__(self) -> str:
        return f"{self.user} — {self.get_role_display()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Keep User.is_internal_staff in sync so it can be used as a cheap,
        # index-friendly filter without joining to this table. The
        # authoritative role data still lives here; `is_internal_staff`
        # itself grants no access (see module docstring).
        super().save(*args, **kwargs)
        if not self.user.is_internal_staff:
            self.user.is_internal_staff = True
            self.user.save(update_fields=["is_internal_staff"])
