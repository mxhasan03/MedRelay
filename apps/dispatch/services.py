"""The dispatch service API (docs/ARCHITECTURE_AND_DATA_MODEL.md section 6
"Dispatch service"): `recommend_couriers`, `assign_delivery`, `offer_delivery`,
`reassign_delivery`.

## Concurrency-safety design (docs/ARCHITECTURE_AND_DATA_MODEL.md section 9)

`assign_delivery` is the load-bearing function for the "one delivery assigned
atomically" acceptance criterion:

1. It runs inside `transaction.atomic()` and takes
   `DeliveryRequest.objects.select_for_update()` on the target row before
   checking/changing anything — a real row lock on PostgreSQL. **On SQLite
   (this project's test/CI database), `select_for_update()` is a documented
   no-op** — Django's own SQL compiler silently omits the `FOR UPDATE`
   clause whenever `DatabaseFeatures.has_select_for_update` is `False`
   (`django/db/models/sql/compiler.py`; SQLite's backend does not set this
   feature), rather than raising. This was confirmed by reading Django's own
   compiler source in this environment, not assumed.
2. Because of (1), the *actual*, backend-independent correctness guarantee
   this module relies on is the partial database `UniqueConstraint` on
   `DeliveryAssignment` (`unique_active_assignment_per_delivery_request` in
   `apps.dispatch.models`) — a real `IntegrityError` when two concurrent
   transactions both try to create an `ACTIVE` assignment row for the same
   delivery request, regardless of whether a row lock was actually taken.
3. **A second, real discovery made by actually running this project's own
   multi-threaded concurrency test against SQLite** (not merely assumed):
   SQLite's own whole-database write-transaction serialization is coarse
   enough that a concurrent writer sometimes cannot even acquire the write
   lock at all within its default timeout, which surfaces as
   `django.db.OperationalError` ("database is locked"), not `IntegrityError`
   — a different exception type than the unique-constraint violation, but
   the exact same *kind* of event (a genuine write conflict, not a bug).
   `assign_delivery`/`reassign_delivery` therefore catch **both**
   `IntegrityError` and `OperationalError` (inside their own nested
   savepoint, so the outer transaction isn't poisoned — see the Django
   gotcha this avoids in the code below) and re-raise either as a clean
   `apps.dispatch.exceptions.AssignmentConflictError`.
4. See `apps.dispatch.tests.test_concurrency` and docs/CURRENT_STATUS.md
   "Phase 4" section for the actual multi-threaded test built against this,
   and an honest statement of what it does and does not prove about
   PostgreSQL row-locking specifically.

## Hard-gate-cannot-be-overridden design

Every function below that assigns/offers a delivery to a specific courier
(`assign_delivery`, `offer_delivery`, `reassign_delivery`) calls
`apps.couriers.eligibility.check_courier_eligibility` **unconditionally**,
before writing anything, and raises `IneligibleCourierError` if it fails —
there is no `reason`/override parameter anywhere in this module that
suppresses that check. `DispatchOverride` rows are only ever written *after*
eligibility has already passed, to record a dispatcher's justification for a
*soft* (scoring/ranking) choice. See docs/CURRENT_STATUS.md "Phase 4" section
for the dedicated test proving this holds at every one of these entry
points.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.couriers.eligibility import check_courier_eligibility
from apps.couriers.models import CourierProfile
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.deliveries.pricing import estimate_distance_km
from apps.deliveries.state_machine import transition_delivery_request
from apps.dispatch.exceptions import (
    AssignmentConflictError,
    IneligibleCourierError,
    JobOfferOwnershipError,
)
from apps.dispatch.models import (
    ACTIVE_ASSIGNMENT_STATUSES,
    AssignmentStatus,
    DeliveryAssignment,
    DispatchOverride,
    DispatchOverrideType,
    DispatchRecommendation,
    DispatchRecommendationCandidate,
    JobOffer,
    JobOfferStatus,
    RouteLeg,
    RouteLegType,
    RoutePlan,
)
from apps.dispatch.scoring import DispatchCandidate, rank_candidates
from apps.dispatch.sla import estimate_eta_to_pickup_minutes, estimate_transit_minutes

if TYPE_CHECKING:
    from apps.accounts.models import User

# Delivery statuses `assign_delivery`/`offer_delivery` may act on. Matches
# the extension to apps.deliveries.state_machine.ALLOWED_TRANSITIONS made in
# this phase (READY_FOR_DISPATCH -> OFFERED, READY_FOR_DISPATCH/OFFERED ->
# ASSIGNED).
ASSIGNABLE_STATUSES = frozenset({DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.OFFERED})
OFFERABLE_STATUSES = frozenset({DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.OFFERED})


def recommend_couriers(
    delivery_id: Any,
    *,
    actor: User | None = None,
    persist: bool = True,
    reference_instant: datetime.datetime | None = None,
) -> list[DispatchCandidate]:
    """Rank every courier candidate for `delivery_id` (see
    `apps.dispatch.scoring.rank_candidates`). Persists a `DispatchRecommendation`
    audit row (plus one `DispatchRecommendationCandidate` per candidate) by
    default — see `apps.dispatch.models.DispatchRecommendation`'s docstring
    for the design decision. Pass `persist=False` for a cheap what-if
    computation that leaves no audit trail (e.g. repeated internal calls from
    within `assign_delivery`/`reassign_delivery` to determine the current
    top-ranked candidate)."""
    delivery_request = DeliveryRequest.objects.get(pk=delivery_id)
    candidates = rank_candidates(delivery_request, reference_instant=reference_instant)
    if persist:
        _persist_recommendation(delivery_request, candidates, actor=actor)
    return candidates


def _persist_recommendation(
    delivery_request: DeliveryRequest, candidates: list[DispatchCandidate], *, actor: User | None
) -> DispatchRecommendation:
    with transaction.atomic():
        recommendation = DispatchRecommendation.objects.create(
            delivery_request=delivery_request,
            computed_by=actor,
            candidate_count=len(candidates),
            eligible_count=sum(1 for c in candidates if c.eligible),
        )
        DispatchRecommendationCandidate.objects.bulk_create(
            [
                DispatchRecommendationCandidate(
                    recommendation=recommendation,
                    courier=candidate.courier,
                    rank=rank,
                    eligible=candidate.eligible,
                    total_score=candidate.total_score,
                    factor_scores={f.name: float(f.raw_score) for f in candidate.factors},
                    reasons=list(candidate.reasons),
                    hard_failure_reasons=[
                        {"code": r.code, "message": r.message}
                        for r in candidate.hard_failure_reasons
                    ],
                    eta_to_pickup_minutes=candidate.eta_to_pickup_minutes,
                    sla_slack_minutes=candidate.sla_slack_minutes,
                    sla_feasibility=candidate.sla_feasibility,
                    toll_estimate=candidate.toll_estimate,
                )
                for rank, candidate in enumerate(candidates, start=1)
            ]
        )
    return recommendation


def _top_eligible_courier_id(delivery_request: DeliveryRequest) -> Any:
    ranked = rank_candidates(delivery_request)
    eligible = [c for c in ranked if c.eligible]
    return eligible[0].courier.pk if eligible else None


def _require_eligible(courier: CourierProfile, delivery_request: DeliveryRequest) -> None:
    result = check_courier_eligibility(courier, delivery_request)
    if not result.eligible:
        reasons = "; ".join(r.message for r in result.hard_failure_reasons)
        raise IneligibleCourierError(
            f"Courier {courier.pk} is not eligible for delivery {delivery_request.pk}: {reasons}"
        )


@transaction.atomic
def assign_delivery(
    delivery_id: Any, courier_id: Any, actor: User | None, reason: str | None = None
) -> DeliveryAssignment:
    """Assign `courier_id` to `delivery_id`. See module docstring for the
    concurrency-safety and hard-gate design this implements.

    `reason` is optional, but is *required* the moment the chosen courier is
    not the current top-ranked eligible candidate (a soft/scoring override,
    docs/PRODUCT_REQUIREMENTS.md section 11) — `ValueError` is raised if
    omitted in that case. It is never used to bypass eligibility.
    """
    delivery_request = DeliveryRequest.objects.select_for_update().get(pk=delivery_id)
    if delivery_request.status not in ASSIGNABLE_STATUSES:
        raise AssignmentConflictError(
            f"Delivery {delivery_id} is not in an assignable state "
            f"(current status: {delivery_request.status!r})."
        )
    if DeliveryAssignment.objects.filter(
        delivery_request=delivery_request, status__in=ACTIVE_ASSIGNMENT_STATUSES
    ).exists():
        raise AssignmentConflictError(f"Delivery {delivery_id} already has an active assignment.")

    courier = CourierProfile.objects.select_related("availability", "home_service_zone").get(
        pk=courier_id
    )
    # Hard gate — never skippable, see module docstring.
    _require_eligible(courier, delivery_request)

    top_choice_id = _top_eligible_courier_id(delivery_request)
    is_override = top_choice_id is not None and top_choice_id != courier.pk
    if is_override and not (reason and reason.strip()):
        raise ValueError(
            "A reason is required when assigning a courier other than the top-ranked "
            "recommendation (docs/PRODUCT_REQUIREMENTS.md section 11)."
        )

    try:
        with transaction.atomic():
            assignment = DeliveryAssignment.objects.create(
                delivery_request=delivery_request,
                courier=courier,
                status=AssignmentStatus.ACTIVE,
                assigned_by=actor,
            )
    except (IntegrityError, OperationalError) as exc:
        # The partial unique constraint fired (a concurrent transaction won
        # the race and already created/committed the ACTIVE assignment row:
        # IntegrityError), OR — a real, honest discovery from this project's
        # own concurrency test on SQLite — the concurrent writer simply could
        # not acquire SQLite's coarse whole-database write lock in time
        # (OperationalError: "database is locked"), which is SQLite's own,
        # much coarser-grained stand-in for what a real PostgreSQL row lock
        # would otherwise resolve more gracefully. Either way this is a clean
        # concurrency conflict, not a bug, and is caught inside its own
        # savepoint (the nested transaction.atomic() above) so the outer
        # transaction is not poisoned — see module docstring and
        # apps.dispatch.tests.test_concurrency for the full write-up.
        raise AssignmentConflictError(
            f"Delivery {delivery_id} was concurrently assigned to another courier."
        ) from exc

    transition_delivery_request(
        delivery_request, DeliveryStatus.ASSIGNED, actor=actor, reason=reason or ""
    )

    # Phase 6: append a COURIER_ASSIGNED custody event. Lazy import — see
    # apps.dispatch.models' module docstring for this codebase's convention
    # of a lazy in-function import at the specific call site that needs the
    # other app's behavior.
    from apps.custody.models import CustodyActorType, CustodyEventType
    from apps.custody.services import record_event

    record_event(
        delivery_request,
        CustodyEventType.COURIER_ASSIGNED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={"courier_id": courier.pk},
    )

    if reason and reason.strip():
        DispatchOverride.objects.create(
            delivery_request=delivery_request,
            actor=actor,
            override_type=(
                DispatchOverrideType.NOT_TOP_RANKED if is_override else DispatchOverrideType.NOTE
            ),
            reason=reason,
            chosen_courier=courier,
        )

    _build_route_plan(assignment)
    return assignment


@transaction.atomic
def offer_delivery(
    delivery_id: Any,
    candidate_ids: list[Any],
    expires_at: datetime.datetime,
    *,
    actor: User | None = None,
) -> list[JobOffer]:
    """Offer `delivery_id` to every courier in `candidate_ids` (broadcast-style
    — `accept_job_offer`/`decline_job_offer` below are the courier-facing
    accept/decline flow, Phase 5). Every candidate must independently pass
    the hard-eligibility gate; if any one of them fails it, the whole call is
    rejected atomically (no partial offers created) — see module docstring."""
    delivery_request = DeliveryRequest.objects.select_for_update().get(pk=delivery_id)
    if delivery_request.status not in OFFERABLE_STATUSES:
        raise AssignmentConflictError(
            f"Delivery {delivery_id} is not in an offerable state "
            f"(current status: {delivery_request.status!r})."
        )

    couriers = list(
        CourierProfile.objects.select_related("availability", "home_service_zone").filter(
            pk__in=candidate_ids
        )
    )
    found_ids = {c.pk for c in couriers}
    missing = set(candidate_ids) - found_ids
    if missing:
        raise ValueError(f"Unknown courier id(s): {sorted(missing)}")

    ineligible: list[str] = []
    for courier in couriers:
        result = check_courier_eligibility(courier, delivery_request)
        if not result.eligible:
            reasons = "; ".join(r.message for r in result.hard_failure_reasons)
            ineligible.append(f"courier {courier.pk}: {reasons}")
    if ineligible:
        raise IneligibleCourierError(
            f"Cannot offer delivery {delivery_id} — ineligible candidate(s): "
            + "; ".join(ineligible)
        )

    if delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH:
        transition_delivery_request(delivery_request, DeliveryStatus.OFFERED, actor=actor)

    return [
        JobOffer.objects.create(
            delivery_request=delivery_request,
            courier=courier,
            status=JobOfferStatus.OFFERED,
            expires_at=expires_at,
            created_by=actor,
        )
        for courier in couriers
    ]


def _reject_if_not_acceptable(offer: JobOffer) -> None:
    """Raise `AssignmentConflictError` if `offer` cannot be accepted right now:
    its status is not (still) `OFFERED`, or it has passed `expires_at`.

    Deliberately **read-only** — it does not flip `offer.status` to `EXPIRED`
    in the database. An earlier version of this function tried to "correct"
    an expired offer's stored status to `EXPIRED` before raising, but since
    `accept_job_offer`/`decline_job_offer` are themselves wrapped in
    `transaction.atomic()`, that write would be rolled back along with
    everything else the moment the exception this function raises propagates
    out of the atomic block — a real bug caught by this phase's own test
    (`test_accept_job_offer_rejects_expired_offer_and_marks_it_expired`
    failed until this was fixed). `JobOffer.is_expired` (a plain computed
    property) already reports the correct answer for display without needing
    a persisted status flip; a real background job to flip stale `OFFERED`
    rows to `EXPIRED` in the database remains Phase 7 territory, unchanged
    from Phase 4's own framing.
    """
    if offer.status != JobOfferStatus.OFFERED:
        raise AssignmentConflictError(
            f"Job offer {offer.pk} is not open (status: {offer.status!r})."
        )
    if offer.is_expired:
        raise AssignmentConflictError(
            f"Job offer {offer.pk} expired at {offer.expires_at} and can no longer be accepted."
        )


@transaction.atomic
def accept_job_offer(
    offer_id: Any, courier_id: Any, *, actor: User | None = None
) -> DeliveryAssignment:
    """A courier accepts one of their own open `JobOffer`s.

    Per docs/PRODUCT_REQUIREMENTS.md section 6 ("Job offers... Courier can
    accept or reject") and docs/CURRENT_STATUS.md's Phase 4 "Known gaps"
    (accept/decline was explicitly deferred to Phase 5): accepting an offer is
    fundamentally the same race as a dispatcher directly assigning a courier —
    two couriers could try to accept overlapping offers for the same delivery
    at once — so this is a **thin wrapper around `assign_delivery`**, not a
    reimplementation of its atomicity/hard-eligibility logic. `assign_delivery`
    still runs inside its own `transaction.atomic()`/`select_for_update()` and
    is still backed by the same partial `UniqueConstraint` on
    `DeliveryAssignment` — see that function's docstring for the full
    concurrency write-up.

    A synthetic, always-non-blank `reason` is passed through to
    `assign_delivery` on every call (not just when the courier happens not to
    be the top-ranked candidate) so the courier's own acceptance is never
    rejected by `assign_delivery`'s "a reason is required for a non-top-ranked
    override" `ValueError` — the `JobOffer` itself, not a dispatcher's
    judgment call, is what justifies this courier being the one assigned. This
    does mean every accepted offer leaves a `DispatchOverride` audit row
    (`NOT_TOP_RANKED` if this courier was not the top-ranked candidate,
    `NOTE` otherwise) — an intentional, honest side effect: "this assignment
    came from a courier's direct offer acceptance," not silence.

    Raises `JobOfferOwnershipError` if `offer_id` was not offered to
    `courier_id`; `AssignmentConflictError` if the offer is not (still) open,
    **including an offer whose `expires_at` has already passed** (see
    `_reject_if_not_acceptable` — this is the load-bearing check for "an
    expired offer cannot be accepted"); and
    `IneligibleCourierError`/`AssignmentConflictError` from the underlying
    `assign_delivery` call for the same reasons that function would normally
    raise them (courier no longer eligible, delivery concurrently assigned to
    someone else, etc.).
    """
    offer = JobOffer.objects.select_for_update().get(pk=offer_id)
    if str(offer.courier_id) != str(courier_id):
        raise JobOfferOwnershipError(f"Job offer {offer_id} was not made to courier {courier_id}.")
    _reject_if_not_acceptable(offer)

    assignment = assign_delivery(
        offer.delivery_request_id,
        courier_id,
        actor,
        reason=f"Courier accepted job offer {offer.pk} directly.",
    )

    offer.status = JobOfferStatus.ACCEPTED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])

    # Other still-open offers for the same delivery are now moot — the
    # partial unique constraint on DeliveryAssignment already guarantees only
    # one of them could ever be successfully accepted, but leaving them
    # dangling as OFFERED would be misleading in the courier's job-offer list
    # (docs/PRODUCT_REQUIREMENTS.md section 6: "Show only eligible jobs").
    JobOffer.objects.filter(
        delivery_request_id=offer.delivery_request_id, status=JobOfferStatus.OFFERED
    ).exclude(pk=offer.pk).update(status=JobOfferStatus.CANCELLED, responded_at=timezone.now())

    return assignment


@transaction.atomic
def decline_job_offer(offer_id: Any, courier_id: Any, *, reason: str = "") -> JobOffer:
    """A courier declines one of their own open `JobOffer`s, optionally recording
    a reason.

    Per docs/PRODUCT_REQUIREMENTS.md section 6 ("Legitimate cargo/safety
    rejection must be recordable") `reason` is always optional (a courier may
    decline without giving one) but is stored verbatim on `JobOffer.decline_reason`
    when given. Declining **must not** block the same delivery from being
    offered to/accepted by someone else — this function only ever mutates the
    one `JobOffer` row being declined; it never touches the `DeliveryRequest`
    status or any other courier's offer for the same delivery, so every other
    open offer (and a fresh `offer_delivery`/`assign_delivery` call) is
    completely unaffected.

    Raises `JobOfferOwnershipError` if `offer_id` was not offered to
    `courier_id`, or `AssignmentConflictError` if the offer is not (still)
    open (already accepted/declined/cancelled). Unlike `accept_job_offer`,
    declining an already-expired-but-still-`OFFERED` offer is allowed —
    the acceptance criterion this project must honor is specifically "an
    expired offer cannot be *accepted*"; declining one (recording that the
    courier is not taking it) is harmless and arguably useful bookkeeping
    even after `expires_at` has passed.
    """
    offer = JobOffer.objects.select_for_update().get(pk=offer_id)
    if str(offer.courier_id) != str(courier_id):
        raise JobOfferOwnershipError(f"Job offer {offer_id} was not made to courier {courier_id}.")
    if offer.status != JobOfferStatus.OFFERED:
        raise AssignmentConflictError(
            f"Job offer {offer_id} is not open to decline (status: {offer.status!r})."
        )

    offer.status = JobOfferStatus.DECLINED
    offer.responded_at = timezone.now()
    offer.decline_reason = reason or ""
    offer.save(update_fields=["status", "responded_at", "decline_reason"])
    return offer


@transaction.atomic
def reassign_delivery(
    delivery_id: Any, courier_id: Any, actor: User | None, reason: str
) -> DeliveryAssignment:
    """Reassign an already-`ASSIGNED` delivery to a different courier.

    `reason` is required (not optional) — every reassignment is itself a
    dispatcher override of an existing assignment decision and always gets a
    `DispatchOverride` row, per docs/PRODUCT_REQUIREMENTS.md section 11. The
    hard-eligibility gate still applies unconditionally to the new courier.
    """
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reassign a delivery.")

    delivery_request = DeliveryRequest.objects.select_for_update().get(pk=delivery_id)
    if delivery_request.status != DeliveryStatus.ASSIGNED:
        raise AssignmentConflictError(
            f"Delivery {delivery_id} has no active assignment to reassign "
            f"(current status: {delivery_request.status!r})."
        )
    current_assignment = (
        DeliveryAssignment.objects.select_for_update()
        .filter(delivery_request=delivery_request, status=AssignmentStatus.ACTIVE)
        .first()
    )
    if current_assignment is None:
        raise AssignmentConflictError(
            f"Delivery {delivery_id} has no active assignment row to reassign."
        )
    if current_assignment.courier_id == courier_id:
        raise ValueError("Cannot reassign a delivery to its currently assigned courier.")

    courier = CourierProfile.objects.select_related("availability", "home_service_zone").get(
        pk=courier_id
    )
    # Hard gate — never skippable, see module docstring.
    _require_eligible(courier, delivery_request)

    previous_courier = current_assignment.courier
    current_assignment.status = AssignmentStatus.REASSIGNED
    current_assignment.unassigned_at = timezone.now()
    current_assignment.save(update_fields=["status", "unassigned_at", "updated_at"])

    try:
        with transaction.atomic():
            new_assignment = DeliveryAssignment.objects.create(
                delivery_request=delivery_request,
                courier=courier,
                status=AssignmentStatus.ACTIVE,
                assigned_by=actor,
            )
    except (IntegrityError, OperationalError) as exc:
        # See assign_delivery's matching except clause above for the full
        # honest explanation of both possible underlying causes here.
        raise AssignmentConflictError(
            f"Delivery {delivery_id} was concurrently assigned to another courier."
        ) from exc

    DispatchOverride.objects.create(
        delivery_request=delivery_request,
        actor=actor,
        override_type=DispatchOverrideType.REASSIGNMENT,
        reason=reason,
        chosen_courier=courier,
        previous_courier=previous_courier,
    )

    _build_route_plan(new_assignment)
    return new_assignment


def _build_route_plan(assignment: DeliveryAssignment) -> RoutePlan:
    """Build (or rebuild) the synthetic `RoutePlan`/`RouteLeg` rows for the
    delivery `assignment` belongs to — see `apps.dispatch.models.RoutePlan`'s
    docstring for the "synthetic placeholder, one row per delivery" design.
    """
    delivery_request = assignment.delivery_request
    eta_to_pickup = estimate_eta_to_pickup_minutes(assignment.courier, delivery_request)
    transit_minutes = estimate_transit_minutes(delivery_request)
    distance_km = estimate_distance_km(delivery_request)

    route_plan, _ = RoutePlan.objects.update_or_create(
        delivery_request=delivery_request,
        defaults={
            "assignment": assignment,
            "total_distance_km": distance_km,
            "total_duration_minutes": eta_to_pickup + transit_minutes,
        },
    )
    route_plan.legs.all().delete()

    pickup_stop = delivery_request.pickup_stop
    destination_stop = delivery_request.destination_stop
    RouteLeg.objects.create(
        route_plan=route_plan,
        sequence=1,
        leg_type=RouteLegType.TO_PICKUP,
        from_facility=None,
        to_facility=pickup_stop.facility if pickup_stop is not None else None,
        distance_km=Decimal("0"),
        duration_minutes=eta_to_pickup,
    )
    RouteLeg.objects.create(
        route_plan=route_plan,
        sequence=2,
        leg_type=RouteLegType.PICKUP_TO_DESTINATION,
        from_facility=pickup_stop.facility if pickup_stop is not None else None,
        to_facility=destination_stop.facility if destination_stop is not None else None,
        distance_km=distance_km,
        duration_minutes=transit_minutes,
    )
    return route_plan


def at_risk_delivery_ids(*, as_of: datetime.datetime | None = None) -> set[Any]:
    """IDs of open (`READY_FOR_DISPATCH`/`OFFERED`) delivery requests whose
    single best-ranked eligible candidate's SLA feasibility is `"at_risk"` or
    `"infeasible"` — the simple SLA-risk rule the dashboard surfaces
    (docs/PRODUCT_REQUIREMENTS.md section 7 "at-risk deadlines"). Not a
    background job/notification (that's Phase 7) — purely a query used at
    render time.
    """
    from apps.dispatch.sla import AT_RISK, INFEASIBLE

    at_risk_ids: set[Any] = set()
    candidates_by_delivery = DeliveryRequest.objects.filter(
        status__in=(DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.OFFERED)
    )
    for delivery_request in candidates_by_delivery:
        ranked = rank_candidates(delivery_request)
        eligible = [c for c in ranked if c.eligible]
        best = eligible[0] if eligible else (ranked[0] if ranked else None)
        if best is not None and best.sla_feasibility in (AT_RISK, INFEASIBLE):
            at_risk_ids.add(delivery_request.pk)
    return at_risk_ids


__all__ = [
    "ASSIGNABLE_STATUSES",
    "OFFERABLE_STATUSES",
    "accept_job_offer",
    "assign_delivery",
    "at_risk_delivery_ids",
    "decline_job_offer",
    "offer_delivery",
    "reassign_delivery",
    "recommend_couriers",
]
