"""Tests for apps.cargo.services."""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.services import (
    PackageScanError,
    confirm_package_scan,
    create_packages_for_delivery_request,
    get_cargo_policy,
    temperature_profile_allowed,
)
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackageFactory,
    PackageIdentifierFactory,
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


# --- confirm_package_scan (Phase 5: manual-entry pickup scan confirmation) ---


def test_confirm_package_scan_with_correct_code_marks_scanned() -> None:
    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request, sequence_number=1)
    identifier = PackageIdentifierFactory(package=package)
    actor = UserFactory()

    result = confirm_package_scan(delivery_request, identifier.code, actor=actor)

    assert result.pk == package.pk
    package.refresh_from_db()
    assert package.scanned_at is not None
    assert package.scanned_by_id == actor.pk


def test_confirm_package_scan_with_nonexistent_code_raises() -> None:
    delivery_request = DeliveryRequestFactory()
    PackageFactory(delivery_request=delivery_request)

    with pytest.raises(PackageScanError):
        confirm_package_scan(delivery_request, "PKG-DOESNOTEXIST")


def test_confirm_package_scan_with_blank_code_raises() -> None:
    delivery_request = DeliveryRequestFactory()

    with pytest.raises(PackageScanError):
        confirm_package_scan(delivery_request, "   ")


def test_confirm_package_scan_rejects_code_belonging_to_a_different_delivery() -> None:
    delivery_request = DeliveryRequestFactory()
    other_delivery_request = DeliveryRequestFactory()
    other_package = PackageFactory(delivery_request=other_delivery_request)
    other_identifier = PackageIdentifierFactory(package=other_package)

    with pytest.raises(PackageScanError):
        confirm_package_scan(delivery_request, other_identifier.code)


def test_confirm_package_scan_is_idempotent_for_the_same_correct_code() -> None:
    """Scanning the same correct code twice just refreshes scanned_at rather
    than erroring — the courier re-tapping "scan" on an already-scanned
    package should not be treated as a failure."""
    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request)
    identifier = PackageIdentifierFactory(package=package)

    first = confirm_package_scan(delivery_request, identifier.code)
    second = confirm_package_scan(delivery_request, identifier.code)

    assert first.pk == second.pk
    assert second.scanned_at is not None


def test_confirm_package_scan_appends_a_pickup_scan_custody_event() -> None:
    """Phase 6: PICKUP_SCAN custody event alongside the existing
    scanned_at/scanned_by bookkeeping."""
    from apps.custody.models import CustodyEventType

    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request)
    identifier = PackageIdentifierFactory(package=package)

    confirm_package_scan(delivery_request, identifier.code)

    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.event_type == CustodyEventType.PICKUP_SCAN
    assert last_event.package_id == package.pk


# --- record_condition_check (Phase 6) ---------------------------------------


def test_record_condition_check_creates_row_and_custody_event() -> None:
    from apps.cargo.models import PackageConditionCheck, SealStatus, TemperatureIndicatorStatus
    from apps.cargo.services import record_condition_check
    from apps.custody.models import CustodyEventType

    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request)

    check = record_condition_check(
        package,
        stage="pickup",
        actor=None,
        seal_status=SealStatus.INTACT,
        temperature_indicator_status=TemperatureIndicatorStatus.OK,
    )

    assert isinstance(check, PackageConditionCheck)
    assert check.has_any_concern is False
    assert check.custody_event is not None
    assert check.custody_event.event_type == CustodyEventType.CONDITION_VERIFIED

    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.pk == check.custody_event_id


def test_record_condition_check_with_broken_seal_flags_a_concern() -> None:
    from apps.cargo.models import SealStatus
    from apps.cargo.services import record_condition_check

    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request)

    check = record_condition_check(
        package, stage="delivery", actor=None, seal_status=SealStatus.BROKEN
    )

    assert check.has_any_concern is True


def test_record_condition_check_twice_for_same_stage_updates_not_duplicates() -> None:
    from apps.cargo.models import PackageConditionCheck, SealStatus
    from apps.cargo.services import record_condition_check

    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request)

    record_condition_check(package, stage="pickup", actor=None, seal_status=SealStatus.INTACT)
    record_condition_check(package, stage="pickup", actor=None, seal_status=SealStatus.BROKEN)

    assert PackageConditionCheck.objects.filter(package=package, stage="pickup").count() == 1
    check = PackageConditionCheck.objects.get(package=package, stage="pickup")
    assert check.seal_status == SealStatus.BROKEN
