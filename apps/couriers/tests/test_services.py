"""Tests for apps.couriers.services — the pickup/transit status-advancement
authorization + single-step guard, courier-portal access control, the
availability self-service updater, the credential-expiration-summary shared
function, the delivery progress-tracker helper, and the cargo-handling
boundary statement.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import InternalRole
from apps.accounts.tests.factories import InternalRoleAssignmentFactory, UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import (
    CourierCredentialStatus,
    CourierCredentialType,
    CourierStatus,
    IdentityReviewStatus,
)
from apps.couriers.services import (
    advance_delivery_status,
    can_access_courier_portal,
    cargo_handling_boundary_text,
    credential_expiration_summary,
    delivery_timeline_steps,
    update_courier_availability,
)
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    VehicleFactory,
)
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryStatus, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.services import assign_delivery
from apps.facilities.tests.factories import ServiceZoneFactory

pytestmark = pytest.mark.django_db


def _assigned_delivery_and_courier():
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)

    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True)

    assign_delivery(delivery_request.pk, courier.pk, None)
    delivery_request.refresh_from_db()
    return delivery_request, courier


def test_advance_delivery_status_by_the_assigned_courier_succeeds() -> None:
    delivery_request, courier = _assigned_delivery_and_courier()

    result = advance_delivery_status(
        delivery_request.pk,
        courier,
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        actor=courier.user,
    )

    assert result.status == DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP


def test_advance_delivery_status_full_sequence_reaches_at_destination() -> None:
    delivery_request, courier = _assigned_delivery_and_courier()

    for to_status in (
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
        DeliveryStatus.AT_DESTINATION,
    ):
        result = advance_delivery_status(
            delivery_request.pk, courier, to_status, actor=courier.user
        )
        assert result.status == to_status


def test_advance_delivery_status_rejects_a_different_courier() -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    other_courier = CourierProfileFactory()

    with pytest.raises(PermissionError):
        advance_delivery_status(
            delivery_request.pk,
            other_courier,
            DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
            actor=other_courier.user,
        )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED


def test_advance_delivery_status_allows_internal_ops_override() -> None:
    """An internal ops user with dispatch-board access (apps.organizations.
    services.can_dispatch) may advance a delivery even though they are not
    the assigned courier — covering "or an internal ops override" per this
    module's docstring."""
    delivery_request, courier = _assigned_delivery_and_courier()
    other_courier = CourierProfileFactory()
    ops_user = UserFactory()
    InternalRoleAssignmentFactory(user=ops_user, role=InternalRole.OPERATIONS_MANAGER)

    result = advance_delivery_status(
        delivery_request.pk,
        other_courier,
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        actor=ops_user,
    )

    assert result.status == DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP


def test_advance_delivery_status_rejects_skipping_a_step() -> None:
    delivery_request, courier = _assigned_delivery_and_courier()

    with pytest.raises(InvalidTransitionError):
        advance_delivery_status(
            delivery_request.pk, courier, DeliveryStatus.PICKED_UP, actor=courier.user
        )


def test_can_access_courier_portal_requires_courier_profile() -> None:
    plain_user = UserFactory()
    assert can_access_courier_portal(plain_user) is False

    courier = CourierProfileFactory()
    assert can_access_courier_portal(courier.user) is True


# --- update_courier_availability ---------------------------------------


def test_update_courier_availability_creates_row_on_first_use() -> None:
    courier = CourierProfileFactory()
    zone = ServiceZoneFactory()

    availability = update_courier_availability(
        courier,
        is_online=True,
        current_service_zone_id=zone.pk,
        shift_start="09:00",
        shift_end="17:00",
        max_concurrent_deliveries=3,
    )

    assert availability.courier_id == courier.pk
    assert availability.is_online is True
    assert availability.current_service_zone_id == zone.pk
    assert availability.shift_start == datetime.time(9, 0)
    assert availability.shift_end == datetime.time(17, 0)
    assert availability.max_concurrent_deliveries == 3


def test_update_courier_availability_clears_zone_and_shift_when_blank() -> None:
    zone = ServiceZoneFactory()
    availability = CourierAvailabilityFactory(
        current_service_zone=zone, shift_start=datetime.time(9, 0), shift_end=datetime.time(17, 0)
    )

    updated = update_courier_availability(
        availability.courier,
        is_online=False,
        current_service_zone_id=None,
        shift_start=None,
        shift_end=None,
        max_concurrent_deliveries=None,
    )

    assert updated.is_online is False
    assert updated.current_service_zone_id is None
    assert updated.shift_start is None
    assert updated.shift_end is None


def test_update_courier_availability_rejects_unknown_zone() -> None:
    courier = CourierProfileFactory()

    with pytest.raises(ValidationError):
        update_courier_availability(
            courier,
            is_online=True,
            current_service_zone_id=999999,
            shift_start=None,
            shift_end=None,
            max_concurrent_deliveries=None,
        )


def test_update_courier_availability_rejects_malformed_shift_time() -> None:
    courier = CourierProfileFactory()

    with pytest.raises(ValidationError):
        update_courier_availability(
            courier,
            is_online=True,
            current_service_zone_id=None,
            shift_start="not-a-time",
            shift_end=None,
            max_concurrent_deliveries=None,
        )


def test_update_courier_availability_rejects_negative_capacity() -> None:
    courier = CourierProfileFactory()

    with pytest.raises(ValidationError):
        update_courier_availability(
            courier,
            is_online=True,
            current_service_zone_id=None,
            shift_start=None,
            shift_end=None,
            max_concurrent_deliveries=-1,
        )


# --- credential_expiration_summary ---------------------------------------


def test_credential_expiration_summary_scoped_to_one_courier() -> None:
    today = datetime.date.today()
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()
    expired_a = CourierCredentialFactory(
        courier=courier_a,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=1),
    )
    CourierCredentialFactory(
        courier=courier_b,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=1),
    )

    summary = credential_expiration_summary(courier=courier_a)

    assert [c.pk for c in summary.expired] == [expired_a.pk]


def test_credential_expiration_summary_across_all_couriers_when_unscoped() -> None:
    today = datetime.date.today()
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()
    expired_a = CourierCredentialFactory(
        courier=courier_a,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=1),
    )
    expired_b = CourierCredentialFactory(
        courier=courier_b,
        credential_type=CourierCredentialType.INSURANCE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=1),
    )

    summary = credential_expiration_summary()

    expired_ids = {c.pk for c in summary.expired}
    assert {expired_a.pk, expired_b.pk} <= expired_ids


# --- delivery_timeline_steps ---------------------------------------


def test_delivery_timeline_steps_marks_completed_current_and_upcoming() -> None:
    delivery_request, _courier = _assigned_delivery_and_courier()
    advance_delivery_status(
        delivery_request.pk,
        _courier,
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        actor=_courier.user,
    )
    delivery_request.refresh_from_db()

    steps = delivery_timeline_steps(delivery_request)
    by_code = {step.code: step.state for step in steps}

    assert by_code[DeliveryStatus.ASSIGNED] == "completed"
    assert by_code[DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP] == "current"
    assert by_code[DeliveryStatus.AT_PICKUP] == "upcoming"
    assert by_code[DeliveryStatus.DELIVERED] == "upcoming"


def test_delivery_timeline_steps_all_upcoming_for_exception_state() -> None:
    delivery_request, _courier = _assigned_delivery_and_courier()
    delivery_request.status = DeliveryStatus.INCIDENT_HOLD
    delivery_request.save(update_fields=["status"])

    steps = delivery_timeline_steps(delivery_request)

    assert all(step.state == "upcoming" for step in steps)


# --- cargo_handling_boundary_text ---------------------------------------


def test_cargo_handling_boundary_text_varies_by_cargo_class_and_temperature() -> None:
    ambient_class = CargoClassFactory(
        code=CargoClassCode.CLASS_1, name="Class 1 — Documents & Non-Hazardous Supplies"
    )
    CargoPolicyFactory(
        cargo_class=ambient_class,
        allows_ambient=True,
        allows_refrigerated=False,
        notes="Documents/non-hazardous supplies are not eligible for refrigerated transport.",
    )
    ambient_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT, name="Ambient")
    ambient_delivery = DeliveryRequestFactory(
        cargo_class=ambient_class, temperature_profile=ambient_profile
    )

    refrigerated_class = CargoClassFactory(
        code=CargoClassCode.CLASS_2, name="Class 2 — Approved Routine Specimens"
    )
    CargoPolicyFactory(
        cargo_class=refrigerated_class,
        allows_ambient=True,
        allows_refrigerated=True,
        notes="Routine specimens may require cold-chain (refrigerated) transport.",
    )
    refrigerated_profile = TemperatureProfileFactory(
        code=TemperatureProfileCode.REFRIGERATED, name="Refrigerated"
    )
    refrigerated_delivery = DeliveryRequestFactory(
        cargo_class=refrigerated_class, temperature_profile=refrigerated_profile
    )

    ambient_text = cargo_handling_boundary_text(ambient_delivery)
    refrigerated_text = cargo_handling_boundary_text(refrigerated_delivery)

    assert "Class 1" in ambient_text
    assert "No active temperature control is required" in ambient_text
    assert "may not open" in ambient_text

    assert "Class 2" in refrigerated_text
    assert "insulated/refrigerated container" in refrigerated_text
    assert "cold-chain" in refrigerated_text
    assert "may not open" in refrigerated_text

    assert ambient_text != refrigerated_text


def test_cargo_handling_boundary_text_handles_missing_cargo_class() -> None:
    delivery_request = DeliveryRequestFactory(cargo_class=None, temperature_profile=None)

    text = cargo_handling_boundary_text(delivery_request)

    assert "No cargo class" in text
