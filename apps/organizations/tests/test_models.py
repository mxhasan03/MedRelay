"""Tests for Organization and OrganizationMembership model behavior."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import (
    CustomerRole,
    Organization,
    OrganizationMembership,
    OrganizationType,
)
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def test_organization_str_is_name() -> None:
    org = OrganizationFactory(name="Acme Clinic (Demo)")
    assert str(org) == "Acme Clinic (Demo)"


def test_organization_name_is_unique() -> None:
    # Uses the model directly (not OrganizationFactory, whose
    # `django_get_or_create` would silently return the existing row instead
    # of exercising the DB uniqueness constraint).
    Organization.objects.create(name="Unique Org (Demo)", org_type=OrganizationType.CLINIC)
    with pytest.raises(IntegrityError), transaction.atomic():
        Organization.objects.create(name="Unique Org (Demo)", org_type=OrganizationType.CLINIC)


def test_membership_str_includes_role() -> None:
    org = OrganizationFactory(name="Membership Org (Demo)")
    user = UserFactory(username="memberstr", first_name="Member", last_name="Str")
    membership = OrganizationMembership.objects.create(
        user=user, organization=org, role=CustomerRole.ADMINISTRATOR
    )
    assert "Member Str" in str(membership)
    assert "Administrator" in str(membership)


def test_membership_unique_per_user_per_org() -> None:
    org = OrganizationFactory(name="Dupe Org (Demo)")
    user = UserFactory(username="dupemember")
    OrganizationMembership.objects.create(user=user, organization=org, role=CustomerRole.OWNER)

    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationMembership.objects.create(
            user=user, organization=org, role=CustomerRole.READ_ONLY_AUDITOR
        )


def test_same_user_can_belong_to_multiple_orgs() -> None:
    org_a = OrganizationFactory(name="Multi Org A (Demo)")
    org_b = OrganizationFactory(name="Multi Org B (Demo)")
    user = UserFactory(username="multiorguser")

    OrganizationMembership.objects.create(user=user, organization=org_a, role=CustomerRole.OWNER)
    OrganizationMembership.objects.create(
        user=user, organization=org_b, role=CustomerRole.READ_ONLY_AUDITOR
    )

    assert OrganizationMembership.objects.filter(user=user).count() == 2
