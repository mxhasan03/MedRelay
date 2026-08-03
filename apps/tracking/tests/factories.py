"""factory_boy factories for courier location pings."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.dispatch.tests.factories import DeliveryAssignmentFactory
from apps.tracking.models import CourierLocationPing


class CourierLocationPingFactory(DjangoModelFactory):
    class Meta:
        model = CourierLocationPing

    assignment = factory.SubFactory(DeliveryAssignmentFactory)
    courier = factory.LazyAttribute(lambda o: o.assignment.courier)
    latitude = "40.712800"
    longitude = "-74.006000"
