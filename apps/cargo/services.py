"""Cargo-domain service helpers: policy lookups and package creation.

Kept small and dependency-free of `apps.deliveries` internals beyond the
type reference already established by the FK relationship in `models.py` —
`create_packages_for_delivery_request` takes a `DeliveryRequest` instance
(passed in by the caller in `apps.deliveries`) rather than importing/
querying it itself, so this module never has to know how delivery requests
are looked up or permission-checked.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from apps.cargo.models import (
    CargoClass,
    CargoPolicy,
    Package,
    PackageIdentifier,
    TemperatureProfile,
)

if TYPE_CHECKING:
    from apps.deliveries.models import DeliveryRequest


def get_cargo_policy(cargo_class: CargoClass) -> CargoPolicy:
    """The `CargoPolicy` row for a cargo class (created via seed data migration)."""
    return CargoPolicy.objects.get(cargo_class=cargo_class)


def temperature_profile_allowed(
    cargo_class: CargoClass, temperature_profile: TemperatureProfile
) -> bool:
    """Whether `cargo_class`'s policy permits `temperature_profile` (e.g. Class 1 disallows
    refrigerated per the seeded policy — see docs/CURRENT_STATUS.md)."""
    policy = get_cargo_policy(cargo_class)
    return policy.allows_temperature_profile(temperature_profile)


def create_packages_for_delivery_request(
    delivery_request: DeliveryRequest,
    *,
    count: int,
    cargo_class: CargoClass,
    temperature_profile: TemperatureProfile,
    approximate_weight_kg: Decimal | None = None,
    approximate_length_cm: Decimal | None = None,
    approximate_width_cm: Decimal | None = None,
    approximate_height_cm: Decimal | None = None,
) -> list[Package]:
    """Create `count` `Package` rows (each with its own `PackageIdentifier`) for a
    delivery request, cloning the request-level cargo class/temperature profile/
    approximate dimensions onto every package (Phase 2's wizard captures these
    once per request, not per package — see apps/cargo/models.py's `Package`
    docstring)."""
    packages: list[Package] = []
    for sequence_number in range(1, count + 1):
        package = Package.objects.create(
            delivery_request=delivery_request,
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            sequence_number=sequence_number,
            approximate_weight_kg=approximate_weight_kg,
            approximate_length_cm=approximate_length_cm,
            approximate_width_cm=approximate_width_cm,
            approximate_height_cm=approximate_height_cm,
        )
        PackageIdentifier.objects.create(package=package)
        packages.append(package)
    return packages
