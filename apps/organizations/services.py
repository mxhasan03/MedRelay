"""Tenant-scoped query and permission helpers — the single source of truth
for "which organizations can this user see/manage."

Per docs/ARCHITECTURE_AND_DATA_MODEL.md section 2 and CLAUDE.md's
multi-tenancy rules:

- shared database, explicit `organization_id` scoping on every
  customer-owned entity
- no organization ID accepted blindly from a client without a permission
  check
- internal operations staff access multiple organizations only through
  *explicit* permission checks — `User.is_internal_staff` alone never grants
  implicit access to everything

Every app that owns organization-scoped data should filter its querysets
through `scope_queryset_to_user_orgs` (or a thin per-model `for_user()"
QuerySet method that delegates to it, as `Organization` and
`OrganizationMembership` do in `apps.organizations.models`, and as
`apps.facilities.models.Facility` does) rather than re-implementing tenant
scoping ad hoc.
"""

from __future__ import annotations

from typing import TypeVar

from django.contrib.auth.models import AnonymousUser
from django.db.models import Model, QuerySet

from apps.accounts.models import InternalRole, User
from apps.organizations.models import (
    ORG_MANAGING_ROLES,
    CustomerRole,
    Organization,
    OrganizationMembership,
)

_T = TypeVar("_T", bound=Model)

# `request.user` is always one of these two types (authenticated custom
# `User`, or `AnonymousUser` when not logged in) — every function below
# accepts both and treats "not authenticated" as "no access", never raising
# on an anonymous caller. `isinstance(user, AnonymousUser)` checks below
# double as mypy type-narrowing to plain `User` for the rest of each
# function body (not just a runtime check).
type AnyUser = User | AnonymousUser

# Internal ops roles explicitly granted cross-organization *read* access.
# Being `user.is_internal_staff` grants nothing by itself — access always
# goes through one of these named checks.
CROSS_ORG_READ_ROLES = frozenset(
    {
        InternalRole.OPERATIONS_MANAGER,
        InternalRole.SYSTEM_ADMINISTRATOR,
        InternalRole.CUSTOMER_SUPPORT,
        InternalRole.COMPLIANCE_REVIEWER,
        InternalRole.FINANCE,
        InternalRole.DISPATCHER,
    }
)
# Deliberately excluded: `courier_onboarding_reviewer` — that role's work
# (reviewing courier applications/credentials) has no need to view customer
# organizations or facilities, so it gets no cross-org grant here at all,
# per the "explicit checks, not by default" rule.

# Internal ops roles explicitly granted cross-organization *manage*
# (create/edit membership, create/edit facilities) access. Deliberately a
# much smaller set than the read set.
CROSS_ORG_MANAGE_ROLES = frozenset(
    {
        InternalRole.OPERATIONS_MANAGER,
        InternalRole.SYSTEM_ADMINISTRATOR,
    }
)


def get_member_organization_ids(user: AnyUser) -> set[int]:
    """Organization IDs the user belongs to as a customer-org member."""
    if isinstance(user, AnonymousUser) or not user.is_authenticated:
        return set()
    return set(
        OrganizationMembership.objects.filter(user=user, is_active=True).values_list(
            "organization_id", flat=True
        )
    )


def get_internal_role(user: AnyUser) -> str | None:
    """The user's internal-ops role, if any (`None` for customer-org users)."""
    is_authenticated = getattr(user, "is_authenticated", False)
    is_internal_staff = getattr(user, "is_internal_staff", False)
    if not is_authenticated or not is_internal_staff:
        return None
    assignment = getattr(user, "internal_role_assignment", None)
    return assignment.role if assignment is not None else None


def has_cross_org_read_access(user: AnyUser) -> bool:
    """True if the user's internal role is explicitly allow-listed for cross-org reads."""
    return get_internal_role(user) in CROSS_ORG_READ_ROLES


def has_cross_org_manage_access(user: AnyUser) -> bool:
    """True if the user's internal role is explicitly allow-listed for cross-org management."""
    return get_internal_role(user) in CROSS_ORG_MANAGE_ROLES


def get_org_role(user: AnyUser, organization_id: int) -> str | None:
    """The user's `CustomerRole` within a specific organization, if a member."""
    if isinstance(user, AnonymousUser) or not user.is_authenticated:
        return None
    membership = (
        OrganizationMembership.objects.filter(
            user=user, organization_id=organization_id, is_active=True
        )
        .only("role")
        .first()
    )
    return membership.role if membership else None


def can_view_organization(user: AnyUser, organization_id: int) -> bool:
    """Can the user view this organization and its facilities?"""
    if get_org_role(user, organization_id) is not None:
        return True
    return has_cross_org_read_access(user)


def can_manage_organization(user: AnyUser, organization_id: int) -> bool:
    """Can the user manage (edit profile, manage memberships/facilities for) this organization?"""
    if get_org_role(user, organization_id) in ORG_MANAGING_ROLES:
        return True
    return has_cross_org_manage_access(user)


# Facility management currently uses the same rule as organization
# management. Kept as a distinct name (rather than an alias used directly)
# so a later phase can give facility management its own rule — e.g. letting
# `requester_dispatcher` manage facilities without full org-admin rights —
# without touching call sites that already say "facilities".
can_manage_facilities = can_manage_organization

# Customer-org roles allowed to create delivery requests for their own
# organization (apps.deliveries, Phase 2). Deliberately broader than
# `ORG_MANAGING_ROLES`: "requester/dispatcher" is the role
# docs/PRODUCT_REQUIREMENTS.md section 4 names explicitly for this job, and
# should not need full org-admin rights just to submit a delivery request.
DELIVERY_REQUEST_CREATOR_ROLES = frozenset(
    {CustomerRole.OWNER, CustomerRole.ADMINISTRATOR, CustomerRole.REQUESTER_DISPATCHER}
)


def can_create_delivery_requests(user: AnyUser, organization_id: int) -> bool:
    """Can the user create delivery requests on behalf of this organization?"""
    if get_org_role(user, organization_id) in DELIVERY_REQUEST_CREATOR_ROLES:
        return True
    return has_cross_org_manage_access(user)


# Internal ops roles allowed to use the dispatch board (apps.dispatch, Phase
# 4): recommend/assign/reassign/offer deliveries. This is cross-organization
# by nature (a dispatcher works across every customer organization's open
# deliveries), so it is its own explicit allowlist rather than reusing
# CROSS_ORG_MANAGE_ROLES — `customer_support`/`compliance_reviewer`/`finance`
# have cross-org *read* access for their own reasons but no business
# assigning couriers to deliveries.
DISPATCH_ROLES = frozenset(
    {
        InternalRole.DISPATCHER,
        InternalRole.OPERATIONS_MANAGER,
        InternalRole.SYSTEM_ADMINISTRATOR,
    }
)


def can_dispatch(user: AnyUser) -> bool:
    """Can the user view/act on the dispatch board (recommend, assign, offer,
    reassign)?"""
    return get_internal_role(user) in DISPATCH_ROLES


# Customer-org roles allowed to view their own organization's invoices
# (apps.billing, Phase 7). Deliberately narrower than "any member" — billing
# figures are exactly the kind of data docs/PRODUCT_REQUIREMENTS.md section 4
# names a dedicated "billing manager" role for.
BILLING_ROLES = frozenset(
    {CustomerRole.OWNER, CustomerRole.ADMINISTRATOR, CustomerRole.BILLING_MANAGER}
)

# Internal ops roles allowed to generate invoices / change payment status for
# any organization. `finance` gets this even though it is not in
# CROSS_ORG_MANAGE_ROLES (which is deliberately a much smaller set for
# organization/facility management) — invoicing is finance's own job, not a
# general org-management capability.
CROSS_ORG_BILLING_MANAGE_ROLES = frozenset(
    {InternalRole.FINANCE, InternalRole.OPERATIONS_MANAGER, InternalRole.SYSTEM_ADMINISTRATOR}
)


def can_view_billing(user: AnyUser, organization_id: int) -> bool:
    """Can the user view this organization's invoices?"""
    if get_org_role(user, organization_id) in BILLING_ROLES:
        return True
    return has_cross_org_read_access(user)


def can_manage_billing(user: AnyUser, organization_id: int) -> bool:
    """Can the user generate invoices / change payment status for this
    organization's deliveries?"""
    if get_org_role(user, organization_id) in BILLING_ROLES:
        return True
    return get_internal_role(user) in CROSS_ORG_BILLING_MANAGE_ROLES


def can_export_reports(user: AnyUser, organization_id: int) -> bool:
    """Can the user request/download a report export scoped to this
    organization? Any org member (any role) may export their own
    organization's operational reports; cross-org read access covers
    internal ops the same way it covers ordinary organization viewing."""
    return can_view_organization(user, organization_id)


def scope_queryset_to_user_orgs(
    queryset: QuerySet[_T], user: AnyUser, *, org_field: str = "organization_id"
) -> QuerySet[_T]:
    """Generic tenant-scoping filter for any organization-owned queryset.

    `org_field` is the ORM lookup path from the queryset's model to an
    organization ID — `"organization_id"` for models with a direct FK (e.g.
    `Facility`), or a traversal like `"facility__organization_id"` for
    models owned one level down (e.g. `FacilityContact`).
    """
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if has_cross_org_read_access(user):
        return queryset
    member_org_ids = get_member_organization_ids(user)
    return queryset.filter(**{f"{org_field}__in": member_org_ids})


def scope_organizations_to_user(
    queryset: QuerySet[Organization], user: AnyUser
) -> QuerySet[Organization]:
    """Tenant-scoping filter for the `Organization` model itself (`org_field="id"`)."""
    return scope_queryset_to_user_orgs(queryset, user, org_field="id")


def organizations_for_user(user: AnyUser) -> QuerySet[Organization]:
    """Convenience wrapper: `Organization.objects.for_user(user)`."""
    return Organization.objects.for_user(user)


# Internal ops roles allowed to view the audit viewer (apps.audit, Phase 8).
# Scoped narrowly to the roles docs/PRODUCT_REQUIREMENTS.md section 4 and
# docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 6 actually name for this
# job — "compliance reviewer" explicitly, plus the two roles that already
# hold the broadest cross-org management access. Deliberately excludes
# `customer_support`/`finance`/`dispatcher`, which have cross-org *read*
# access to organizations/facilities/billing for their own work but no
# stated auditability/compliance responsibility.
AUDIT_VIEWER_ROLES = frozenset(
    {
        InternalRole.COMPLIANCE_REVIEWER,
        InternalRole.OPERATIONS_MANAGER,
        InternalRole.SYSTEM_ADMINISTRATOR,
    }
)


def can_view_audit_log(user: AnyUser) -> bool:
    """Can the user view the internal audit viewer (apps.audit)?"""
    return get_internal_role(user) in AUDIT_VIEWER_ROLES


def is_mfa_eligible(user: AnyUser) -> bool:
    """Is this user a "privileged demo account" eligible to enroll TOTP MFA
    (apps.accounts.mfa, Phase 8 — docs/SECURITY_COMPLIANCE_BOUNDARIES.md
    section 4: "TOTP MFA for privileged roles when enabled")?

    Scope decision (see docs/CURRENT_STATUS.md "Phase 8" for the full
    write-up): every internal-ops role (any `InternalRoleAssignment`, not
    just the cross-org-manage subset — a courier-onboarding-reviewer's
    account is just as much a "privileged internal account" as a
    dispatcher's for this purpose) plus any customer-organization
    owner/administrator (`ORG_MANAGING_ROLES`) in at least one active
    membership. Ordinary customer-org roles (requester/dispatcher, billing
    manager, compliance reviewer, read-only auditor) are not required to
    enroll in this demo — MFA is opt-in and enforced only once a user has
    actually completed enrollment (see `apps.accounts.mfa`), never a login
    hard-block for accounts that never enrolled.
    """
    if isinstance(user, AnonymousUser) or not user.is_authenticated:
        return False
    if getattr(user, "is_internal_staff", False):
        return True
    return OrganizationMembership.objects.filter(
        user=user, role__in=ORG_MANAGING_ROLES, is_active=True
    ).exists()
