"""Model-level tests for cargo classes, policies, temperature profiles, packages,
package identifiers, and packaging attestations."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackageFactory,
    PackageIdentifierFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.deliveries.tests.factories import DeliveryRequestFactory

pytestmark = pytest.mark.django_db


def test_seeded_cargo_classes_exist_via_migration() -> None:
    """The data migration (apps/cargo/migrations/0003_seed_cargo_reference_data.py)
    seeds exactly the three fixed classes."""
    from apps.cargo.models import CargoClass

    codes = set(CargoClass.objects.values_list("code", flat=True))
    assert codes == {CargoClassCode.CLASS_1, CargoClassCode.CLASS_2, CargoClassCode.CLASS_3}


def test_seeded_temperature_profiles_are_ambient_and_refrigerated_only() -> None:
    from apps.cargo.models import TemperatureProfile

    codes = set(TemperatureProfile.objects.values_list("code", flat=True))
    assert codes == {TemperatureProfileCode.AMBIENT, TemperatureProfileCode.REFRIGERATED}
    # Frozen is explicitly deferred per docs/PRODUCT_REQUIREMENTS.md section 3.
    assert "frozen" not in codes


def test_seeded_class_1_policy_disallows_refrigerated() -> None:
    from apps.cargo.models import CargoClass, CargoPolicy

    class_1 = CargoClass.objects.get(code=CargoClassCode.CLASS_1)
    policy = CargoPolicy.objects.get(cargo_class=class_1)
    assert policy.requires_packaging_attestation is True
    assert policy.allows_ambient is True
    assert policy.allows_refrigerated is False


def test_seeded_class_2_and_3_policies_allow_refrigerated() -> None:
    from apps.cargo.models import CargoClass, CargoPolicy

    for code in (CargoClassCode.CLASS_2, CargoClassCode.CLASS_3):
        cargo_class = CargoClass.objects.get(code=code)
        policy = CargoPolicy.objects.get(cargo_class=cargo_class)
        assert policy.allows_refrigerated is True
        assert policy.requires_packaging_attestation is True


def test_cargo_policy_allows_temperature_profile_helper() -> None:
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    policy = CargoPolicyFactory(
        cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=False
    )
    ambient = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    refrigerated = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    assert policy.allows_temperature_profile(ambient) is True
    assert policy.allows_temperature_profile(refrigerated) is False


def test_package_identifier_code_is_unique_and_barcode_like() -> None:
    identifier_1 = PackageIdentifierFactory()
    identifier_2 = PackageIdentifierFactory()
    assert identifier_1.code != identifier_2.code
    assert identifier_1.code.startswith("PKG-")


def test_package_identifier_renders_real_qr_svg_and_png() -> None:
    identifier = PackageIdentifierFactory()

    svg = identifier.render_qr_svg()
    assert "<svg" in svg
    assert "</svg>" in svg

    png_bytes = identifier.render_qr_png_bytes()
    # PNG magic number.
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png_bytes) > 0


def test_package_belongs_to_delivery_request_and_has_sequence() -> None:
    delivery_request = DeliveryRequestFactory()
    package = PackageFactory(delivery_request=delivery_request, sequence_number=1)
    assert package.delivery_request_id == delivery_request.id
    assert package in delivery_request.packages.all()


def test_packaging_attestation_notes_reject_prohibited_keywords() -> None:
    delivery_request = DeliveryRequestFactory()
    with pytest.raises(ValidationError):
        PackagingAttestationFactory(
            delivery_request=delivery_request,
            notes="This shipment includes a controlled substance for transport.",
        )


def test_packaging_attestation_clean_notes_saves_successfully() -> None:
    delivery_request = DeliveryRequestFactory()
    attestation = PackagingAttestationFactory(
        delivery_request=delivery_request, notes="Sealed and labeled per policy."
    )
    assert attestation.pk is not None


def test_temperature_profile_in_range_respects_both_bounds() -> None:
    from decimal import Decimal

    from apps.cargo.models import TemperatureProfile

    # Built directly (not via TemperatureProfileFactory's get_or_create,
    # which would fetch the already-migration-seeded "refrigerated" row and
    # silently ignore these explicit bounds) with a unique test-only code so
    # this test's exact min/max values are the ones actually persisted.
    profile = TemperatureProfile.objects.create(
        code="in_range_test_profile",
        name="In-Range Test",
        min_temp_c=Decimal("2.0"),
        max_temp_c=Decimal("8.0"),
    )
    assert profile.in_range(Decimal("5.0")) is True
    assert profile.in_range(Decimal("2.0")) is True
    assert profile.in_range(Decimal("8.0")) is True
    assert profile.in_range(Decimal("1.9")) is False
    assert profile.in_range(Decimal("8.1")) is False


def test_temperature_profile_in_range_with_no_bounds_is_always_true() -> None:
    from apps.cargo.models import TemperatureProfile

    profile = TemperatureProfile.objects.create(
        code="no_bounds_test_profile", name="No Bounds Test", min_temp_c=None, max_temp_c=None
    )
    assert profile.in_range(999) is True
    assert profile.in_range(-999) is True


def test_package_condition_check_has_any_concern_true_for_temperature_tripped() -> None:
    from apps.cargo.models import PackageConditionCheck, TemperatureIndicatorStatus

    package = PackageFactory()
    check = PackageConditionCheck.objects.create(
        package=package,
        stage="delivery",
        temperature_indicator_status=TemperatureIndicatorStatus.TRIPPED,
    )
    assert check.has_any_concern is True


def test_package_condition_check_unique_per_package_and_stage() -> None:
    from django.db import IntegrityError

    from apps.cargo.models import PackageConditionCheck

    package = PackageFactory()
    PackageConditionCheck.objects.create(package=package, stage="pickup")
    with pytest.raises(IntegrityError):
        PackageConditionCheck.objects.create(package=package, stage="pickup")
