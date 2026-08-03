"""The explainable, weighted dispatch-candidate score
(docs/PRODUCT_REQUIREMENTS.md section 11 "Explainable score for eligible
couriers").

`score_candidate`/`rank_candidates` are the computation behind
`apps.dispatch.services.recommend_couriers` and are also called directly by
`assign_delivery`/`reassign_delivery` to determine whether a dispatcher's
chosen courier is the top-ranked eligible candidate (i.e. whether a
`DispatchOverride` reason is required).

## What's real vs. placeholder in each factor (honest accounting)

- **ETA to pickup** (weight 0.25) — real computation, synthetic input
  (service-zone-match tiers; see `apps.dispatch.sla` — no courier-location
  model exists yet).
- **SLA slack** (weight 0.25) — real computation over a synthetic ETA/
  transit estimate plus the real `required_delivery_by`.
- **Reliability / on-time history** (weight 0.10) — **placeholder, always a
  neutral 0.5.** No delivery has ever completed in this codebase (no
  transition into `DELIVERED` exists yet — that's Phase 5/6 work), so there
  is no real on-time history to compute from. A neutral constant is used
  rather than any fabricated "history" — see `_reliability_factor` below.
- **Route compatibility** (weight 0.10) — real computation (service-zone
  match against *both* the pickup and destination facility).
- **Active workload** (weight 0.15) — real computation, counting real
  `DeliveryAssignment` rows (Phase 3's "always 0" workload proxy is now
  real; see `apps.couriers.eligibility`).
- **Facility familiarity** (weight 0.10) — real computation, counting real
  past `DeliveryAssignment` rows to the pickup facility (honestly usually 0
  in a fresh demo, since no delivery has a completion path yet, but the
  query itself is real, not fabricated).
- **Toll/parking burden** (weight 0.05) — real computation reusing Phase
  2's `inter_borough_toll_estimate` `PricingRule` (a route property, not a
  courier property, so it does not differentiate between candidates on the
  same delivery — but it is genuinely computed and shown).
- **Customer preference, non-binding** (weight 0.00) — **explicitly
  deferred, not built.** Contributes zero weight; see
  `_customer_preference_factor` below and docs/CURRENT_STATUS.md "Phase 4"
  "Known gaps".

Weights sum to 1.00. `total_score` is on a 0-100 scale (`100 * sum(weight *
raw_score)`), rounded to 2 decimal places.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from apps.couriers.eligibility import (
    EligibilityFailureReason,
    check_courier_eligibility,
)
from apps.deliveries.models import PricingRuleKey
from apps.deliveries.pricing import get_pricing_rules
from apps.dispatch.sla import (
    compute_sla_estimate,
    estimate_eta_to_pickup_minutes,
)

if TYPE_CHECKING:
    import datetime

    from apps.couriers.models import CourierProfile
    from apps.deliveries.models import DeliveryRequest

ZERO = Decimal("0")
ONE = Decimal("1")
TWO_PLACES = Decimal("0.01")


def _clamp01(value: Decimal) -> Decimal:
    return max(ZERO, min(ONE, value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ScoreFactor:
    """One weighted factor's contribution to a candidate's total score, with a
    human-readable explanation — never just an opaque number."""

    name: str
    weight: Decimal
    raw_score: Decimal  # 0..1
    weighted_score: Decimal  # raw_score * weight, on the same 0..1 scale
    reason: str


@dataclass(frozen=True)
class DispatchCandidate:
    """One courier's full, explainable candidacy for a delivery request."""

    courier: CourierProfile
    eligible: bool
    hard_failure_reasons: tuple[EligibilityFailureReason, ...]
    total_score: Decimal | None  # None only when ineligible
    factors: tuple[ScoreFactor, ...]
    eta_to_pickup_minutes: Decimal
    estimated_delivery_at: datetime.datetime
    sla_slack_minutes: Decimal
    sla_feasibility: str
    toll_estimate: Decimal

    @property
    def reasons(self) -> tuple[str, ...]:
        """Every human-readable reason (hard-failure messages first, then each
        scoring factor's explanation) — the single field a UI/template needs
        to show "why" for this candidate."""
        hard = tuple(r.message for r in self.hard_failure_reasons)
        soft = tuple(f.reason for f in self.factors)
        return hard + soft


# Factor weights (docs/PRODUCT_REQUIREMENTS.md section 11's suggested factor
# list) — see module docstring table for what's real vs. placeholder.
WEIGHT_ETA_TO_PICKUP = Decimal("0.25")
WEIGHT_SLA_SLACK = Decimal("0.25")
WEIGHT_RELIABILITY = Decimal("0.10")
WEIGHT_ROUTE_COMPATIBILITY = Decimal("0.10")
WEIGHT_ACTIVE_WORKLOAD = Decimal("0.15")
WEIGHT_FACILITY_FAMILIARITY = Decimal("0.10")
WEIGHT_TOLL_BURDEN = Decimal("0.05")
WEIGHT_CUSTOMER_PREFERENCE = Decimal("0.00")


def _eta_factor(eta_minutes: Decimal) -> ScoreFactor:
    raw = _clamp01(ONE - (eta_minutes / Decimal("60")))
    return ScoreFactor(
        name="eta_to_pickup",
        weight=WEIGHT_ETA_TO_PICKUP,
        raw_score=raw,
        weighted_score=raw * WEIGHT_ETA_TO_PICKUP,
        reason=f"Estimated {eta_minutes} min to reach the pickup facility (synthetic, "
        "service-zone-based estimate — no real courier-location data exists yet).",
    )


def _sla_slack_factor(
    slack_minutes: Decimal, min_slack_minutes: int, feasibility: str
) -> ScoreFactor:
    baseline = Decimal(min_slack_minutes)
    if slack_minutes <= 0:
        raw = ZERO
    elif slack_minutes >= baseline * 2:
        raw = ONE
    else:
        raw = _clamp01(slack_minutes / (baseline * 2))
    return ScoreFactor(
        name="sla_slack",
        weight=WEIGHT_SLA_SLACK,
        raw_score=raw,
        weighted_score=raw * WEIGHT_SLA_SLACK,
        reason=f"SLA slack ~{slack_minutes} min against a {min_slack_minutes}-min minimum "
        f"buffer (feasibility: {feasibility}).",
    )


def _reliability_factor() -> ScoreFactor:
    # Honest placeholder — see module docstring table. No DeliveryRequest has
    # ever reached DELIVERED in this codebase (Phase 4 does not implement
    # transitions past ASSIGNED), so there is no real on-time-rate data to
    # compute, and none is fabricated. TODO(Phase 5/6): once completed
    # DeliveryAssignment/DeliveryRequest outcomes exist, replace this neutral
    # constant with a real on-time-rate query.
    raw = Decimal("0.5")
    return ScoreFactor(
        name="reliability",
        weight=WEIGHT_RELIABILITY,
        raw_score=raw,
        weighted_score=raw * WEIGHT_RELIABILITY,
        reason="Neutral default reliability score — no completed-delivery history exists "
        "yet in this prototype (see docs/CURRENT_STATUS.md Phase 4 'Known gaps').",
    )


def _route_compatibility_factor(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> ScoreFactor:
    from apps.dispatch.sla import courier_zone_id as get_courier_zone_id

    zone_id = get_courier_zone_id(courier)
    pickup_stop = delivery_request.pickup_stop
    destination_stop = delivery_request.destination_stop
    pickup_zone_id = pickup_stop.facility.service_zone_id if pickup_stop is not None else None
    destination_zone_id = (
        destination_stop.facility.service_zone_id if destination_stop is not None else None
    )

    if zone_id is not None and zone_id == pickup_zone_id == destination_zone_id:
        raw = ONE
        reason = "Courier's zone matches both the pickup and destination facility zones."
    elif zone_id is not None and zone_id == pickup_zone_id:
        raw = Decimal("0.7")
        reason = "Courier's zone matches the pickup facility zone (delivery crosses zones)."
    else:
        raw = Decimal("0.4")
        reason = "Limited or no service-zone overlap data between courier and this route."
    return ScoreFactor(
        name="route_compatibility",
        weight=WEIGHT_ROUTE_COMPATIBILITY,
        raw_score=raw,
        weighted_score=raw * WEIGHT_ROUTE_COMPATIBILITY,
        reason=reason,
    )


def _active_workload_factor(courier: CourierProfile) -> ScoreFactor:
    from apps.dispatch.models import ACTIVE_ASSIGNMENT_STATUSES, DeliveryAssignment

    current_workload = DeliveryAssignment.objects.filter(
        courier=courier, status__in=ACTIVE_ASSIGNMENT_STATUSES
    ).count()
    availability = getattr(courier, "availability", None)
    max_concurrent = availability.max_concurrent_deliveries if availability is not None else 1
    utilization = Decimal(current_workload) / Decimal(max(1, max_concurrent))
    raw = _clamp01(ONE - utilization)
    return ScoreFactor(
        name="active_workload",
        weight=WEIGHT_ACTIVE_WORKLOAD,
        raw_score=raw,
        weighted_score=raw * WEIGHT_ACTIVE_WORKLOAD,
        reason=f"{current_workload} active assignment(s) of {max_concurrent} configured capacity.",
    )


def _facility_familiarity_factor(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> ScoreFactor:
    from apps.dispatch.models import DeliveryAssignment

    pickup_stop = delivery_request.pickup_stop
    count = 0
    if pickup_stop is not None:
        count = (
            DeliveryAssignment.objects.filter(
                courier=courier, delivery_request__stops__facility_id=pickup_stop.facility_id
            )
            .distinct()
            .count()
        )
    raw = _clamp01(Decimal(count) / Decimal("5"))
    return ScoreFactor(
        name="facility_familiarity",
        weight=WEIGHT_FACILITY_FAMILIARITY,
        raw_score=raw,
        weighted_score=raw * WEIGHT_FACILITY_FAMILIARITY,
        reason=f"{count} prior assignment(s) to this pickup facility.",
    )


def _toll_burden_factor(delivery_request: DeliveryRequest) -> tuple[ScoreFactor, Decimal]:
    rules = get_pricing_rules()
    toll_estimate = ZERO
    pickup_stop = delivery_request.pickup_stop
    destination_stop = delivery_request.destination_stop
    if (
        pickup_stop is not None
        and destination_stop is not None
        and pickup_stop.facility.borough != destination_stop.facility.borough
    ):
        toll_estimate = rules.get(PricingRuleKey.INTER_BOROUGH_TOLL_ESTIMATE, ZERO)
    raw = ONE if toll_estimate == 0 else _clamp01(ONE - (toll_estimate / Decimal("50")))
    factor = ScoreFactor(
        name="toll_burden",
        weight=WEIGHT_TOLL_BURDEN,
        raw_score=raw,
        weighted_score=raw * WEIGHT_TOLL_BURDEN,
        reason=f"Estimated toll/inter-borough burden: ${_money(toll_estimate)} (a route property, "
        "the same for every candidate on this delivery).",
    )
    return factor, _money(toll_estimate)


def _customer_preference_factor() -> ScoreFactor:
    # Explicitly deferred — see module docstring table and
    # docs/CURRENT_STATUS.md Phase 4 "Known gaps". Zero weight: present in
    # the breakdown for transparency, but never affects ranking.
    raw = Decimal("0.5")
    return ScoreFactor(
        name="customer_preference",
        weight=WEIGHT_CUSTOMER_PREFERENCE,
        raw_score=raw,
        weighted_score=raw * WEIGHT_CUSTOMER_PREFERENCE,
        reason="Customer preference is out of scope for Phase 4 (documented as deferred) — "
        "zero weight, does not affect ranking.",
    )


def score_candidate(
    courier: CourierProfile,
    delivery_request: DeliveryRequest,
    *,
    reference_instant: datetime.datetime | None = None,
) -> DispatchCandidate:
    """Score one courier against one delivery request. Always runs the Phase 3
    hard-eligibility engine first; an ineligible courier still gets a full,
    explainable score breakdown (useful for a dispatcher asking "why wasn't
    this courier recommended"), but `total_score` is `None` for it so it can
    never accidentally outrank an eligible candidate."""
    eligibility = check_courier_eligibility(courier, delivery_request)
    sla_estimate = compute_sla_estimate(
        courier, delivery_request, reference_instant=reference_instant
    )
    eta = estimate_eta_to_pickup_minutes(courier, delivery_request)

    toll_factor, toll_estimate = _toll_burden_factor(delivery_request)
    factors = (
        _eta_factor(eta),
        _sla_slack_factor(
            sla_estimate.sla_slack_minutes, sla_estimate.min_slack_minutes, sla_estimate.feasibility
        ),
        _reliability_factor(),
        _route_compatibility_factor(courier, delivery_request),
        _active_workload_factor(courier),
        _facility_familiarity_factor(courier, delivery_request),
        toll_factor,
        _customer_preference_factor(),
    )

    total_score = None
    if eligibility.eligible:
        total_score = _money(sum((f.weighted_score for f in factors), ZERO) * Decimal("100"))

    return DispatchCandidate(
        courier=courier,
        eligible=eligibility.eligible,
        hard_failure_reasons=eligibility.hard_failure_reasons,
        total_score=total_score,
        factors=factors,
        eta_to_pickup_minutes=eta,
        estimated_delivery_at=sla_estimate.estimated_delivery_at,
        sla_slack_minutes=sla_estimate.sla_slack_minutes,
        sla_feasibility=sla_estimate.feasibility,
        toll_estimate=toll_estimate,
    )


def rank_candidates(
    delivery_request: DeliveryRequest,
    *,
    reference_instant: datetime.datetime | None = None,
) -> list[DispatchCandidate]:
    """Score every `CourierProfile` against `delivery_request` and return them
    ranked: eligible candidates first (highest `total_score` first), then
    ineligible candidates (for transparency/explainability — see
    `score_candidate`).

    Like `apps.couriers.eligibility.eligible_couriers_for`, this is a plain
    Python-level O(n) scan over all couriers, not an optimized database
    query — the same documented, deliberate scope limit Phase 3 accepted at
    this prototype's demo data volumes.
    """
    from apps.couriers.models import CourierProfile

    candidates = CourierProfile.objects.select_related(
        "user", "availability", "home_service_zone"
    ).all()
    scored = [
        score_candidate(courier, delivery_request, reference_instant=reference_instant)
        for courier in candidates
    ]
    return sorted(
        scored,
        key=lambda c: (
            0 if c.eligible else 1,
            -(c.total_score or ZERO),
            c.courier.pk,
        ),
    )
