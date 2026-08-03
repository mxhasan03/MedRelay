"""The delivery status state machine — extended through Phase 5's courier
pickup/transit transitions.

docs/PRODUCT_REQUIREMENTS.md section 9 defines the full state machine
(`DRAFT` through `DELIVERED`, plus exception/terminal states). Phase 2
implemented the early-lifecycle transitions:

    DRAFT -> SUBMITTED -> VALIDATION_REQUIRED -> READY_FOR_DISPATCH

plus `CANCELLED` from any of those four states. Phase 4
(`apps.dispatch.services`) extended this same dict with the dispatch
transitions it implements:

    READY_FOR_DISPATCH -> OFFERED -> ASSIGNED
    READY_FOR_DISPATCH -> ASSIGNED   (direct assignment, skipping a broadcast offer)
    OFFERED -> READY_FOR_DISPATCH    (an offer round with no acceptance reverts to the open pool)

plus `CANCELLED` reachable from `OFFERED`/`ASSIGNED` too. Phase 5
(`apps.couriers.services.advance_delivery_status`) extends this same dict
again — per Phase 2's own instruction to do so here rather than build a
parallel transition map elsewhere — with the courier-driven middle
transitions it implements:

    ASSIGNED -> COURIER_EN_ROUTE_TO_PICKUP -> AT_PICKUP -> PICKED_UP
        -> IN_TRANSIT -> AT_DESTINATION

plus `CANCELLED` reachable from every one of those five states too (the same
"cancellation is always reachable from any active state" precedent Phase 2/4
established). **`ALLOWED_TRANSITIONS` below is still intentionally a
*partial* map: it stops at `AT_DESTINATION` and does not include
`AT_DESTINATION -> DELIVERED`.** This is a deliberate phase boundary, not an
oversight — `DELIVERED` implies proof-of-delivery capture (recipient
PIN/signature), which is Phase 6 ("custody, proof, temperature, and
incidents") work per docs/IMPLEMENTATION_ROADMAP.md. Phase 5 gets a delivery
to the destination's doorstep and stops there; see
docs/CURRENT_STATUS.md "Phase 5" for the full write-up of this boundary and
`apps.couriers.services.advance_delivery_status`'s own docstring for the
courier-authorization guard layered on top of this dict (only the currently
assigned courier, or an internal ops override, may drive these five
transitions — this module itself has no notion of "who" is asking, only
"is this transition legal at all"). A reassignment
(`apps.dispatch.services.reassign_delivery`) does not change
`DeliveryRequest.status` at all — it stays `ASSIGNED` while the underlying
`apps.dispatch.models.DeliveryAssignment` row is swapped — so no
`ASSIGNED -> ASSIGNED` self-transition entry is needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryStatus, DeliveryStatusTransition

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.deliveries.models import DeliveryRequest

# Phase-2-owned transitions. Every other `DeliveryStatus` value maps to an
# empty set here (no outgoing transition implemented yet), not because it is
# truly terminal in the product sense, but because Phase 2 does not drive
# any transition into or out of it.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    DeliveryStatus.DRAFT: frozenset({DeliveryStatus.SUBMITTED, DeliveryStatus.CANCELLED}),
    DeliveryStatus.SUBMITTED: frozenset(
        {DeliveryStatus.VALIDATION_REQUIRED, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.VALIDATION_REQUIRED: frozenset(
        {DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.READY_FOR_DISPATCH: frozenset(
        {DeliveryStatus.CANCELLED, DeliveryStatus.OFFERED, DeliveryStatus.ASSIGNED}
    ),
    DeliveryStatus.OFFERED: frozenset(
        {DeliveryStatus.ASSIGNED, DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.ASSIGNED: frozenset(
        {DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP: frozenset(
        {DeliveryStatus.AT_PICKUP, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.AT_PICKUP: frozenset({DeliveryStatus.PICKED_UP, DeliveryStatus.CANCELLED}),
    DeliveryStatus.PICKED_UP: frozenset({DeliveryStatus.IN_TRANSIT, DeliveryStatus.CANCELLED}),
    DeliveryStatus.IN_TRANSIT: frozenset({DeliveryStatus.AT_DESTINATION, DeliveryStatus.CANCELLED}),
    # AT_DESTINATION -> DELIVERED is deliberately not implemented in Phase 5 —
    # see module docstring. AT_DESTINATION has no outgoing transition here at
    # all yet (a delivery that reaches the doorstep and needs to be cancelled
    # from there is an edge case left for Phase 6's incident/return-to-sender
    # flow, not modeled here as a bare CANCELLED escape hatch).
}


def validate_ready_for_dispatch(delivery_request: DeliveryRequest) -> None:
    """Raise `ValidationError` if `delivery_request` is missing anything required
    before it may reach `READY_FOR_DISPATCH`.

    This is the hard gate implementing docs/PRODUCT_REQUIREMENTS.md section 5
    ("The request must block dispatch when required cargo or packaging
    information is missing.") and
    docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 7 ("missing cargo
    classification blocks dispatch").
    """
    from apps.cargo.services import get_cargo_policy, temperature_profile_allowed
    from apps.cargo.validation import find_prohibited_cargo_keywords

    errors: list[str] = []

    if delivery_request.cargo_class_id is None:
        errors.append("Cargo classification is required before this request can be dispatched.")
    if delivery_request.temperature_profile_id is None:
        errors.append(
            "A temperature requirement is required before this request can be dispatched."
        )

    cargo_class = delivery_request.cargo_class
    temperature_profile = delivery_request.temperature_profile
    if cargo_class is not None:
        policy = get_cargo_policy(cargo_class)
        if policy.requires_packaging_attestation and not delivery_request.has_packaging_attestation:
            errors.append(
                "A packaging/classification attestation is required for this cargo class "
                "before this request can be dispatched."
            )
        if temperature_profile is not None and not temperature_profile_allowed(
            cargo_class, temperature_profile
        ):
            errors.append(
                f"{cargo_class.name} does not permit "
                f"{temperature_profile.name} temperature control."
            )

    if delivery_request.pickup_stop is None or delivery_request.destination_stop is None:
        errors.append("Both a pickup and a destination stop are required before dispatch.")

    keyword_hits = find_prohibited_cargo_keywords(delivery_request.facility_instructions)
    if keyword_hits:
        errors.append(
            "Facility instructions appear to reference an excluded cargo/service category "
            f"({', '.join(keyword_hits)})."
        )

    if errors:
        raise ValidationError(errors)


@transaction.atomic
def transition_delivery_request(
    delivery_request: DeliveryRequest,
    to_status: str,
    *,
    actor: User | None,
    reason: str = "",
) -> DeliveryRequest:
    """Move `delivery_request` from its current status to `to_status`, recording an
    append-only `DeliveryStatusTransition` row.

    Raises `InvalidTransitionError` if `to_status` is not reachable from the
    current status per `ALLOWED_TRANSITIONS`. Raises `ValidationError` (and
    leaves the delivery request's status unchanged) if `to_status` is
    `READY_FOR_DISPATCH` and `validate_ready_for_dispatch` finds anything
    missing.
    """
    from_status = delivery_request.status
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition delivery request {delivery_request.pk} from "
            f"{from_status!r} to {to_status!r}."
        )

    if to_status == DeliveryStatus.READY_FOR_DISPATCH:
        validate_ready_for_dispatch(delivery_request)

    delivery_request.status = to_status
    delivery_request.version += 1
    delivery_request.save(update_fields=["status", "version", "updated_at"])

    DeliveryStatusTransition.objects.create(
        delivery_request=delivery_request,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=reason,
    )
    return delivery_request
