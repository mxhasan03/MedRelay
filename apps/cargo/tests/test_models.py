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
