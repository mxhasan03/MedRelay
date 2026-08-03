"""Tests for Facility and related model behavior."""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction

from apps.facilities.models import (
    Borough,
    DayOfWeek,
    Facility,
    FacilityContact,
    FacilityReceivingRule,
    FacilityType,
    ServiceZone,
)
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def test_facility_str_includes_org() -> None:
    org = OrganizationFactory(name="Facility Org (Demo)")
    facility = FacilityFactory(organization=org, name="Main Site (Demo)")
    assert str(facility) == "Main Site (Demo) (Facility Org (Demo))"


def test_facility_name_unique_per_org_but_not_globally() -> None:
    # Uses Facility.objects.create directly (not FacilityFactory, whose
    # `django_get_or_create` would silently return the existing row instead
    # of exercising the DB uniqueness constraint).
    org_a = OrganizationFactory(name="Dup Facility Org A (Demo)")
    org_b = OrganizationFactory(name="Dup Facility Org B (Demo)")

    common_fields = {
        "name": "Shared Name (Demo)",
        "facility_type": FacilityType.CLINIC_SITE,
        "address_line1": "1 Test Fictional St",
        "postal_code": "10001",
        "borough": Borough.MANHATTAN,
    }
    Facility.objects.create(organization=org_a, **common_fields)
    # Same name, different org: allowed.
    Facility.objects.create(organization=org_b, **common_fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        Facility.objects.create(organization=org_a, **common_fields)


def test_facility_defaults_to_active() -> None:
    facility = FacilityFactory()
    assert facility.is_active is True


def test_facility_contact_can_be_marked_primary() -> None:
    facility = FacilityFactory(name="Contact Facility (Demo)")
    contact = FacilityContact.objects.create(
        facility=facility,
        name="Demo Contact",
        phone="555-0100",
        email="demo.contact@example.com",
        is_primary=True,
    )
    assert contact in facility.contacts.all()


def test_receiving_rule_unique_per_day() -> None:
    facility = FacilityFactory(name="Rule Facility (Demo)")
    FacilityReceivingRule.objects.create(
        facility=facility,
        day_of_week=DayOfWeek.MONDAY,
        opens_at=datetime.time(8, 0),
        closes_at=datetime.time(18, 0),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        FacilityReceivingRule.objects.create(
            facility=facility,
            day_of_week=DayOfWeek.MONDAY,
            opens_at=datetime.time(9, 0),
            closes_at=datetime.time(17, 0),
        )


def test_receiving_rule_can_mark_day_closed() -> None:
    facility = FacilityFactory(name="Closed Day Facility (Demo)")
    rule = FacilityReceivingRule.objects.create(
        facility=facility, day_of_week=DayOfWeek.SUNDAY, is_closed=True
    )
    assert rule.is_closed is True
    assert rule.opens_at is None


def test_service_zone_str() -> None:
    zone = ServiceZone.objects.create(name="Test Zone (Demo)", borough=Borough.BROOKLYN)
    assert "Test Zone (Demo)" in str(zone)
    assert "Brooklyn" in str(zone)


def test_facility_can_reference_service_zone() -> None:
    zone = ServiceZone.objects.create(name="Linked Zone (Demo)", borough=Borough.MANHATTAN)
    facility = FacilityFactory(name="Zoned Facility (Demo)", service_zone=zone)
    assert facility.service_zone == zone
    assert facility in zone.facilities.all()


def test_facility_lat_lng_are_plain_decimal_fields_not_postgis() -> None:
    """Regression guard for the documented geo-storage decision (see
    apps/facilities/models.py module docstring and docs/CURRENT_STATUS.md):
    latitude/longitude must stay plain decimal fields, not a PostGIS
    PointField, until Phase 4 introduces real geo-distance dispatch logic.
    """
    facility = FacilityFactory(
        name="Geo Facility (Demo)", latitude="40.730000", longitude="-73.990000"
    )
    facility.refresh_from_db()
    assert facility.latitude is not None
    assert facility.longitude is not None
    # A plain Decimal, not a GEOS Point/geometry object.
    assert not hasattr(facility.latitude, "geom_type")
