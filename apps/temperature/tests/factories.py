"""factory_boy factories for temperature-app tests."""

from __future__ import annotations

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.temperature.models import TemperatureExcursion, TemperatureReading


class TemperatureReadingFactory(DjangoModelFactory):
    class Meta:
        model = TemperatureReading

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    temperature_c = 4.0
    recorded_at = factory.LazyFunction(timezone.now)


class TemperatureExcursionFactory(DjangoModelFactory):
    """Note: `incident` defaults to an independent `IncidentFactory()` (its
    own, separate `delivery_request`) rather than trying to force it onto
    the same delivery as this excursion — nothing that queries
    `TemperatureExcursion` (e.g. `apps.dispatch.views`' "open temperature
    excursion count" annotation, which only ever joins through `incident__
    status`) needs the two `delivery_request`s to match; a caller that does
    care can override `incident=` explicitly."""

    class Meta:
        model = TemperatureExcursion

    reading = factory.SubFactory(TemperatureReadingFactory)
    delivery_request = factory.LazyAttribute(lambda o: o.reading.delivery_request)
    temperature_c = 4.0
    incident = factory.SubFactory("apps.incidents.tests.factories.IncidentFactory")
