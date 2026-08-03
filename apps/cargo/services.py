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

from django.utils import timezone

from apps.cargo.models import (
    CargoClass,
    CargoPolicy,
    Package,
    PackageIdentifier,
    TemperatureProfile,
)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.deliveries.models import DeliveryRequest


class PackageScanError(Exception):
    """Raised by `confirm_package_scan` when the submitted code does not identify a
    package belonging to the given delivery request. Used for both a genuinely
    unknown/nonexistent code and a code that identifies a real package belonging
    to a *different* delivery request — deliberately the same error/message for
    both, so a courier scanning the wrong package's code gets no information
    about what delivery request that code actually belongs to.
    """


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


def confirm_package_scan(
    delivery_request: DeliveryRequest, code: str, *, actor: User | None = None
) -> Package:
    """Confirm a package pickup scan for `code` against `delivery_request`'s packages.

    This is the manual-code-entry (and, from the browser, camera-`BarcodeDetector`-
    filled-then-submitted) fallback path described in
    docs/PRODUCT_REQUIREMENTS.md section 6 ("scan package") —
    apps.couriers.views.PackageScanView is the only caller. Real camera-based
    scanning happens entirely in the browser (populating the same manual-entry
    field with the decoded value before submit); this function only ever sees a
    plain string code and has no way to know whether it came from a camera or a
    keyboard, which is exactly the point — the manual path is always present and
    always functional, per this phase's honesty requirement about what can
    actually be tested headlessly.

    Raises `PackageScanError` for a blank/unknown code, or a code belonging to a
    different delivery request. Idempotent at the call level: scanning the same
    correct code twice just refreshes `scanned_at`/`scanned_by` rather than
    erroring (the courier re-tapping "scan" on an already-scanned package should
    not be treated as a failure).
    """
    code = (code or "").strip()
    if not code:
        raise PackageScanError("A package code is required.")
    try:
        identifier = PackageIdentifier.objects.select_related("package").get(code=code)
    except PackageIdentifier.DoesNotExist as exc:
        raise PackageScanError(
            f"Code {code!r} does not match any package for this delivery."
        ) from exc

    package = identifier.package
    if package.delivery_request_id != delivery_request.pk:
        raise PackageScanError(f"Code {code!r} does not match any package for this delivery.")

    package.scanned_at = timezone.now()
    package.scanned_by = actor
    package.save(update_fields=["scanned_at", "scanned_by", "updated_at"])
    return package
