"""Tests for apps.cargo.services."""

from __future__ import annotations

import pytest

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.services import (
    create_packages_for_delivery_request,
    get_cargo_policy,
    temperature_profile_allowed,
)
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    TemperatureProfileFactory,
)
from apps.deliveries.tests.factories import DeliveryRequestFactory

pytestmark = pytest.mark.django_db


def test_get_cargo_policy_returns_seeded_policy() -> None:
    from apps.cargo.models import CargoClass

    cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_2)
    policy = get_cargo_policy(cargo_class)
    assert policy.cargo_class_id == cargo_class.id


def test_temperature_profile_allowed_matches_policy() -> None:
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=False)
    ambient = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    refrigerated = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)

    assert temperature_profile_allowed(cargo_class, ambient) is True
    assert temperature_profile_allowed(cargo_class, refrigerated) is False


def test_create_packages_for_delivery_request_creates_count_with_identifiers() -> None:
    delivery_request = DeliveryRequestFactory()
    cargo_class = CargoClassFactory()
    temperature_profile = TemperatureProfileFactory()

    packages = create_packages_for_delivery_request(
        delivery_request,
        count=3,
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
    )

    assert len(packages) == 3
    assert [p.sequence_number for p in packages] == [1, 2, 3]
    for package in packages:
        assert package.identifier is not None
        assert package.identifier.code.startswith("PKG-")
        assert package.cargo_class_id == cargo_class.id
        assert package.temperature_profile_id == temperature_profile.id
