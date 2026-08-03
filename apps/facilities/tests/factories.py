"""factory_boy factories for facilities."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.facilities.models import Borough, Facility, FacilityType, ServiceZone
from apps.organizations.tests.factories import OrganizationFactory


class ServiceZoneFactory(DjangoModelFactory):
    class Meta:
        model = ServiceZone
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Test Service Zone {n} (Demo)")
    borough = Borough.MANHATTAN


class FacilityFactory(DjangoModelFactory):
    class Meta:
        model = Facility
        django_get_or_create = ("organization", "name")

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Test Facility {n} (Demo)")
    facility_type = FacilityType.CLINIC_SITE
    address_line1 = "1 Test Fictional St"
    postal_code = "10001"
    borough = Borough.MANHATTAN
