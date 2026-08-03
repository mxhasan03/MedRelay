"""Tests for `apps.dispatch.sla` — synthetic ETA-to-pickup, transit-time, and
SLA-feasibility calculation."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.couriers.tests.factories import CourierAvailabilityFactory, CourierProfileFactory
from apps.deliveries.models import StopType
from apps.deliveries.tests.factories import (
    DEFAULT_PICKUP_START,
    DeliveryRequestFactory,
    DeliveryStopFactory,
    PricingRuleFactory,
)
from apps.dispatch.sla import (
    AT_RISK,
    ETA_DIFFERENT_ZONE_MINUTES,
    ETA_SAME_ZONE_MINUTES,
    ETA_UNKNOWN_ZONE_MINUTES,
    FEASIBLE,
    INFEASIBLE,
    compute_sla_estimate,
    estimate_eta_to_pickup_minutes,
    estimate_transit_minutes,
)
from apps.dispatch.tests.factories import SLAProfileFactory
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory

pytestmark = pytest.mark.django_db


def _delivery_with_stops(**kwargs):
    delivery_request = DeliveryRequestFactory(**kwargs)
    pickup = DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1
    )
    destination = DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    return delivery_request, pickup, destination


def test_eta_same_zone_is_the_lowest_tier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, pickup, _ = _delivery_with_stops()
    pickup.facility.service_zone = zone
    pickup.facility.save()
    courier = CourierProfileFactory()
    CourierAvailabilityFactory(courier=courier, current_service_zone=zone)

    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)

    assert eta == ETA_SAME_ZONE_MINUTES


def test_eta_different_zone_is_the_highest_tier() -> None:
    zone_a = ServiceZoneFactory()
    zone_b = ServiceZoneFactory()
    delivery_request, pickup, _ = _delivery_with_stops()
    pickup.facility.service_zone = zone_a
    pickup.facility.save()
    courier = CourierProfileFactory()
    CourierAvailabilityFactory(courier=courier, current_service_zone=zone_b)

    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)

    assert eta == ETA_DIFFERENT_ZONE_MINUTES


def test_eta_unknown_zone_when_no_zone_data() -> None:
    delivery_request, pickup, _ = _delivery_with_stops()
    pickup.facility.service_zone = None
    pickup.facility.save()
    courier = CourierProfileFactory()

    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)

    assert eta == ETA_UNKNOWN_ZONE_MINUTES


def test_eta_unknown_zone_when_no_pickup_stop() -> None:
    delivery_request = DeliveryRequestFactory()
    courier = CourierProfileFactory()

    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)

    assert eta == ETA_UNKNOWN_ZONE_MINUTES


def test_transit_minutes_uses_haversine_distance_and_average_speed() -> None:
    PricingRuleFactory(key="average_speed_kmh", amount=Decimal("30.00"))
    pickup_facility = FacilityFactory(latitude=Decimal("40.7128"), longitude=Decimal("-74.0060"))
    destination_facility = FacilityFactory(
        latitude=Decimal("40.6782"), longitude=Decimal("-73.9442")
    )
    delivery_request = DeliveryRequestFactory()
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

    transit_minutes = estimate_transit_minutes(delivery_request)

    assert transit_minutes > 0


def test_compute_sla_estimate_feasible_when_ample_slack() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    zone = ServiceZoneFactory()
    delivery_request, pickup, _ = _delivery_with_stops(
        required_delivery_by=DEFAULT_PICKUP_START + datetime.timedelta(hours=6)
    )
    pickup.facility.service_zone = zone
    pickup.facility.save()
    courier = CourierProfileFactory()
    CourierAvailabilityFactory(courier=courier, current_service_zone=zone)

    estimate = compute_sla_estimate(courier, delivery_request)

    assert estimate.feasibility == FEASIBLE
    assert estimate.sla_slack_minutes > 60


def test_compute_sla_estimate_infeasible_when_deadline_already_passed() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    delivery_request, _, _ = _delivery_with_stops(
        required_delivery_by=DEFAULT_PICKUP_START - datetime.timedelta(minutes=1)
    )
    courier = CourierProfileFactory()

    estimate = compute_sla_estimate(courier, delivery_request)

    assert estimate.feasibility == INFEASIBLE
    assert estimate.sla_slack_minutes < 0


def test_compute_sla_estimate_at_risk_when_slack_below_minimum() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=120)
    zone = ServiceZoneFactory()
    # eta (15) + transit (fallback ~10 min at 30km/h over 5km) < required
    # window, but by less than the 120-minute minimum buffer.
    delivery_request, pickup, _ = _delivery_with_stops(
        required_delivery_by=DEFAULT_PICKUP_START + datetime.timedelta(minutes=45)
    )
    pickup.facility.service_zone = zone
    pickup.facility.save()
    courier = CourierProfileFactory()
    CourierAvailabilityFactory(courier=courier, current_service_zone=zone)

    estimate = compute_sla_estimate(courier, delivery_request)

    assert estimate.feasibility == AT_RISK
    assert 0 <= estimate.sla_slack_minutes < 120


def test_compute_sla_estimate_anchors_on_pickup_window_start_not_wall_clock() -> None:
    """Determinism: identical inputs always produce an identical estimate,
    regardless of the wall-clock time the calculation happens to run at —
    the same design choice apps.deliveries.pricing's after-hours surcharge
    made."""
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    delivery_request, _, _ = _delivery_with_stops()
    courier = CourierProfileFactory()

    first = compute_sla_estimate(courier, delivery_request)
    second = compute_sla_estimate(courier, delivery_request)

    assert first == second
    assert first.reference_instant == delivery_request.pickup_window_start


def test_compute_sla_estimate_respects_explicit_reference_instant() -> None:
    SLAProfileFactory(service_level="scheduled", min_slack_minutes=60)
    delivery_request, _, _ = _delivery_with_stops()
    courier = CourierProfileFactory()
    explicit_instant = DEFAULT_PICKUP_START + datetime.timedelta(hours=1)

    estimate = compute_sla_estimate(courier, delivery_request, reference_instant=explicit_instant)

    assert estimate.reference_instant == explicit_instant
