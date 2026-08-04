"""Tests for the `simulate_temperature_readings` management command — the
zero-cost, honestly-documented "simulated sensor" generator (see the
command's own module docstring: no real IoT device is involved)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cargo.models import TemperatureProfileCode
from apps.cargo.tests.factories import TemperatureProfileFactory
from apps.deliveries.models import DeliveryStatus
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.temperature.models import TemperatureExcursion, TemperatureReading

pytestmark = pytest.mark.django_db


def test_simulate_temperature_readings_generates_readings_in_range() -> None:
    profile = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    delivery_request = DeliveryRequestFactory(temperature_profile=profile)

    out = StringIO()
    call_command(
        "simulate_temperature_readings",
        str(delivery_request.pk),
        "--count=3",
        "--seed=42",
        stdout=out,
    )

    assert TemperatureReading.objects.filter(delivery_request=delivery_request).count() == 3
    assert "Generated 3 simulated reading" in out.getvalue()


def test_simulate_temperature_readings_with_excursion_chance_can_open_incidents() -> None:
    profile = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    delivery_request = DeliveryRequestFactory(
        temperature_profile=profile, status=DeliveryStatus.IN_TRANSIT
    )

    call_command(
        "simulate_temperature_readings",
        str(delivery_request.pk),
        "--count=10",
        "--excursion-chance=1.0",
        "--seed=7",
    )

    assert TemperatureExcursion.objects.filter(delivery_request=delivery_request).count() == 10
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD


def test_simulate_temperature_readings_raises_for_unknown_delivery() -> None:
    with pytest.raises(CommandError):
        call_command("simulate_temperature_readings", "00000000-0000-0000-0000-000000000000")


def test_simulate_temperature_readings_raises_without_temperature_profile() -> None:
    delivery_request = DeliveryRequestFactory(temperature_profile=None)
    with pytest.raises(CommandError):
        call_command("simulate_temperature_readings", str(delivery_request.pk))
