"""Tests for the `seed_demo_data` management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.accounts.models import InternalRoleAssignment
from apps.facilities.models import Facility, FacilityContact, FacilityReceivingRule, ServiceZone
from apps.organizations.models import Organization, OrganizationMembership

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_seed_demo_data_creates_expected_counts() -> None:
    call_command("seed_demo_data", stdout=StringIO())

    assert Organization.objects.count() == 3
    assert Facility.objects.count() == 8
    assert ServiceZone.objects.count() == 2
    assert OrganizationMembership.objects.count() == 18  # 3 orgs x 6 customer roles
    assert InternalRoleAssignment.objects.count() == 7
    assert FacilityContact.objects.count() == 8
    # 5 weekday + 2 weekend rules per facility.
    assert FacilityReceivingRule.objects.count() == 8 * 7


def test_seed_demo_data_facilities_span_manhattan_and_brooklyn() -> None:
    call_command("seed_demo_data", stdout=StringIO())

    boroughs = set(Facility.objects.values_list("borough", flat=True))
    assert boroughs == {"manhattan", "brooklyn"}


def test_seed_demo_data_is_idempotent() -> None:
    call_command("seed_demo_data", stdout=StringIO())
    call_command("seed_demo_data", stdout=StringIO())

    assert Organization.objects.count() == 3
    assert Facility.objects.count() == 8
    assert OrganizationMembership.objects.count() == 18
    assert User.objects.count() == 25  # 18 customer-org + 7 internal


def test_seed_demo_data_users_have_no_clinical_fields() -> None:
    """Data-minimization guard: no field on the seeded models resembles PHI."""
    call_command("seed_demo_data", stdout=StringIO())

    prohibited_terms = {"diagnosis", "ssn", "insurance", "lab_result", "prescription"}
    for facility in Facility.objects.all():
        field_names = {f.name.lower() for f in facility._meta.get_fields()}
        assert field_names.isdisjoint(prohibited_terms)
