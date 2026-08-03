"""Tests for the synthetic quote engine (apps.deliveries.pricing).

Covers the Phase 2 acceptance criteria: same inputs always produce the same
quote, and each surcharge component (service level, cargo/equipment,
after-hours, toll, return-trip) changes the total predictably.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import CargoClassFactory, TemperatureProfileFactory
from apps.deliveries.models import PricingRule, PricingRuleKey, ServiceLevel, StopType
from apps.deliveries.pricing import (
    calculate_quote_breakdown,
    is_after_hours,
    quote_delivery_request,
)
from apps.deliveries.tests.factories import (
    DEFAULT_PICKUP_START,
    DeliveryRequestFactory,
    DeliveryStopFactory,
)
from apps.facilities.tests.factories import FacilityFactory

pytestmark = pytest.mark.django_db


def _rule_amount(key: str) -> Decimal:
    return PricingRule.objects.get(key=key).amount


def _with_stops(delivery_request, *, pickup_borough="manhattan", destination_borough="manhattan"):
    pickup_facility = FacilityFactory(
        borough=pickup_borough, latitude="40.750000", longitude="-73.990000"
    )
    destination_facility = FacilityFactory(
        borough=destination_borough, latitude="40.760000", longitude="-73.980000"
    )
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
    return delivery_request


def test_quote_is_deterministic_for_identical_inputs() -> None:
    delivery_request = _with_stops(DeliveryRequestFactory())
    first = calculate_quote_breakdown(delivery_request)
    second = calculate_quote_breakdown(delivery_request)
    assert first == second


def test_service_level_surcharge_changes_total_predictably() -> None:
    scheduled = _with_stops(DeliveryRequestFactory(service_level=ServiceLevel.SCHEDULED))
    same_day = _with_stops(DeliveryRequestFactory(service_level=ServiceLevel.SAME_DAY))
    stat = _with_stops(DeliveryRequestFactory(service_level=ServiceLevel.STAT))

    scheduled_quote = calculate_quote_breakdown(scheduled)
    same_day_quote = calculate_quote_breakdown(same_day)
    stat_quote = calculate_quote_breakdown(stat)

    assert scheduled_quote.service_level_surcharge == Decimal("0.00")
    assert same_day_quote.service_level_surcharge == _rule_amount(PricingRuleKey.SAME_DAY_SURCHARGE)
    assert stat_quote.service_level_surcharge == _rule_amount(PricingRuleKey.STAT_SURCHARGE)

    assert same_day_quote.total_price - scheduled_quote.total_price == _rule_amount(
        PricingRuleKey.SAME_DAY_SURCHARGE
    )
    assert stat_quote.total_price - scheduled_quote.total_price == _rule_amount(
        PricingRuleKey.STAT_SURCHARGE
    )


def test_cargo_class_surcharge_changes_total_predictably() -> None:
    class_1 = CargoClassFactory(code=CargoClassCode.CLASS_1)
    class_2 = CargoClassFactory(code=CargoClassCode.CLASS_2)
    class_3 = CargoClassFactory(code=CargoClassCode.CLASS_3)

    dr_1 = _with_stops(DeliveryRequestFactory(cargo_class=class_1))
    dr_2 = _with_stops(DeliveryRequestFactory(cargo_class=class_2))
    dr_3 = _with_stops(DeliveryRequestFactory(cargo_class=class_3))

    quote_1 = calculate_quote_breakdown(dr_1)
    quote_2 = calculate_quote_breakdown(dr_2)
    quote_3 = calculate_quote_breakdown(dr_3)

    assert quote_1.cargo_equipment_surcharge == Decimal("0.00")
    assert quote_2.cargo_equipment_surcharge == _rule_amount(PricingRuleKey.CARGO_CLASS_2_SURCHARGE)
    assert quote_3.cargo_equipment_surcharge == _rule_amount(PricingRuleKey.CARGO_CLASS_3_SURCHARGE)


def test_refrigerated_surcharge_adds_to_cargo_equipment_surcharge() -> None:
    ambient = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    refrigerated = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)

    dr_ambient = _with_stops(DeliveryRequestFactory(temperature_profile=ambient))
    dr_refrigerated = _with_stops(DeliveryRequestFactory(temperature_profile=refrigerated))

    quote_ambient = calculate_quote_breakdown(dr_ambient)
    quote_refrigerated = calculate_quote_breakdown(dr_refrigerated)

    assert (
        quote_refrigerated.cargo_equipment_surcharge - quote_ambient.cargo_equipment_surcharge
        == (_rule_amount(PricingRuleKey.REFRIGERATED_SURCHARGE))
    )


def test_after_hours_surcharge_applies_outside_standard_weekday_hours() -> None:
    # 2026-01-05 is a Monday; 14:00 UTC = 09:00 America/New_York (within hours).
    business_hours = _with_stops(DeliveryRequestFactory(pickup_window_start=DEFAULT_PICKUP_START))
    # 03:00 UTC on the same Monday = 22:00 America/New_York the prior day (after hours).
    late_night = _with_stops(
        DeliveryRequestFactory(
            pickup_window_start=datetime.datetime(2026, 1, 5, 3, 0, tzinfo=datetime.UTC)
        )
    )
    # 2026-01-10 is a Saturday.
    weekend = _with_stops(
        DeliveryRequestFactory(
            pickup_window_start=datetime.datetime(2026, 1, 10, 14, 0, tzinfo=datetime.UTC)
        )
    )

    assert is_after_hours(business_hours) is False
    assert is_after_hours(late_night) is True
    assert is_after_hours(weekend) is True

    business_quote = calculate_quote_breakdown(business_hours)
    late_night_quote = calculate_quote_breakdown(late_night)
    assert business_quote.after_hours_surcharge == Decimal("0.00")
    assert late_night_quote.after_hours_surcharge == _rule_amount(
        PricingRuleKey.AFTER_HOURS_SURCHARGE
    )


def test_inter_borough_toll_applies_only_when_boroughs_differ() -> None:
    same_borough = _with_stops(
        DeliveryRequestFactory(), pickup_borough="manhattan", destination_borough="manhattan"
    )
    cross_borough = _with_stops(
        DeliveryRequestFactory(), pickup_borough="manhattan", destination_borough="brooklyn"
    )

    same_quote = calculate_quote_breakdown(same_borough)
    cross_quote = calculate_quote_breakdown(cross_borough)

    assert same_quote.toll_estimate == Decimal("0.00")
    assert cross_quote.toll_estimate == _rule_amount(PricingRuleKey.INTER_BOROUGH_TOLL_ESTIMATE)


def test_wait_time_placeholder_fee_is_always_applied() -> None:
    delivery_request = _with_stops(DeliveryRequestFactory())
    quote = calculate_quote_breakdown(delivery_request)
    assert quote.wait_time_fee == _rule_amount(PricingRuleKey.WAIT_TIME_PLACEHOLDER_FEE)


def test_return_trip_fee_only_applied_when_requested() -> None:
    delivery_request = _with_stops(DeliveryRequestFactory())
    without_return = calculate_quote_breakdown(delivery_request, requires_return_trip=False)
    with_return = calculate_quote_breakdown(delivery_request, requires_return_trip=True)

    assert without_return.return_trip_fee == Decimal("0.00")
    assert with_return.return_trip_fee == _rule_amount(PricingRuleKey.RETURN_TRIP_FEE)
    assert with_return.total_price - without_return.total_price == _rule_amount(
        PricingRuleKey.RETURN_TRIP_FEE
    )


def test_missing_facility_coordinates_falls_back_to_flat_distance() -> None:
    from apps.deliveries.pricing import FALLBACK_DISTANCE_KM

    delivery_request = DeliveryRequestFactory()
    pickup_facility = FacilityFactory(latitude=None, longitude=None)
    destination_facility = FacilityFactory(latitude=None, longitude=None)
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
    quote = calculate_quote_breakdown(delivery_request)
    assert quote.distance_km == FALLBACK_DISTANCE_KM


def test_quote_delivery_request_persists_quote_and_sets_estimated_price() -> None:
    delivery_request = _with_stops(DeliveryRequestFactory())
    quote = quote_delivery_request(delivery_request)

    delivery_request.refresh_from_db()
    assert delivery_request.estimated_price == quote.total_price
    assert delivery_request.quote.total_price == quote.total_price


def test_quote_delivery_request_overwrites_existing_quote_on_recompute() -> None:
    delivery_request = _with_stops(DeliveryRequestFactory(service_level=ServiceLevel.SCHEDULED))
    first_quote = quote_delivery_request(delivery_request)

    delivery_request.service_level = ServiceLevel.STAT
    delivery_request.save(update_fields=["service_level"])
    second_quote = quote_delivery_request(delivery_request)

    assert second_quote.pk == first_quote.pk  # same row, updated in place
    assert second_quote.total_price > first_quote.total_price
