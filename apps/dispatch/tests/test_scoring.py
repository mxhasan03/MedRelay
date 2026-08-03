"""Tests for `apps.dispatch.scoring` — the explainable weighted score."""

from __future__ import annotations

import pytest

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
from apps.dispatch.scoring import rank_candidates, score_candidate
from apps.dispatch.tests.factories import DeliveryAssignmentFactory, SLAProfileFactory
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


def test_ineligible_courier_gets_no_total_score_but_full_explanation() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)
    courier.status = CourierStatus.SUSPENDED
    courier.save()

    candidate = score_candidate(courier, delivery_request)

    assert candidate.eligible is False
    assert candidate.total_score is None
    assert len(candidate.hard_failure_reasons) >= 1
    # Still gets a full factor breakdown with human-readable reasons.
    assert len(candidate.factors) == 8
    assert all(f.reason for f in candidate.factors)
    assert len(candidate.reasons) >= len(candidate.factors)


def test_eligible_courier_gets_a_numeric_score_and_reasons() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)

    candidate = score_candidate(courier, delivery_request)

    assert candidate.eligible is True
    assert candidate.total_score is not None
    assert 0 <= candidate.total_score <= 100
    assert candidate.reasons  # non-empty, human-readable


def test_higher_workload_scores_lower_active_workload_factor() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    idle_courier = _eligible_courier(cargo_class, zone)
    idle_courier.availability.max_concurrent_deliveries = 2
    idle_courier.availability.save()

    busy_courier = _eligible_courier(cargo_class, zone)
    busy_courier.availability.max_concurrent_deliveries = 2
    busy_courier.availability.save()
    DeliveryAssignmentFactory(courier=busy_courier)

    idle_candidate = score_candidate(idle_courier, delivery_request)
    busy_candidate = score_candidate(busy_courier, delivery_request)

    idle_workload_factor = next(f for f in idle_candidate.factors if f.name == "active_workload")
    busy_workload_factor = next(f for f in busy_candidate.factors if f.name == "active_workload")
    assert idle_workload_factor.raw_score > busy_workload_factor.raw_score


def test_rank_candidates_sorts_eligible_before_ineligible_and_by_score() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    eligible_one = _eligible_courier(cargo_class, zone)
    eligible_two = _eligible_courier(cargo_class, zone)
    ineligible = CourierProfileFactory(status=CourierStatus.APPLICANT)

    ranked = rank_candidates(delivery_request)
    ranked_ids = [c.courier.pk for c in ranked]

    assert ranked_ids.index(eligible_one.pk) < ranked_ids.index(ineligible.pk)
    assert ranked_ids.index(eligible_two.pk) < ranked_ids.index(ineligible.pk)
    eligible_flags = [c.eligible for c in ranked]
    # Every eligible candidate is ranked ahead of every ineligible one.
    assert eligible_flags == sorted(eligible_flags, reverse=True)
    scores = [c.total_score for c in ranked if c.eligible]
    assert scores == sorted(scores, reverse=True)
