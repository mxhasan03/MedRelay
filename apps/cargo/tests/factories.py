"""factory_boy factories for cargo classes, policies, temperature profiles, and packages."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.cargo.models import (
    CargoClass,
    CargoClassCode,
    CargoPolicy,
    Package,
    PackageIdentifier,
    PackagingAttestation,
    TemperatureProfile,
    TemperatureProfileCode,
)


class CargoClassFactory(DjangoModelFactory):
    class Meta:
        model = CargoClass
        django_get_or_create = ("code",)

    code = CargoClassCode.CLASS_1
    name = "Class 1 — Documents & Non-Hazardous Supplies (Test)"


class CargoPolicyFactory(DjangoModelFactory):
    class Meta:
        model = CargoPolicy
        django_get_or_create = ("cargo_class",)

    cargo_class = factory.SubFactory(CargoClassFactory)
    requires_packaging_attestation = True
    allows_ambient = True
    allows_refrigerated = False


class TemperatureProfileFactory(DjangoModelFactory):
    class Meta:
        model = TemperatureProfile
        django_get_or_create = ("code",)

    code = TemperatureProfileCode.AMBIENT
    name = "Ambient (Test)"


class PackageFactory(DjangoModelFactory):
    class Meta:
        model = Package

    # String-path SubFactory to avoid a circular import: apps.deliveries.tests.factories
    # imports from this module (CargoClassFactory/TemperatureProfileFactory), so this
    # module cannot import apps.deliveries.tests.factories at module load time.
    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    cargo_class = factory.SubFactory(CargoClassFactory)
    temperature_profile = factory.SubFactory(TemperatureProfileFactory)
    sequence_number = 1


class PackageIdentifierFactory(DjangoModelFactory):
    class Meta:
        model = PackageIdentifier

    package = factory.SubFactory(PackageFactory)


class PackagingAttestationFactory(DjangoModelFactory):
    class Meta:
        model = PackagingAttestation

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
