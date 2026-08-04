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
established). A reassignment
(`apps.dispatch.services.reassign_delivery`) does not change
`DeliveryRequest.status` at all — it stays `ASSIGNED` while the underlying
`apps.dispatch.models.DeliveryAssignment` row is swapped — so no
`ASSIGNED -> ASSIGNED` self-transition entry is needed here.

**Phase 6** (`apps.custody`/`apps.incidents`/`apps.temperature`) finally
extends this dict past `AT_DESTINATION`:

    AT_DESTINATION -> DELIVERED   (gated by validate_delivered below)
    {ASSIGNED, COURIER_EN_ROUTE_TO_PICKUP, AT_PICKUP, PICKED_UP, IN_TRANSIT,
        AT_DESTINATION} -> INCIDENT_HOLD
    {ASSIGNED, COURIER_EN_ROUTE_TO_PICKUP, AT_PICKUP, PICKED_UP, IN_TRANSIT,
        AT_DESTINATION} -> RETURNING   (a return can also be initiated
        directly, without a formal incident ever being opened — per
        docs/IMPLEMENTATION_ROADMAP.md's "RETURNING -> RETURNED... from
        INCIDENT_HOLD (or from other applicable states per the state
        diagram)")
    INCIDENT_HOLD -> {ASSIGNED, COURIER_EN_ROUTE_TO_PICKUP, AT_PICKUP,
        PICKED_UP, IN_TRANSIT, AT_DESTINATION, RETURNING, CANCELLED, FAILED}
    RETURNING -> RETURNED

`INCIDENT_HOLD` is only ever entered/exited through
`apps.incidents.services.open_incident`/`resolve_incident` (never a bare
`transition_delivery_request` call from arbitrary code) — those functions
snapshot which status to resume to and pick the right destination status.
This module's own hard, un-bypassable guard is `validate_delivered` below:
`DELIVERED` requires a `ProofOfDelivery` row to exist *and* no open
severe/critical incident, checked every time `DELIVERED` is attempted
regardless of which service function is calling — see
docs/CURRENT_STATUS.md "Phase 6" section for the full write-up and
`apps/deliveries/tests/test_state_machine.py`'s incident-hold-blocks-DELIVERED
test.
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

# The "active courier workflow" states an incident can place on hold from —
# matches exactly the states apps.couriers.services.COURIER_ADVANCE_SEQUENCE
# drives a delivery through, plus AT_DESTINATION (its final stop).
_ACTIVE_COURIER_STATES = frozenset(
    {
        DeliveryStatus.ASSIGNED,
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
        DeliveryStatus.AT_DESTINATION,
    }
)

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
        {
            DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
            DeliveryStatus.CANCELLED,
            DeliveryStatus.INCIDENT_HOLD,
            DeliveryStatus.RETURNING,
        }
    ),
    DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP: frozenset(
        {
            DeliveryStatus.AT_PICKUP,
            DeliveryStatus.CANCELLED,
            DeliveryStatus.INCIDENT_HOLD,
            DeliveryStatus.RETURNING,
        }
    ),
    DeliveryStatus.AT_PICKUP: frozenset(
        {
            DeliveryStatus.PICKED_UP,
            DeliveryStatus.CANCELLED,
            DeliveryStatus.INCIDENT_HOLD,
            DeliveryStatus.RETURNING,
        }
    ),
    DeliveryStatus.PICKED_UP: frozenset(
        {
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.CANCELLED,
            DeliveryStatus.INCIDENT_HOLD,
            DeliveryStatus.RETURNING,
        }
    ),
    DeliveryStatus.IN_TRANSIT: frozenset(
        {
            DeliveryStatus.AT_DESTINATION,
            DeliveryStatus.CANCELLED,
            DeliveryStatus.INCIDENT_HOLD,
            DeliveryStatus.RETURNING,
        }
    ),
    # AT_DESTINATION -> DELIVERED (Phase 6, gated by validate_delivered).
    DeliveryStatus.AT_DESTINATION: frozenset(
        {DeliveryStatus.DELIVERED, DeliveryStatus.INCIDENT_HOLD, DeliveryStatus.RETURNING}
    ),
    # Phase 6: incident-hold resume/exit paths. Structurally this dict allows
    # resuming to any active courier state or exiting to RETURNING/CANCELLED/
    # FAILED — apps.incidents.services.resolve_incident is what actually
    # decides *which* one to use for a given incident (see module docstring).
    DeliveryStatus.INCIDENT_HOLD: _ACTIVE_COURIER_STATES
    | frozenset({DeliveryStatus.RETURNING, DeliveryStatus.CANCELLED, DeliveryStatus.FAILED}),
    # Phase 6: return-to-sender completion.
    DeliveryStatus.RETURNING: frozenset({DeliveryStatus.RETURNED}),
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


def validate_delivered(delivery_request: DeliveryRequest) -> None:
    """Raise `ValidationError` if `delivery_request` is not yet eligible for
    `DELIVERED`.

    This is the hard, un-bypassable gate for two Phase 6 invariants
    (docs/ARCHITECTURE_AND_DATA_MODEL.md section 5):

    - "delivery requires pickup/custody acceptance" — a `ProofOfDelivery` row
      (recipient PIN/signature capture) must exist.
    - "incident hold blocks completion until an authorized resolution" — no
      `OPEN` incident whose severity is in `apps.incidents.models.
      HOLD_SEVERITIES` may exist for this delivery.

    Lazy imports (of `apps.custody.models`/`apps.incidents.models`) match
    `validate_ready_for_dispatch`'s own convention above, and avoid a
    module-scope import cycle: `apps.incidents.services` imports this module
    at module scope (to call `transition_delivery_request`), so this module
    cannot import `apps.incidents` back at module scope without creating a
    real cycle.
    """
    from apps.custody.models import ProofOfDelivery
    from apps.incidents.models import HOLD_SEVERITIES, Incident, IncidentStatus

    errors: list[str] = []

    if not ProofOfDelivery.objects.filter(delivery_request=delivery_request).exists():
        errors.append(
            "Proof of delivery (recipient PIN/signature capture) is required before this "
            "delivery can be marked delivered."
        )

    open_severe_incident_exists = Incident.objects.filter(
        delivery_request=delivery_request,
        status=IncidentStatus.OPEN,
        severity__in=HOLD_SEVERITIES,
    ).exists()
    if open_severe_incident_exists:
        errors.append(
            "An open incident is placing this delivery on hold; it must be resolved before "
            "delivery can be completed."
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
    `READY_FOR_DISPATCH`/`DELIVERED` and the corresponding validation gate
    finds anything missing.
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
    elif to_status == DeliveryStatus.DELIVERED:
        validate_delivered(delivery_request)

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
