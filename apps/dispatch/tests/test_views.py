"""HTTP-level tests for the dispatch board: login/role gating, the ranked
candidate list rendering, and the assign/reassign/offer actions driving the
real service layer through a POST (not a direct service-layer call)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import InternalRoleAssignmentFactory, UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    VehicleFactory,
)
from apps.deliveries.models import DeliveryStatus, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory

pytestmark = pytest.mark.django_db


def _ready_for_dispatch_delivery(*, pickup_zone=None):
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    pickup_facility = FacilityFactory(service_zone=pickup_zone)
    destination_facility = FacilityFactory()
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.PICKUP,
        sequence=1,
        facility=pickup_facility,
    )
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.DESTINATION,
        sequence=2,
        facility=destination_facility,
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    return delivery_request, cargo_class


def _eligible_courier(cargo_class, zone):
    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True, current_service_zone=zone)
    return courier


def _dispatcher_user() -> object:
    assignment = InternalRoleAssignmentFactory()
    return assignment.user


def test_dispatch_board_requires_login(client: Client) -> None:
    response = client.get(reverse("dispatch-board"))
    assert response.status_code == 302
    assert reverse("login") in response.url


def test_dispatch_board_forbidden_for_non_dispatcher(client: Client) -> None:
    user = UserFactory(username="not_a_dispatcher")
    client.force_login(user)
    response = client.get(reverse("dispatch-board"))
    assert response.status_code == 403


def test_dispatch_board_lists_unassigned_delivery(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, _cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"))

    assert response.status_code == 200
    assert str(delivery_request.pk)[:8] in response.content.decode()


def test_dispatch_board_detail_shows_ranked_candidates(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board-detail", kwargs={"pk": delivery_request.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    assert str(courier) in content


def test_dispatch_assign_action_assigns_and_redirects(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)
    client.force_login(_dispatcher_user())

    response = client.post(
        reverse("dispatch-assign", kwargs={"pk": delivery_request.pk}),
        data={"courier_id": courier.pk},
    )

    assert response.status_code == 302
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED
    assert DeliveryAssignment.objects.filter(
        delivery_request=delivery_request, courier=courier, status=AssignmentStatus.ACTIVE
    ).exists()


def test_dispatch_assign_action_ineligible_courier_shows_error_not_500(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, _cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    ineligible = CourierProfileFactory(status=CourierStatus.SUSPENDED)
    client.force_login(_dispatcher_user())

    response = client.post(
        reverse("dispatch-assign", kwargs={"pk": delivery_request.pk}),
        data={"courier_id": ineligible.pk, "reason": "Please assign anyway."},
    )

    assert response.status_code == 302  # redirects back with an error message, not a 500
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    assert not DeliveryAssignment.objects.filter(delivery_request=delivery_request).exists()


def test_dispatch_reassign_action_requires_reason(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    first_courier = _eligible_courier(cargo_class, zone)
    second_courier = _eligible_courier(cargo_class, zone)
    client.force_login(_dispatcher_user())
    client.post(
        reverse("dispatch-assign", kwargs={"pk": delivery_request.pk}),
        data={"courier_id": first_courier.pk},
    )

    response = client.post(
        reverse("dispatch-reassign", kwargs={"pk": delivery_request.pk}),
        data={"courier_id": second_courier.pk, "reason": ""},
    )

    assert response.status_code == 302
    assert (
        DeliveryAssignment.objects.get(
            delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
        ).courier_id
        == first_courier.pk
    )  # unchanged — blank reason was rejected
