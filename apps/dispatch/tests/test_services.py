"""Tests for `apps.dispatch.services` — the dispatch service API.

Covers the Phase 4 acceptance criteria (docs/CURRENT_STATUS.md "Phase 4"):

1. one delivery assigned atomically (the single-process/single-thread parts;
   see `apps.dispatch.tests.test_concurrency` for the genuine multi-threaded
   race test)
2. hard gates cannot be overridden — a dedicated test below tries to force
   an ineligible courier through every entry point (`assign_delivery`,
   `offer_delivery`, `reassign_delivery`) and confirms every one rejects it,
   even when a dispatcher-supplied override `reason` is provided.
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

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
from apps.dispatch.exceptions import AssignmentConflictError, IneligibleCourierError
from apps.dispatch.models import (
    AssignmentStatus,
    DeliveryAssignment,
    DispatchOverride,
    DispatchOverrideType,
    DispatchRecommendation,
    JobOffer,
    RoutePlan,
)
from apps.dispatch.services import (
    assign_delivery,
    at_risk_delivery_ids,
    offer_delivery,
    reassign_delivery,
    recommend_couriers,
)
from apps.dispatch.tests.factories import SLAProfileFactory
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


def _ineligible_courier():
    """A courier who fails a hard filter (account not active) — used for the
    "hard gates cannot be overridden" tests."""
    return CourierProfileFactory(status=CourierStatus.SUSPENDED)


@pytest.fixture(autouse=True)
def _sla_profiles(db):
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    SLAProfileFactory(service_level="same_day", min_slack_minutes=30)
    SLAProfileFactory(service_level="stat", min_slack_minutes=15)


# --- recommend_couriers -------------------------------------------------------


def test_recommend_couriers_persists_a_recommendation_and_candidate_rows() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    _eligible_courier(cargo_class, zone)

    candidates = recommend_couriers(delivery_request.pk)

    assert len(candidates) >= 1
    recommendation = DispatchRecommendation.objects.get(delivery_request=delivery_request)
    assert recommendation.candidate_count == len(candidates)
    assert recommendation.candidates.count() == len(candidates)


def test_recommend_couriers_persist_false_leaves_no_audit_row() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    _eligible_courier(cargo_class, zone)

    recommend_couriers(delivery_request.pk, persist=False)

    assert not DispatchRecommendation.objects.filter(delivery_request=delivery_request).exists()


# --- assign_delivery -----------------------------------------------------------


def test_assign_delivery_creates_active_assignment_and_transitions_to_assigned() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)

    assignment = assign_delivery(delivery_request.pk, courier.pk, None)

    delivery_request.refresh_from_db()
    assert assignment.status == AssignmentStatus.ACTIVE
    assert assignment.courier_id == courier.pk
    assert delivery_request.status == DeliveryStatus.ASSIGNED
    assert RoutePlan.objects.filter(delivery_request=delivery_request).exists()
    route_plan = RoutePlan.objects.get(delivery_request=delivery_request)
    assert route_plan.legs.count() == 2


def test_assign_delivery_to_top_ranked_candidate_needs_no_reason() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)

    assignment = assign_delivery(delivery_request.pk, courier.pk, None)

    assert assignment is not None
    assert not DispatchOverride.objects.filter(delivery_request=delivery_request).exists()


def test_assign_delivery_to_non_top_ranked_candidate_requires_a_reason() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    _eligible_courier(cargo_class, zone)  # the top-ranked candidate
    second_courier = _eligible_courier(cargo_class, zone)
    # Give the second courier a worse (but still eligible — capacity 2, not
    # exceeded) workload profile so it is not tied for first place with the
    # first candidate.
    second_courier.availability.max_concurrent_deliveries = 2
    second_courier.availability.save()
    DeliveryAssignment.objects.create(
        delivery_request=DeliveryRequestFactory(),
        courier=second_courier,
        status=AssignmentStatus.ACTIVE,
    )

    with pytest.raises(ValueError, match="reason is required"):
        assign_delivery(delivery_request.pk, second_courier.pk, None)

    with_reason = assign_delivery(
        delivery_request.pk, second_courier.pk, None, reason="Closer to the facility today."
    )
    assert with_reason.courier_id == second_courier.pk
    override = DispatchOverride.objects.get(delivery_request=delivery_request)
    assert override.override_type == DispatchOverrideType.NOT_TOP_RANKED
    assert override.chosen_courier_id == second_courier.pk


def test_assign_delivery_raises_when_delivery_not_in_assignable_status() -> None:
    delivery_request = DeliveryRequestFactory()  # still DRAFT
    courier = CourierProfileFactory()

    with pytest.raises(AssignmentConflictError):
        assign_delivery(delivery_request.pk, courier.pk, None)


def test_assign_delivery_raises_when_already_assigned() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    first_courier = _eligible_courier(cargo_class, zone)
    second_courier = _eligible_courier(cargo_class, zone)
    assign_delivery(delivery_request.pk, first_courier.pk, None)

    with pytest.raises(AssignmentConflictError):
        assign_delivery(delivery_request.pk, second_courier.pk, None)


# --- offer_delivery --------------------------------------------------------------


def test_offer_delivery_creates_offers_and_transitions_to_offered() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier_a = _eligible_courier(cargo_class, zone)
    courier_b = _eligible_courier(cargo_class, zone)
    expires_at = timezone.now() + datetime.timedelta(minutes=30)

    offers = offer_delivery(delivery_request.pk, [courier_a.pk, courier_b.pk], expires_at)

    delivery_request.refresh_from_db()
    assert len(offers) == 2
    assert delivery_request.status == DeliveryStatus.OFFERED
    assert JobOffer.objects.filter(delivery_request=delivery_request).count() == 2


def test_offer_delivery_raises_for_unknown_courier_id() -> None:
    zone = ServiceZoneFactory()
    delivery_request, _cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    expires_at = timezone.now() + datetime.timedelta(minutes=30)

    with pytest.raises(ValueError, match="Unknown courier"):
        offer_delivery(delivery_request.pk, [999999], expires_at)


# --- reassign_delivery -----------------------------------------------------------


def test_reassign_delivery_requires_a_reason() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    first_courier = _eligible_courier(cargo_class, zone)
    second_courier = _eligible_courier(cargo_class, zone)
    assign_delivery(delivery_request.pk, first_courier.pk, None)

    with pytest.raises(ValueError, match="reason is required"):
        reassign_delivery(delivery_request.pk, second_courier.pk, None, "")


def test_reassign_delivery_swaps_assignment_and_records_override() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    first_courier = _eligible_courier(cargo_class, zone)
    second_courier = _eligible_courier(cargo_class, zone)
    original_assignment = assign_delivery(delivery_request.pk, first_courier.pk, None)

    new_assignment = reassign_delivery(
        delivery_request.pk, second_courier.pk, None, "First courier called in sick."
    )

    original_assignment.refresh_from_db()
    delivery_request.refresh_from_db()
    assert original_assignment.status == AssignmentStatus.REASSIGNED
    assert original_assignment.unassigned_at is not None
    assert new_assignment.status == AssignmentStatus.ACTIVE
    assert new_assignment.courier_id == second_courier.pk
    assert delivery_request.status == DeliveryStatus.ASSIGNED
    override = DispatchOverride.objects.get(
        delivery_request=delivery_request, override_type=DispatchOverrideType.REASSIGNMENT
    )
    assert override.previous_courier_id == first_courier.pk
    assert override.chosen_courier_id == second_courier.pk
    # Exactly one ACTIVE assignment row exists at any time.
    assert (
        DeliveryAssignment.objects.filter(
            delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
        ).count()
        == 1
    )


def test_reassign_delivery_raises_if_no_active_assignment() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)

    with pytest.raises(AssignmentConflictError):
        reassign_delivery(delivery_request.pk, courier.pk, None, "Some reason.")


def test_reassign_delivery_rejects_reassigning_to_the_same_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)
    assign_delivery(delivery_request.pk, courier.pk, None)

    with pytest.raises(ValueError, match="currently assigned"):
        reassign_delivery(delivery_request.pk, courier.pk, None, "No-op reassignment.")


# --- Hard gates cannot be overridden, at every entry point -----------------------


def test_hard_eligibility_gate_cannot_be_overridden_via_assign_delivery() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    ineligible = _ineligible_courier()

    with pytest.raises(IneligibleCourierError):
        assign_delivery(delivery_request.pk, ineligible.pk, None, reason="I really want this one.")

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    assert not DeliveryAssignment.objects.filter(delivery_request=delivery_request).exists()


def test_hard_eligibility_gate_cannot_be_overridden_via_offer_delivery() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    eligible = _eligible_courier(cargo_class, zone)
    ineligible = _ineligible_courier()
    expires_at = timezone.now() + datetime.timedelta(minutes=30)

    # Even mixed in with a genuinely eligible candidate, one ineligible
    # courier in the batch must reject the whole call — no partial offers.
    with pytest.raises(IneligibleCourierError):
        offer_delivery(delivery_request.pk, [eligible.pk, ineligible.pk], expires_at)

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    assert not JobOffer.objects.filter(delivery_request=delivery_request).exists()


def test_hard_eligibility_gate_cannot_be_overridden_via_reassign_delivery() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    original_courier = _eligible_courier(cargo_class, zone)
    ineligible = _ineligible_courier()
    assign_delivery(delivery_request.pk, original_courier.pk, None)

    with pytest.raises(IneligibleCourierError):
        reassign_delivery(
            delivery_request.pk, ineligible.pk, None, "Dispatcher insists on this courier."
        )

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED
    active = DeliveryAssignment.objects.get(
        delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
    )
    assert active.courier_id == original_courier.pk  # unchanged


# --- at_risk_delivery_ids --------------------------------------------------------


def test_at_risk_delivery_ids_flags_infeasible_delivery() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    delivery_request.required_delivery_by = (
        delivery_request.pickup_window_start - datetime.timedelta(minutes=1)
    )
    delivery_request.save(update_fields=["required_delivery_by"])
    _eligible_courier(cargo_class, zone)

    at_risk = at_risk_delivery_ids()

    assert delivery_request.pk in at_risk


def test_at_risk_delivery_ids_excludes_comfortably_feasible_delivery() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    delivery_request.required_delivery_by = (
        delivery_request.pickup_window_start + datetime.timedelta(hours=6)
    )
    delivery_request.save(update_fields=["required_delivery_by"])
    _eligible_courier(cargo_class, zone)

    at_risk = at_risk_delivery_ids()

    assert delivery_request.pk not in at_risk
