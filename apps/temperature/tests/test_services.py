"""Tests for `apps.temperature.services.record_reading` — simulated reading
storage and the excursion -> incident -> (possible) INCIDENT_HOLD pipeline.

This is the "temperature-excursion-triggers-hold path" half of Phase 6's
incident-hold acceptance criterion.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.cargo.models import TemperatureProfileCode
from apps.cargo.tests.factories import TemperatureProfileFactory
from apps.deliveries.models import DeliveryStatus
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.incidents.models import IncidentCategory, IncidentSeverity, IncidentStatus
from apps.temperature.models import TemperatureExcursion, TemperatureReading
from apps.temperature.services import record_reading

pytestmark = pytest.mark.django_db


def _refrigerated_delivery_request(status: str = DeliveryStatus.IN_TRANSIT):
    profile = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    assert profile.min_temp_c == Decimal("2.0")
    assert profile.max_temp_c == Decimal("8.0")
    return DeliveryRequestFactory(temperature_profile=profile, status=status)


def test_in_range_reading_does_not_create_an_excursion() -> None:
    delivery_request = _refrigerated_delivery_request()
    reading = record_reading(delivery_request, temperature_c=Decimal("5.0"))

    assert TemperatureReading.objects.count() == 1
    assert TemperatureExcursion.objects.count() == 0
    assert not hasattr(reading, "excursion")
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.IN_TRANSIT


def test_out_of_range_reading_creates_excursion_opens_incident_and_holds_delivery() -> None:
    delivery_request = _refrigerated_delivery_request()

    reading = record_reading(delivery_request, temperature_c=Decimal("25.0"))

    excursion = TemperatureExcursion.objects.get(reading=reading)
    assert excursion.delivery_request_id == delivery_request.pk
    assert excursion.threshold_min_c == Decimal("2.0")
    assert excursion.threshold_max_c == Decimal("8.0")
    assert excursion.incident is not None
    assert excursion.incident.category == IncidentCategory.TEMPERATURE_EXCURSION
    assert excursion.incident.severity == IncidentSeverity.SEVERE
    assert excursion.incident.status == IncidentStatus.OPEN
    assert excursion.incident.placed_delivery_on_hold is True

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD


def test_below_range_reading_is_also_an_excursion() -> None:
    delivery_request = _refrigerated_delivery_request()
    reading = record_reading(delivery_request, temperature_c=Decimal("-5.0"))
    assert TemperatureExcursion.objects.filter(reading=reading).exists()
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD


def test_profile_with_no_configured_bounds_never_excursions() -> None:
    """A temperature profile with both bounds blank (unconstrained) never
    flags an excursion, regardless of the reading."""
    from apps.cargo.models import TemperatureProfile

    unconstrained = TemperatureProfile.objects.create(
        code="unconstrained_test_profile", name="Unconstrained (Test)"
    )
    delivery_request = DeliveryRequestFactory(temperature_profile=unconstrained)

    record_reading(delivery_request, temperature_c=Decimal("999.0"))

    assert TemperatureExcursion.objects.count() == 0
    delivery_request.refresh_from_db()
    assert delivery_request.status != DeliveryStatus.INCIDENT_HOLD


def test_record_reading_per_package_uses_the_packages_own_temperature_profile() -> None:
    from apps.cargo.tests.factories import PackageFactory

    ambient_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    refrigerated_profile = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    delivery_request = DeliveryRequestFactory(
        temperature_profile=ambient_profile, status=DeliveryStatus.IN_TRANSIT
    )
    package = PackageFactory(
        delivery_request=delivery_request, temperature_profile=refrigerated_profile
    )

    # 20C would be fine for ambient (15-25C) but is an excursion for this
    # specific package's own refrigerated (2-8C) profile.
    reading = record_reading(delivery_request, package=package, temperature_c=Decimal("20.0"))

    assert TemperatureExcursion.objects.filter(reading=reading, package=package).exists()
