"""HTTP-level tests for the dispatch board: login/role gating, the ranked
candidate list rendering, and the assign/reassign/offer actions driving the
real service layer through a POST (not a direct service-layer call)."""

from __future__ import annotations

import datetime

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
from apps.dispatch.tests.factories import DeliveryAssignmentFactory
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory
from apps.incidents.models import IncidentSeverity, IncidentStatus
from apps.incidents.tests.factories import IncidentFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.temperature.tests.factories import TemperatureExcursionFactory
from apps.tracking.tests.factories import CourierLocationPingFactory

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


# --- sort/filter query params (UI cleanup pass) --------------------------------


def test_dispatch_board_unassigned_sort_by_required_delivery_by(client: Client) -> None:
    zone = ServiceZoneFactory()
    earlier, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    later, _ = _ready_for_dispatch_delivery(pickup_zone=zone)
    earlier.required_delivery_by = later.required_delivery_by - datetime.timedelta(days=1)
    earlier.save(update_fields=["required_delivery_by"])
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"), {"unassigned_sort": "required_delivery_by"})

    content = response.content.decode()
    assert content.index(str(earlier.pk)[:8]) < content.index(str(later.pk)[:8])


def test_dispatch_board_unassigned_org_filter(client: Client) -> None:
    zone = ServiceZoneFactory()
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    delivery_a, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    delivery_a.organization = org_a
    delivery_a.save(update_fields=["organization"])
    delivery_b, _ = _ready_for_dispatch_delivery(pickup_zone=zone)
    delivery_b.organization = org_b
    delivery_b.save(update_fields=["organization"])
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"), {"unassigned_org": org_a.pk})

    content = response.content.decode()
    assert str(delivery_a.pk)[:8] in content
    assert str(delivery_b.pk)[:8] not in content


def test_dispatch_board_at_risk_only_filter(client: Client) -> None:
    zone = ServiceZoneFactory()
    at_risk_delivery, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    at_risk_delivery.required_delivery_by = (
        at_risk_delivery.pickup_window_start - datetime.timedelta(minutes=1)
    )
    at_risk_delivery.save(update_fields=["required_delivery_by"])
    _eligible_courier(cargo_class, zone)
    feasible_delivery, cargo_class_2 = _ready_for_dispatch_delivery(pickup_zone=zone)
    feasible_delivery.required_delivery_by = (
        feasible_delivery.pickup_window_start + datetime.timedelta(hours=6)
    )
    feasible_delivery.save(update_fields=["required_delivery_by"])
    _eligible_courier(cargo_class_2, zone)
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"), {"unassigned_at_risk": "1"})

    content = response.content.decode()
    assert str(at_risk_delivery.pk)[:8] in content
    assert str(feasible_delivery.pk)[:8] not in content
    assert "INFEASIBLE" in content


# --- incident/temperature/location surfacing (UI cleanup pass) ----------------


def test_dispatch_board_list_shows_open_incident_badge_not_resolved_one(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, _cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    IncidentFactory(
        delivery_request=delivery_request,
        severity=IncidentSeverity.MODERATE,
        status=IncidentStatus.OPEN,
    )
    IncidentFactory(
        delivery_request=delivery_request,
        severity=IncidentSeverity.MINOR,
        status=IncidentStatus.RESOLVED,
    )
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"))

    content = response.content.decode()
    assert "1 open" in content  # only the OPEN incident counted, not the RESOLVED one


def test_dispatch_board_list_shows_temperature_alert_badge(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, _cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    TemperatureExcursionFactory(
        reading__delivery_request=delivery_request, delivery_request=delivery_request
    )
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board"))

    content = response.content.decode()
    assert "temperature alert" in content.lower() or "1 alert" in content


def test_dispatch_board_detail_shows_no_location_data_for_courier_without_pings(
    client: Client,
) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    _eligible_courier(cargo_class, zone)
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board-detail", kwargs={"pk": delivery_request.pk}))

    assert "No location data" in response.content.decode()


def test_dispatch_board_detail_shows_last_seen_for_courier_with_a_ping(client: Client) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)
    CourierLocationPingFactory(
        assignment=DeliveryAssignmentFactory(courier=courier), courier=courier
    )
    client.force_login(_dispatcher_user())

    response = client.get(reverse("dispatch-board-detail", kwargs={"pk": delivery_request.pk}))

    content = response.content.decode()
    assert "No location data" not in content
    assert "ago" in content or "just now" in content


def test_dispatch_board_detail_eligible_only_filter_hides_ineligible_candidates(
    client: Client,
) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    eligible = _eligible_courier(cargo_class, zone)
    ineligible = CourierProfileFactory(status=CourierStatus.SUSPENDED)
    client.force_login(_dispatcher_user())

    response = client.get(
        reverse("dispatch-board-detail", kwargs={"pk": delivery_request.pk}),
        {"eligible_only": "1"},
    )

    content = response.content.decode()
    assert str(eligible) in content
    assert str(ineligible) not in content


def test_dispatch_board_detail_candidate_sort_by_eta(client: Client) -> None:
    zone = ServiceZoneFactory()
    other_zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    same_zone_courier = _eligible_courier(cargo_class, zone)  # closer synthetic ETA
    far_courier = _eligible_courier(cargo_class, other_zone)  # farther synthetic ETA
    client.force_login(_dispatcher_user())

    response = client.get(
        reverse("dispatch-board-detail", kwargs={"pk": delivery_request.pk}),
        {"candidate_sort": "eta"},
    )

    content = response.content.decode()
    # `str(courier)` is identical for every courier in this test suite
    # (UserFactory always uses "Test"/"User" as first/last name) — the
    # per-row checkbox's `value="<courier.pk>"` is what actually
    # distinguishes the two rendered rows.
    assert content.index(str(same_zone_courier.pk)) < content.index(str(far_courier.pk))
