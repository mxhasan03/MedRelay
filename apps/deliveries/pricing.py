"""Synthetic quote engine (docs/PRODUCT_REQUIREMENTS.md section 14).

Design decisions (see docs/CURRENT_STATUS.md "Phase 2" section for the full
write-up):

- **Pricing rules as a real model (`PricingRule`), not hard-coded
  constants.** Every dollar amount used below is read from an active
  `PricingRule` row (seeded via a data migration, editable from the admin),
  so tuning the demo's synthetic pricing never requires a code change —
  only a data edit. All amounts are synthetic/configurable, per
  docs/PRODUCT_REQUIREMENTS.md section 14 ("synthetic configurable rules
  only... Do not connect a real payment processor.").
- **Distance is a synthetic straight-line (haversine) estimate between
  facility coordinates**, using the plain `DecimalField` latitude/longitude
  already established in Phase 1 (`apps.facilities.models.Facility`) — not
  a real routing call. Real turn-by-turn/road-network distance via
  self-hosted OSRM is explicitly deferred to a later phase per
  docs/TECH_STACK_AND_ZERO_COST_POLICY.md ("OSRM self-hosted/local for
  routing in the demo") — this function does not call any external service
  and never will pretend to. If either facility is missing coordinates, a
  flat fallback distance is used instead (documented below) so the quote
  engine still produces a deterministic result rather than erroring.
- **After-hours** is computed against docs/PRODUCT_REQUIREMENTS.md section 2
  ("Weekdays: 7:00 AM-8:00 PM") using the pickup window's *start* time
  converted to `America/New_York` (the display timezone every datetime in
  this codebase is stored in UTC and shown in, per CLAUDE.md) — not the
  wall-clock time the quote happens to be computed at, so the same delivery
  request always produces the same quote regardless of when
  `quote_delivery_request` is called (a hard requirement per the Phase 2
  acceptance criteria: "same inputs always produce the same quote").
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apps.deliveries.models import PricingRule, PricingRuleKey, Quote, ServiceLevel

if TYPE_CHECKING:
    from apps.deliveries.models import DeliveryRequest

NY_TZ = ZoneInfo("America/New_York")

# Used only when a pickup or destination facility is missing lat/lng
# coordinates (both are optional fields per Phase 1 — see
# apps/facilities/models.py). A flat placeholder, not a real distance API
# fallback.
FALLBACK_DISTANCE_KM = Decimal("5.0")

TWO_PLACES = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def get_pricing_rules() -> dict[str, Decimal]:
    """All active `PricingRule` amounts, keyed by `PricingRuleKey` value."""
    return {row.key: row.amount for row in PricingRule.objects.filter(is_active=True)}


def _haversine_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> Decimal:
    """Great-circle distance between two lat/lng points, in kilometers.

    A straight-line estimate, not a road-network distance — see module
    docstring.
    """
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return Decimal(str(round(earth_radius_km * c, 3)))


def estimate_distance_km(delivery_request: DeliveryRequest) -> Decimal:
    pickup = delivery_request.pickup_stop
    destination = delivery_request.destination_stop
    if pickup is None or destination is None:
        return FALLBACK_DISTANCE_KM

    pickup_facility = pickup.facility
    destination_facility = destination.facility
    if (
        pickup_facility.latitude is None
        or pickup_facility.longitude is None
        or destination_facility.latitude is None
        or destination_facility.longitude is None
    ):
        return FALLBACK_DISTANCE_KM

    return _haversine_km(
        pickup_facility.latitude,
        pickup_facility.longitude,
        destination_facility.latitude,
        destination_facility.longitude,
    )


def is_after_hours(delivery_request: DeliveryRequest) -> bool:
    """Whether the pickup window start falls outside docs/PRODUCT_REQUIREMENTS.md
    section 2's standard weekday operating hours (7:00 AM-8:00 PM)."""
    local_start = delivery_request.pickup_window_start.astimezone(NY_TZ)
    if local_start.weekday() >= 5:  # Saturday=5, Sunday=6
        return True
    return not (7 <= local_start.hour < 20)


@dataclass(frozen=True)
class QuoteBreakdown:
    base_fee: Decimal
    distance_km: Decimal
    distance_time_fee: Decimal
    service_level_surcharge: Decimal
    cargo_equipment_surcharge: Decimal
    toll_estimate: Decimal
    wait_time_fee: Decimal
    after_hours_surcharge: Decimal
    return_trip_fee: Decimal
    total_price: Decimal


def calculate_quote_breakdown(
    delivery_request: DeliveryRequest, *, requires_return_trip: bool = False
) -> QuoteBreakdown:
    """Pure computation of every quote component for `delivery_request`.

    Deterministic: calling this twice with the same delivery request state
    (and the same active `PricingRule` rows) always returns identical
    numbers — no wall-clock "now", no randomness.
    """
    rules = get_pricing_rules()

    def rule(key: str) -> Decimal:
        return rules.get(key, Decimal("0"))

    base_fee = rule(PricingRuleKey.BASE_FEE)

    distance_km = estimate_distance_km(delivery_request)
    per_km_rate = rule(PricingRuleKey.PER_KM_RATE)
    per_minute_rate = rule(PricingRuleKey.PER_MINUTE_RATE)
    average_speed_kmh = rule(PricingRuleKey.AVERAGE_SPEED_KMH) or Decimal("30")
    time_minutes = (distance_km / average_speed_kmh) * Decimal("60")
    distance_time_fee = (distance_km * per_km_rate) + (time_minutes * per_minute_rate)

    if delivery_request.service_level == ServiceLevel.SAME_DAY:
        service_level_surcharge = rule(PricingRuleKey.SAME_DAY_SURCHARGE)
    elif delivery_request.service_level == ServiceLevel.STAT:
        service_level_surcharge = rule(PricingRuleKey.STAT_SURCHARGE)
    else:
        service_level_surcharge = Decimal("0")

    cargo_equipment_surcharge = Decimal("0")
    if delivery_request.cargo_class is not None:
        from apps.cargo.models import CargoClassCode

        if delivery_request.cargo_class.code == CargoClassCode.CLASS_2:
            cargo_equipment_surcharge += rule(PricingRuleKey.CARGO_CLASS_2_SURCHARGE)
        elif delivery_request.cargo_class.code == CargoClassCode.CLASS_3:
            cargo_equipment_surcharge += rule(PricingRuleKey.CARGO_CLASS_3_SURCHARGE)
    if delivery_request.temperature_profile is not None:
        from apps.cargo.models import TemperatureProfileCode

        if delivery_request.temperature_profile.code == TemperatureProfileCode.REFRIGERATED:
            cargo_equipment_surcharge += rule(PricingRuleKey.REFRIGERATED_SURCHARGE)

    toll_estimate = Decimal("0")
    pickup = delivery_request.pickup_stop
    destination = delivery_request.destination_stop
    if (
        pickup is not None
        and destination is not None
        and pickup.facility.borough != destination.facility.borough
    ):
        toll_estimate = rule(PricingRuleKey.INTER_BOROUGH_TOLL_ESTIMATE)

    wait_time_fee = rule(PricingRuleKey.WAIT_TIME_PLACEHOLDER_FEE)

    after_hours_surcharge = (
        rule(PricingRuleKey.AFTER_HOURS_SURCHARGE)
        if is_after_hours(delivery_request)
        else Decimal("0")
    )

    return_trip_fee = rule(PricingRuleKey.RETURN_TRIP_FEE) if requires_return_trip else Decimal("0")

    total_price = (
        base_fee
        + distance_time_fee
        + service_level_surcharge
        + cargo_equipment_surcharge
        + toll_estimate
        + wait_time_fee
        + after_hours_surcharge
        + return_trip_fee
    )

    return QuoteBreakdown(
        base_fee=_money(base_fee),
        distance_km=distance_km,
        distance_time_fee=_money(distance_time_fee),
        service_level_surcharge=_money(service_level_surcharge),
        cargo_equipment_surcharge=_money(cargo_equipment_surcharge),
        toll_estimate=_money(toll_estimate),
        wait_time_fee=_money(wait_time_fee),
        after_hours_surcharge=_money(after_hours_surcharge),
        return_trip_fee=_money(return_trip_fee),
        total_price=_money(total_price),
    )


def quote_delivery_request(
    delivery_request: DeliveryRequest, *, requires_return_trip: bool = False
) -> Quote:
    """Compute a quote breakdown and persist/update the request's `Quote` row and
    `estimated_price`."""
    breakdown = calculate_quote_breakdown(
        delivery_request, requires_return_trip=requires_return_trip
    )

    quote, _ = Quote.objects.update_or_create(
        delivery_request=delivery_request,
        defaults={
            "base_fee": breakdown.base_fee,
            "distance_km": breakdown.distance_km,
            "distance_time_fee": breakdown.distance_time_fee,
            "service_level_surcharge": breakdown.service_level_surcharge,
            "cargo_equipment_surcharge": breakdown.cargo_equipment_surcharge,
            "toll_estimate": breakdown.toll_estimate,
            "wait_time_fee": breakdown.wait_time_fee,
            "after_hours_surcharge": breakdown.after_hours_surcharge,
            "return_trip_fee": breakdown.return_trip_fee,
            "total_price": breakdown.total_price,
        },
    )
    delivery_request.estimated_price = breakdown.total_price
    delivery_request.save(update_fields=["estimated_price", "updated_at"])
    return quote
