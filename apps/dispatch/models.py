"""Dispatch recommendations, job offers, assignments, dispatcher overrides,
synthetic route plans, and SLA target profiles.

Phase 4 (docs/IMPLEMENTATION_ROADMAP.md "Phase 4 — Dispatch and operations
console") is the first phase with real concurrent writers for a
`DeliveryRequest` — exactly what `DeliveryRequest.version` and this app's
transaction/row-lock discipline were reserved for
(docs/ARCHITECTURE_AND_DATA_MODEL.md section 9). See
`apps.dispatch.services` for the load-bearing entry points
(`recommend_couriers`/`assign_delivery`/`offer_delivery`/`reassign_delivery`)
and docs/CURRENT_STATUS.md "Phase 4" section for the full design write-up,
including the honest SQLite-vs-Postgres concurrency-test confidence
discussion.

Cross-app note: every FK below to `couriers.CourierProfile` and
`deliveries.DeliveryRequest` uses Django's lazy string app-label reference
(`"couriers.CourierProfile"`, `"deliveries.DeliveryRequest"`), exactly like
`apps.cargo.models`' FKs into `apps.deliveries` — no direct Python import of
either app's `models` module is needed for these relations, so there is no
import cycle at model-definition time. Separately, `apps.couriers.eligibility`
now does a *lazy, in-function* import of `apps.dispatch.models.DeliveryAssignment`
(to count real active workload) and `apps.dispatch.sla` (to compute a real
SLA-feasibility verdict) — the same "local import inside a function body"
convention already used throughout this codebase (e.g.
`DeliveryRequest.clean()`'s lazy import of `apps.cargo.validation`) to avoid a
hard import-time cycle between apps that need each other's behavior.

Design decisions actually implemented here (full write-up in
docs/CURRENT_STATUS.md "Phase 4"):

1. **One ACTIVE `DeliveryAssignment` per delivery, enforced at the database
   level**, not just in application code: a partial `UniqueConstraint` below
   (`unique_active_assignment_per_delivery_request`) makes a second
   concurrent INSERT of an ACTIVE assignment for the same delivery request
   fail with a real `IntegrityError` regardless of backend — this is the
   actual, backend-independent correctness backstop for the "one delivery
   assigned atomically" acceptance criterion. `apps.dispatch.services.
   assign_delivery` additionally takes a `select_for_update()` row lock on
   the `DeliveryRequest` row, which is a real, meaningful row lock on
   PostgreSQL but a documented no-op on SQLite (see that module's docstring
   and docs/CURRENT_STATUS.md for the honest confidence discussion).
2. **`DispatchRecommendation`/`DispatchRecommendationCandidate` are
   persisted by default**, not purely ephemeral — every real
   `recommend_couriers` call writes an audit row per candidate (score,
   factor breakdown, eligibility, human-readable reasons) because dispatch
   decisions are exactly the kind of safety/audit-relevant record this
   prototype should keep an explainable trail of. This is a deliberate,
   documented choice (the roadmap explicitly allows "computed on-demand and
   optionally persisted" — Phase 4 chooses "persist by default", cheap at
   this prototype's demo data volumes).
3. **`DispatchOverride` only ever records a *soft* scoring/ranking choice**
   (assigning a non-top-ranked eligible candidate, or reassigning an
   already-assigned delivery) — it is never consulted to decide whether a
   hard-eligibility failure can be bypassed. `apps.dispatch.services.
   assign_delivery`/`reassign_delivery` call `apps.couriers.eligibility.
   check_courier_eligibility` unconditionally, before any override record is
   ever written, and raise `apps.dispatch.exceptions.IneligibleCourierError`
   with no code path that inspects `reason` to skip that check.
4. **`RoutePlan`/`RouteLeg` are synthetic placeholders**, reusing the exact
   same haversine-distance approach as Phase 2's quote engine
   (`apps.deliveries.pricing.estimate_distance_km`) — not a real OSRM/
   routing-engine call, consistent with the zero-cost policy. "ETA to
   pickup" has no real courier-location signal to draw on at all yet
   (`CourierLocationPing` is Phase 5 work, per Phase 3's own "Known gaps"),
   so it is a small set of documented synthetic tiers based on service-zone
   match, not a distance calculation — see `apps.dispatch.sla` for the exact
   values and rationale.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AssignmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REASSIGNED = "reassigned", "Reassigned"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


# The only statuses `apps.couriers.eligibility._current_workload` counts as
# "currently occupying one of the courier's concurrent-delivery slots".
ACTIVE_ASSIGNMENT_STATUSES = frozenset({AssignmentStatus.ACTIVE})

# Delivery statuses `apps.dispatch.services.assign_delivery`/`offer_delivery`
# may act on. Kept here (rather than only inline in services.py) so
# `apps.deliveries.state_machine.ALLOWED_TRANSITIONS` and this module agree on
# the same vocabulary at a glance.
ASSIGNABLE_DELIVERY_STATUSES = frozenset({"ready_for_dispatch", "offered"})


class DeliveryAssignment(models.Model):
    """The authoritative record of a courier assigned to a delivery request.

    Phase 3 (`apps.couriers.eligibility`) documented that its "current
    capacity exceeded" hard filter's workload count was honestly always `0`
    because no model existed yet to count real active assignments against —
    this is that model. `apps.couriers.eligibility._current_workload` now
    counts `DeliveryAssignment.objects.filter(courier=courier,
    status=AssignmentStatus.ACTIVE).count()` via a lazy import (see module
    docstring).

    A delivery can accumulate more than one `DeliveryAssignment` row over its
    lifetime (an initial assignment, then a `REASSIGNED` row if
    `apps.dispatch.services.reassign_delivery` runs) — this is a history, not
    a single mutable slot, but at most one row may be `ACTIVE` for a given
    delivery request at any time, enforced by the partial unique constraint
    below (see module docstring point 1 for the honest SQLite-vs-Postgres
    strength of that guarantee).
    """

    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="assignments"
    )
    courier = models.ForeignKey(
        "couriers.CourierProfile", on_delete=models.PROTECT, related_name="assignments"
    )
    status = models.CharField(
        max_length=16, choices=AssignmentStatus.choices, default=AssignmentStatus.ACTIVE
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_assignments_made",
        help_text="The dispatcher (or None for an unattended/automated assignment) who made "
        "this assignment.",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    unassigned_at = models.DateTimeField(null=True, blank=True)
    score_at_assignment = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="The candidate's total explainable score (apps.dispatch.scoring) at the "
        "moment this assignment was made, kept for audit even if scoring inputs later change.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_request"],
                condition=models.Q(status=AssignmentStatus.ACTIVE),
                name="unique_active_assignment_per_delivery_request",
            )
        ]

    def __str__(self) -> str:
        return f"{self.delivery_request_id} -> {self.courier} ({self.status})"


class JobOfferStatus(models.TextChoices):
    OFFERED = "offered", "Offered"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class JobOffer(models.Model):
    """One offer of `delivery_request` to `courier`, with an expiration time.

    `apps.dispatch.services.offer_delivery` can create several `JobOffer`
    rows at once (a broadcast-style offer to more than one eligible
    candidate) — Phase 4 builds the model and the dispatcher-facing creation
    path only. **Phase 5 implements the accept/decline transitions** — see
    `apps.dispatch.services.accept_job_offer`/`decline_job_offer`, which reuse
    `assign_delivery`'s atomicity/hard-eligibility guarantees for acceptance
    rather than duplicating them (docs/CURRENT_STATUS.md "Phase 5"). `is_expired`
    is still a plain computed property for display; nothing automatically
    flips a stale `OFFERED` row's stored `status` to `EXPIRED` in the
    database, in the background or otherwise (still Phase 7 territory) —
    `accept_job_offer` rejects an attempt to accept an already-expired offer
    (`apps.dispatch.services._reject_if_not_acceptable`, checking `is_expired`
    read-only) without ever persisting a status change for it, specifically
    because that write would be rolled back anyway the moment the resulting
    exception propagates out of `accept_job_offer`'s own `transaction.atomic()`
    -- see that function's docstring for the real bug this avoided.
    """

    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="job_offers"
    )
    courier = models.ForeignKey(
        "couriers.CourierProfile", on_delete=models.CASCADE, related_name="job_offers"
    )
    status = models.CharField(
        max_length=16, choices=JobOfferStatus.choices, default=JobOfferStatus.OFFERED
    )
    offered_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(
        blank=True,
        help_text=(
            "Phase 5: optional courier-recorded reason for declining "
            "(docs/PRODUCT_REQUIREMENTS.md section 6 — 'Legitimate cargo/safety rejection must "
            "be recordable'). Never required; a courier may decline without giving a reason."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_job_offers_created",
    )

    class Meta:
        ordering = ["-offered_at"]

    def __str__(self) -> str:
        return f"Offer: {self.delivery_request_id} -> {self.courier} ({self.status})"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return self.status == JobOfferStatus.OFFERED and timezone.now() > self.expires_at


class DispatchOverrideType(models.TextChoices):
    NOT_TOP_RANKED = "not_top_ranked", "Chose a non-top-ranked eligible candidate"
    REASSIGNMENT = "reassignment", "Reassignment away from an existing assignment"
    NOTE = "note", "Dispatcher note on an otherwise top-ranked choice"


class DispatchOverride(models.Model):
    """A dispatcher's mandatory, recorded reason for a soft/scoring override.

    Per docs/PRODUCT_REQUIREMENTS.md section 11 ("Dispatchers can override
    recommendations but must record a reason. Overrides never bypass hard
    safety/authorization rules."). See module docstring point 3 — this model
    is never in the code path that decides eligibility; it is written to
    *after* `apps.couriers.eligibility.check_courier_eligibility` has already
    passed for the chosen courier.
    """

    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="dispatch_overrides"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_overrides_made",
    )
    override_type = models.CharField(max_length=16, choices=DispatchOverrideType.choices)
    reason = models.TextField(help_text="Required — see save() below, never blank.")
    chosen_courier = models.ForeignKey(
        "couriers.CourierProfile",
        on_delete=models.PROTECT,
        related_name="dispatch_overrides_chosen",
    )
    previous_courier = models.ForeignKey(
        "couriers.CourierProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_overrides_previous",
        help_text="Set only for REASSIGNMENT overrides — the courier being reassigned away from.",
    )
    recommendation = models.ForeignKey(
        "dispatch.DispatchRecommendation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overrides",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Override on {self.delivery_request_id}: {self.get_override_type_display()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # A blank reason must never reach the database — this is the model-level
        # backstop for "dispatchers must record a reason" (the service layer in
        # apps.dispatch.services also checks this before ever constructing one).
        if not self.reason or not self.reason.strip():
            raise ValidationError("DispatchOverride.reason is required and cannot be blank.")
        super().save(*args, **kwargs)


class DispatchRecommendation(models.Model):
    """One computed, persisted "ranked candidate list" run for a delivery
    request — the audit/explainability record backing
    `apps.dispatch.services.recommend_couriers`. See module docstring point 2
    for the "persisted by default" design decision.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest",
        on_delete=models.CASCADE,
        related_name="dispatch_recommendations",
    )
    computed_at = models.DateTimeField(auto_now_add=True)
    computed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_recommendations_run",
    )
    candidate_count = models.PositiveIntegerField(default=0)
    eligible_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-computed_at"]

    def __str__(self) -> str:
        return f"Recommendation for {self.delivery_request_id} @ {self.computed_at}"


class DispatchRecommendationCandidate(models.Model):
    """One ranked candidate row within a `DispatchRecommendation`.

    `factor_scores`/`reasons`/`hard_failure_reasons` are JSON snapshots of
    `apps.dispatch.scoring.DispatchCandidate` at computation time (Decimal
    values converted to `float`/`str` for JSON-serializability — see
    `apps.dispatch.services._persist_recommendation`).
    """

    recommendation = models.ForeignKey(
        DispatchRecommendation, on_delete=models.CASCADE, related_name="candidates"
    )
    courier = models.ForeignKey(
        "couriers.CourierProfile",
        on_delete=models.CASCADE,
        related_name="dispatch_recommendation_candidates",
    )
    rank = models.PositiveIntegerField()
    eligible = models.BooleanField()
    total_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    factor_scores = models.JSONField(default=dict, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    hard_failure_reasons = models.JSONField(default=list, blank=True)
    eta_to_pickup_minutes = models.DecimalField(max_digits=6, decimal_places=1)
    sla_slack_minutes = models.DecimalField(max_digits=9, decimal_places=1)
    sla_feasibility = models.CharField(max_length=16)
    toll_estimate = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["recommendation_id", "rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["recommendation", "courier"], name="unique_candidate_per_recommendation"
            )
        ]

    def __str__(self) -> str:
        return f"#{self.rank} {self.courier} for recommendation {self.recommendation_id}"


class RouteLegType(models.TextChoices):
    TO_PICKUP = "to_pickup", "Courier to Pickup"
    PICKUP_TO_DESTINATION = "pickup_to_destination", "Pickup to Destination"


class RoutePlan(models.Model):
    """A synthetic, single-current-plan-per-delivery route estimate, built when
    a delivery is assigned/reassigned (`apps.dispatch.services._build_route_plan`).

    See module docstring point 4 — this is not real routing; distances reuse
    `apps.deliveries.pricing.estimate_distance_km`'s haversine estimate and
    durations reuse the same `average_speed_kmh` `PricingRule`. One row per
    delivery request (`OneToOneField`, `update_or_create`d), matching Phase
    2's `Quote` model's "one current row, not a history table" precedent.
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="route_plan"
    )
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_plans",
    )
    total_distance_km = models.DecimalField(max_digits=7, decimal_places=2)
    total_duration_minutes = models.DecimalField(max_digits=7, decimal_places=1)
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Route plan for {self.delivery_request_id}"


class RouteLeg(models.Model):
    """One synthetic leg of a `RoutePlan` — currently always exactly two legs
    (courier-to-pickup, pickup-to-destination); see module docstring point 4.
    `from_facility` is null for the courier-to-pickup leg, since there is no
    real courier-location model yet to name an origin facility for.
    """

    route_plan = models.ForeignKey(RoutePlan, on_delete=models.CASCADE, related_name="legs")
    sequence = models.PositiveIntegerField()
    leg_type = models.CharField(max_length=32, choices=RouteLegType.choices)
    from_facility = models.ForeignKey(
        "facilities.Facility",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_legs_from",
    )
    to_facility = models.ForeignKey(
        "facilities.Facility",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_legs_to",
    )
    distance_km = models.DecimalField(max_digits=7, decimal_places=2)
    duration_minutes = models.DecimalField(max_digits=7, decimal_places=1)

    class Meta:
        ordering = ["route_plan_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["route_plan", "sequence"], name="unique_route_leg_sequence"
            )
        ]

    def __str__(self) -> str:
        return f"Leg {self.sequence} ({self.get_leg_type_display()}) of {self.route_plan_id}"


class SLAProfile(models.Model):
    """Per-service-level SLA target definitions.

    Turns Phase 3's `EligibilityResult.sla_feasibility` placeholder
    (`"not_evaluated"`, always) into a real (if still synthetic) verdict:
    `apps.dispatch.sla.compute_sla_estimate` compares a synthetic
    ETA-to-pickup + transit-time estimate against `DeliveryRequest.
    required_delivery_by`, and classifies the result as `"feasible"`,
    `"at_risk"`, or `"infeasible"` using `min_slack_minutes` from the row
    matching the delivery's `service_level`. Seeded via a data migration
    (mirrors `apps.deliveries.PricingRule`'s "admin-editable synthetic
    reference data" pattern) with one row per
    `apps.deliveries.models.ServiceLevel` value.
    """

    service_level = models.CharField(
        max_length=16, unique=True, help_text="An apps.deliveries.models.ServiceLevel value."
    )
    min_slack_minutes = models.PositiveIntegerField(
        help_text="Minimum acceptable buffer (minutes) between the estimated delivery "
        "completion and required_delivery_by before a delivery is flagged 'at risk'. "
        "Negative slack is always 'infeasible' regardless of this value."
    )
    description = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["service_level"]

    def __str__(self) -> str:
        return f"{self.service_level} (min slack {self.min_slack_minutes}m)"
