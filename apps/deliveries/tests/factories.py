"""factory_boy factories for delivery requests, stops, pricing rules, and recurring routes."""

from __future__ import annotations

import datetime
import decimal

import factory
from factory.django import DjangoModelFactory

from apps.cargo.tests.factories import CargoClassFactory, TemperatureProfileFactory
from apps.deliveries.models import (
    DeliveryRequest,
    DeliveryStop,
    PricingRule,
    RecurringRoute,
    ServiceLevel,
    StopType,
)
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.tests.factories import OrganizationFactory

# A fixed, deterministic "Monday at 10am UTC" instant used as the default pickup
# window across every delivery-request factory instance, so pricing/state-machine
# tests get reproducible after-hours behavior unless a test overrides it.
DEFAULT_PICKUP_START = datetime.datetime(
    2026, 1, 5, 14, 0, tzinfo=datetime.UTC
)  # 9am America/New_York


class DeliveryRequestFactory(DjangoModelFactory):
    class Meta:
        model = DeliveryRequest

    organization = factory.SubFactory(OrganizationFactory)
    service_level = ServiceLevel.SCHEDULED
    pickup_window_start = DEFAULT_PICKUP_START
    pickup_window_end = factory.LazyAttribute(
        lambda o: o.pickup_window_start + datetime.timedelta(hours=2)
    )
    required_delivery_by = factory.LazyAttribute(
        lambda o: o.pickup_window_start + datetime.timedelta(hours=4)
    )
    cargo_class = factory.SubFactory(CargoClassFactory)
    temperature_profile = factory.SubFactory(TemperatureProfileFactory)
    package_count = 1
    sender_contact_name = "Sender Contact (Test)"
    sender_contact_role = "Front desk"
    recipient_contact_name = "Recipient Contact (Test)"
    recipient_contact_role = "Lab intake"


class DeliveryStopFactory(DjangoModelFactory):
    class Meta:
        model = DeliveryStop

    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    stop_type = StopType.PICKUP
    sequence = 1
    facility = factory.SubFactory(FacilityFactory)


class PricingRuleFactory(DjangoModelFactory):
    class Meta:
        model = PricingRule
        django_get_or_create = ("key",)

    amount = decimal.Decimal("1.00")


class RecurringRouteFactory(DjangoModelFactory):
    class Meta:
        model = RecurringRoute

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Test Recurring Route {n} (Demo)")
    frequency = "weekly"
    weekly_days_of_week = [0, 2, 4]
    start_date = datetime.date(2026, 1, 5)
    service_level = ServiceLevel.SCHEDULED
    cargo_class = factory.SubFactory(CargoClassFactory)
    temperature_profile = factory.SubFactory(TemperatureProfileFactory)
