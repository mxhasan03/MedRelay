"""Tests for apps.couriers.services — the pickup/transit status-advancement
authorization + single-step guard, and courier-portal access control.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import InternalRole
from apps.accounts.tests.factories import InternalRoleAssignmentFactory, UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.services import advance_delivery_status, can_access_courier_portal
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
