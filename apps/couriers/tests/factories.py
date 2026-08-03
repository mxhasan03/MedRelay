"""factory_boy factories for courier profiles, credentials, training, vehicles,
equipment, cargo authorizations, and availability."""

from __future__ import annotations

import datetime

import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.cargo.tests.factories import CargoClassFactory
from apps.couriers.models import (
    CargoAuthorization,
    CourierAvailability,
    CourierCredential,
    CourierCredentialStatus,
    CourierCredentialType,
    CourierProfile,
    CourierStatus,
    Equipment,
    EquipmentType,
    TrainingRecord,
    TrainingRecordType,
    Vehicle,
    VehicleType,
)
from apps.facilities.tests.factories import ServiceZoneFactory


class CourierProfileFactory(DjangoModelFactory):
    class Meta:
        model = CourierProfile
        django_get_or_create = ("user",)

    user = factory.SubFactory(UserFactory)
    status = CourierStatus.APPROVED


class CourierCredentialFactory(DjangoModelFactory):
    class Meta:
        model = CourierCredential

    courier = factory.SubFactory(CourierProfileFactory)
    credential_type = CourierCredentialType.DRIVER_LICENSE
    status = CourierCredentialStatus.APPROVED
    issued_on = factory.LazyFunction(lambda: datetime.date(2026, 1, 1))
    expires_on = factory.LazyFunction(lambda: datetime.date(2027, 1, 1))
    evidence_reference = "synthetic-credential-demo.pdf"


class TrainingRecordFactory(DjangoModelFactory):
    class Meta:
        model = TrainingRecord

    courier = factory.SubFactory(CourierProfileFactory)
    training_type = TrainingRecordType.GENERAL_ORIENTATION
    completed_on = factory.LazyFunction(lambda: datetime.date(2026, 1, 1))


class VehicleFactory(DjangoModelFactory):
    class Meta:
        model = Vehicle

    courier = factory.SubFactory(CourierProfileFactory)
    vehicle_type = VehicleType.VAN
    plate_number = factory.Sequence(lambda n: f"DEMO-{n:04d}")
    is_active = True
    supports_refrigeration = False


class EquipmentFactory(DjangoModelFactory):
    class Meta:
        model = Equipment

    courier = factory.SubFactory(CourierProfileFactory)
    equipment_type = EquipmentType.INSULATED_CONTAINER
    is_active = True
    supports_refrigeration = False


class CargoAuthorizationFactory(DjangoModelFactory):
    class Meta:
        model = CargoAuthorization
        django_get_or_create = ("courier", "cargo_class")

    courier = factory.SubFactory(CourierProfileFactory)
    cargo_class = factory.SubFactory(CargoClassFactory)
    supports_refrigeration = False
    is_active = True


class CourierAvailabilityFactory(DjangoModelFactory):
    class Meta:
        model = CourierAvailability
        django_get_or_create = ("courier",)

    courier = factory.SubFactory(CourierProfileFactory)
    is_online = True
    current_service_zone = factory.SubFactory(ServiceZoneFactory)
    max_concurrent_deliveries = 1
