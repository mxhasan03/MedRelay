"""Synthetic ETA-to-pickup, transit-time, and SLA-feasibility estimation.

This module is what finally lets `apps.couriers.eligibility.EligibilityResult
.sla_feasibility` carry a real (if synthetic) value instead of Phase 3's
`"not_evaluated"` placeholder, and is what `apps.dispatch.scoring` uses to
score the "ETA to pickup" and "SLA slack" factors.

Two honest, documented placeholders:

1. **ETA-to-pickup is a small set of synthetic tiers, not a distance
   calculation.** There is still no real courier-location model in this
   codebase — `CourierLocationPing` remains Phase 5 work (see Phase 3's
   "Known gaps" in docs/CURRENT_STATUS.md) — so there is no real "from"
   point to measure a courier's distance to the pickup facility from at all.
   The only real signal available is service-zone match (the same signal
   `apps.couriers.eligibility._check_service_zone` already uses for its hard
   filter): same zone as the pickup facility, different zone, or unknown.
   Each tier maps to a fixed synthetic minute value below. This is
   explicitly weaker than the haversine distance estimate used for
   *transit* time (which has two real facility coordinates to work with) —
   it is a real, if crude, computation, not a fabricated one.
2. **Transit time reuses Phase 2's exact haversine + average-speed
   approach** (`apps.deliveries.pricing.estimate_distance_km` and the
   `average_speed_kmh` `PricingRule`), not a real OSRM/routing call — see
   that module's docstring for the full rationale (zero-cost policy, no
   external service call now or planned).

**Anchor instant, and why it is not wall-clock "now"**: `compute_sla_estimate`
anchors its ETA/transit-time math on `delivery_request.pickup_window_start`
by default (a caller may override via `reference_instant`), the same
determinism-over-realism choice Phase 2's quote engine made for its
after-hours surcharge (computed against the pickup window, not the moment
the quote happens to be computed) — see
`apps.deliveries.pricing`'s module docstring. This keeps `compute_sla_estimate`
(and therefore `EligibilityResult.sla_feasibility` and every dispatch score)
deterministic for a fixed delivery request/courier pair regardless of when
the calculation runs, which matters for reproducible tests and demo
recordings alike.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from apps.deliveries.models import PricingRuleKey
from apps.deliveries.pricing import estimate_distance_km, get_pricing_rules

if TYPE_CHECKING:
    from apps.couriers.models import CourierProfile
    from apps.deliveries.models import DeliveryRequest

FEASIBLE = "feasible"
AT_RISK = "at_risk"
INFEASIBLE = "infeasible"
# Re-exported so callers only need to import from this module; matches
# apps.couriers.eligibility.SLA_FEASIBILITY_NOT_EVALUATED exactly (kept as a
# defensive fallback only — see compute_sla_estimate below).
NOT_EVALUATED = "not_evaluated"

# Synthetic ETA-to-pickup tiers (minutes) — see module docstring point 1.
ETA_SAME_ZONE_MINUTES = Decimal("15")
ETA_UNKNOWN_ZONE_MINUTES = Decimal("25")
ETA_DIFFERENT_ZONE_MINUTES = Decimal("40")

# Fallback average speed if no active `average_speed_kmh` PricingRule exists
# (mirrors apps.deliveries.pricing's own fallback default).
DEFAULT_AVERAGE_SPEED_KMH = Decimal("30")

# Fallback "at risk" threshold used only if no SLAProfile row matches the
# delivery's service level (should not happen once the seed data migration
# has run, but this keeps the function total/non-crashing regardless).
FALLBACK_MIN_SLACK_MINUTES = 30


@dataclass(frozen=True)
class SLAEstimate:
    eta_to_pickup_minutes: Decimal
    transit_minutes: Decimal
    total_minutes: Decimal
    reference_instant: datetime.datetime
    estimated_delivery_at: datetime.datetime
    sla_slack_minutes: Decimal
    min_slack_minutes: int
    feasibility: str


def courier_zone_id(courier: CourierProfile) -> int | None:
    """The courier's current (falling back to home) service zone ID, or `None`
    if neither is set. Shared with `apps.dispatch.scoring`'s "route
    compatibility" factor so both modules agree on the exact same "which
    zone is this courier in right now" logic."""
    availability = getattr(courier, "availability", None)
    if availability is not None and availability.current_service_zone_id is not None:
        return availability.current_service_zone_id  # type: ignore[no-any-return]
    if courier.home_service_zone_id is not None:
        return courier.home_service_zone_id  # type: ignore[no-any-return]
    return None


def estimate_eta_to_pickup_minutes(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> Decimal:
    """Synthetic ETA-to-pickup, in minutes — see module docstring point 1."""
    zone_id = courier_zone_id(courier)
    pickup_stop = delivery_request.pickup_stop
    facility_zone_id = pickup_stop.facility.service_zone_id if pickup_stop is not None else None
    if zone_id is None or facility_zone_id is None:
        return ETA_UNKNOWN_ZONE_MINUTES
    if zone_id == facility_zone_id:
        return ETA_SAME_ZONE_MINUTES
    return ETA_DIFFERENT_ZONE_MINUTES


def estimate_transit_minutes(delivery_request: DeliveryRequest) -> Decimal:
    """Synthetic pickup-to-destination transit time, in minutes — see module
    docstring point 2. Reuses Phase 2's haversine estimate and average-speed
    pricing rule exactly."""
    distance_km = estimate_distance_km(delivery_request)
    rules = get_pricing_rules()
    average_speed = rules.get(PricingRuleKey.AVERAGE_SPEED_KMH) or DEFAULT_AVERAGE_SPEED_KMH
    if average_speed <= 0:
        average_speed = DEFAULT_AVERAGE_SPEED_KMH
    return (distance_km / average_speed) * Decimal("60")


def get_sla_profile(service_level: str) -> Any:
    # Lazy import: apps.dispatch.models -> nothing cyclical here (this module
    # is itself in apps.dispatch), kept lazy only for import-order tidiness
    # with the rest of this module's TYPE_CHECKING-only model imports.
    from apps.dispatch.models import SLAProfile

    return SLAProfile.objects.filter(service_level=service_level).first()


def compute_sla_estimate(
    courier: CourierProfile,
    delivery_request: DeliveryRequest,
    *,
    reference_instant: datetime.datetime | None = None,
) -> SLAEstimate:
    """The real (if synthetic) SLA-feasibility calculation.

    `reference_instant` defaults to `delivery_request.pickup_window_start`
    (see module docstring for why this beats wall-clock "now" here).
    """
    reference = reference_instant or delivery_request.pickup_window_start
    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)
    transit = estimate_transit_minutes(delivery_request)
    total = eta + transit
    estimated_delivery_at = reference + datetime.timedelta(minutes=float(total))
    slack_minutes = Decimal(
        (delivery_request.required_delivery_by - estimated_delivery_at).total_seconds()
    ) / Decimal("60")

    profile = get_sla_profile(delivery_request.service_level)
    min_slack = profile.min_slack_minutes if profile is not None else FALLBACK_MIN_SLACK_MINUTES

    if slack_minutes < 0:
        feasibility = INFEASIBLE
    elif slack_minutes < min_slack:
        feasibility = AT_RISK
    else:
        feasibility = FEASIBLE

    return SLAEstimate(
        eta_to_pickup_minutes=eta,
        transit_minutes=transit,
        total_minutes=total,
        reference_instant=reference,
        estimated_delivery_at=estimated_delivery_at,
        sla_slack_minutes=slack_minutes,
        min_slack_minutes=min_slack,
        feasibility=feasibility,
    )
