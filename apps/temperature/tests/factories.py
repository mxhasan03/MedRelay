"""factory_boy factories for temperature-app tests."""

from __future__ import annotations

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.temperature.models import TemperatureReading


class TemperatureReadingFactory(DjangoModelFactory):
    class Meta:
        model = TemperatureReading

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    temperature_c = 4.0
    recorded_at = factory.LazyFunction(timezone.now)
