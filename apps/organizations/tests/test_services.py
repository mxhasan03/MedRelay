"""Cross-tenant isolation tests and role-permission matrix tests.

These are the hard acceptance-criteria tests for Phase 1
(docs/IMPLEMENTATION_ROADMAP.md): proving a user in Organization A cannot
read/write Organization B's data through the tenant-scoped query/permission
helpers, and that each role in docs/PRODUCT_REQUIREMENTS.md section 4 gets
exactly the access it should.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.facilities.models import Facility
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.models import CustomerRole, OrganizationMembership
from apps.organizations.services import (
    can_manage_facilities,
    can_manage_organization,
    can_view_organization,
    get_member_organization_ids,
    has_cross_org_manage_access,
    has_cross_org_read_access,
    organizations_for_user,
    scope_queryset_to_user_orgs,
)
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_a():
    return OrganizationFactory(name="Tenant Org A (Demo)")


@pytest.fixture
def org_b():
    return OrganizationFactory(name="Tenant Org B (Demo)")


@pytest.fixture
def user_a(org_a):
    user = UserFactory(username="tenant_user_a")
    OrganizationMembership.objects.create(user=user, organization=org_a, role=CustomerRole.OWNER)
    return user


@pytest.fixture
def user_b(org_b):
    user = UserFactory(username="tenant_user_b")
    OrganizationMembership.objects.create(user=user, organization=org_b, role=CustomerRole.OWNER)
    return user


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


def test_member_organization_ids_only_includes_own_orgs(user_a, org_a, org_b) -> None:
    assert get_member_organization_ids(user_a) == {org_a.pk}


def test_user_a_cannot_view_org_b(user_a, org_b) -> None:
    assert can_view_organization(user_a, org_b.pk) is False


def test_user_a_cannot_manage_org_b(user_a, org_b) -> None:
    assert can_manage_organization(user_a, org_b.pk) is False


def test_organizations_for_user_excludes_other_tenants(user_a, org_a, org_b) -> None:
    visible = organizations_for_user(user_a)
    assert list(visible) == [org_a]
    assert org_b not in visible


def test_facility_queryset_for_user_excludes_other_tenants(user_a, org_a, org_b) -> None:
    facility_a = FacilityFactory(organization=org_a, name="Facility A (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="Facility B (Demo)")

    visible = Facility.objects.for_user(user_a)

    assert list(visible) == [facility_a]
    assert facility_b not in visible


def test_generic_scope_helper_excludes_other_tenants(user_a, org_a, org_b) -> None:
    FacilityFactory(organization=org_a, name="Scoped A (Demo)")
    FacilityFactory(organization=org_b, name="Scoped B (Demo)")

    scoped = scope_queryset_to_user_orgs(
        Facility.objects.all(), user_a, org_field="organization_id"
    )

    assert scoped.count() == 1
    assert scoped.first().organization_id == org_a.pk


def test_user_with_no_memberships_sees_nothing(org_a, org_b) -> None:
    lonely_user = UserFactory(username="lonely_user")
    assert organizations_for_user(lonely_user).count() == 0
    assert get_member_organization_ids(lonely_user) == set()


def test_anonymous_user_sees_nothing(org_a) -> None:
    anon = AnonymousUser()
    assert organizations_for_user(anon).count() == 0
    assert can_view_organization(anon, org_a.pk) is False
    assert list(Facility.objects.for_user(anon)) == []


def test_two_users_each_confined_to_their_own_org(user_a, user_b, org_a, org_b) -> None:
    """The two-sided version: neither tenant can see the other's data."""
    assert org_a in organizations_for_user(user_a)
    assert org_b not in organizations_for_user(user_a)
    assert org_b in organizations_for_user(user_b)
    assert org_a not in organizations_for_user(user_b)


# ---------------------------------------------------------------------------
# Role-permission matrix: customer organization roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,expected_can_manage",
    [
        (CustomerRole.OWNER, True),
        (CustomerRole.ADMINISTRATOR, True),
        (CustomerRole.REQUESTER_DISPATCHER, False),
        (CustomerRole.BILLING_MANAGER, False),
        (CustomerRole.COMPLIANCE_REVIEWER, False),
        (CustomerRole.READ_ONLY_AUDITOR, False),
    ],
)
def test_customer_role_manage_matrix(role, expected_can_manage, org_a) -> None:
    user = UserFactory(username=f"role_{role}")
    OrganizationMembership.objects.create(user=user, organization=org_a, role=role)

    # Every member, regardless of role, can view their own organization.
    assert can_view_organization(user, org_a.pk) is True
    assert can_manage_organization(user, org_a.pk) is expected_can_manage
    assert can_manage_facilities(user, org_a.pk) is expected_can_manage


def test_read_only_auditor_cannot_manage_anything(org_a) -> None:
    auditor = UserFactory(username="strict_auditor")
    OrganizationMembership.objects.create(
        user=auditor, organization=org_a, role=CustomerRole.READ_ONLY_AUDITOR
    )
    assert can_view_organization(auditor, org_a.pk) is True
    assert can_manage_organization(auditor, org_a.pk) is False
    assert can_manage_facilities(auditor, org_a.pk) is False


# ---------------------------------------------------------------------------
# Role-permission matrix: internal operations roles (cross-org access is
# explicit and opt-in per role, never implicit from `is_internal_staff`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,expected_read,expected_manage",
    [
        (InternalRole.OPERATIONS_MANAGER, True, True),
        (InternalRole.SYSTEM_ADMINISTRATOR, True, True),
        (InternalRole.CUSTOMER_SUPPORT, True, False),
        (InternalRole.COMPLIANCE_REVIEWER, True, False),
        (InternalRole.FINANCE, True, False),
        (InternalRole.DISPATCHER, True, False),
        (InternalRole.COURIER_ONBOARDING_REVIEWER, False, False),
    ],
)
def test_internal_role_cross_org_matrix(role, expected_read, expected_manage, org_a) -> None:
    staffer = UserFactory(username=f"internal_{role}")
    InternalRoleAssignment.objects.create(user=staffer, role=role)

    assert has_cross_org_read_access(staffer) is expected_read
    assert has_cross_org_manage_access(staffer) is expected_manage
    assert can_view_organization(staffer, org_a.pk) is expected_read
    assert can_manage_organization(staffer, org_a.pk) is expected_manage


def test_internal_staff_flag_alone_grants_nothing(org_a) -> None:
    """`is_internal_staff=True` with no InternalRoleAssignment must grant zero access."""
    staffer = UserFactory(username="staff_no_role", is_internal_staff=True)
    assert has_cross_org_read_access(staffer) is False
    assert has_cross_org_manage_access(staffer) is False
    assert can_view_organization(staffer, org_a.pk) is False
    assert can_manage_organization(staffer, org_a.pk) is False


def test_operations_manager_sees_all_orgs_and_facilities(org_a, org_b) -> None:
    facility_a = FacilityFactory(organization=org_a, name="Ops Facility A (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="Ops Facility B (Demo)")

    ops_manager = UserFactory(username="cross_org_ops")
    InternalRoleAssignment.objects.create(user=ops_manager, role=InternalRole.OPERATIONS_MANAGER)

    visible_orgs = organizations_for_user(ops_manager)
    assert org_a in visible_orgs
    assert org_b in visible_orgs

    visible_facilities = Facility.objects.for_user(ops_manager)
    assert facility_a in visible_facilities
    assert facility_b in visible_facilities
