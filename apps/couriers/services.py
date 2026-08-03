"""Courier-facing service layer: pickup/transit status advancement and
courier-portal access control.

Cross-app note (matches `apps.couriers.eligibility`'s exact convention): this
module needs `apps.dispatch.models.DeliveryAssignment` (to find who is
currently assigned) and `apps.organizations.services.can_dispatch` (for the
internal-ops-override check) — both imported lazily inside function bodies,
not at module scope, because `apps.dispatch.services` already imports
`apps.couriers.eligibility`/`apps.couriers.models` at module scope. A
module-scope import here of `apps.dispatch.models` would make the dependency
bidirectional at Python import time; the lazy, in-function import avoids
that, exactly like `apps.couriers.eligibility`'s own lazy imports of
`apps.dispatch.models`/`apps.dispatch.sla`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.deliveries.state_machine import transition_delivery_request

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser

    from apps.accounts.models import User
    from apps.couriers.models import CourierProfile

# The single-step courier-driven transitions this phase implements
# (apps.deliveries.state_machine.ALLOWED_TRANSITIONS also allows CANCELLED
# from every one of these statuses, but that escape hatch is deliberately not
# exposed through this "advance" helper — cancelling a delivery is an
# ops/dispatcher action in this prototype, not part of the courier's own
# forward workflow, so it has no button in the courier PWA this phase).
COURIER_ADVANCE_SEQUENCE: dict[str, str] = {
    DeliveryStatus.ASSIGNED: DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
    DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP: DeliveryStatus.AT_PICKUP,
    DeliveryStatus.AT_PICKUP: DeliveryStatus.PICKED_UP,
    DeliveryStatus.PICKED_UP: DeliveryStatus.IN_TRANSIT,
    DeliveryStatus.IN_TRANSIT: DeliveryStatus.AT_DESTINATION,
}


def can_access_courier_portal(user: User | AnonymousUser) -> bool:
    """Can `user` view/act on the courier PWA at all? Mirrors
    `apps.organizations.services.can_dispatch`'s "one explicit named check"
    convention: `User.is_courier` alone grants nothing (same rule as
    `is_internal_staff`) — a real `CourierProfile` row must also exist."""
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_courier", False)
        and hasattr(user, "courier_profile")
    )


def _active_assignment_for(delivery_request: DeliveryRequest) -> Any:
    from apps.dispatch.models import AssignmentStatus, DeliveryAssignment

    return (
        DeliveryAssignment.objects.select_related("courier__user")
        .filter(delivery_request=delivery_request, status=AssignmentStatus.ACTIVE)
        .first()
    )


@transaction.atomic
def advance_delivery_status(
    delivery_id: Any, courier: CourierProfile, to_status: str, *, actor: User | None
) -> DeliveryRequest:
    """Advance `delivery_id` one step along the Phase 5 courier workflow
    (`ASSIGNED -> COURIER_EN_ROUTE_TO_PICKUP -> AT_PICKUP -> PICKED_UP ->
    IN_TRANSIT -> AT_DESTINATION`).

    Authorization: only the courier currently holding the `ACTIVE`
    `DeliveryAssignment` for this delivery may call this — or an internal ops
    user with dispatch-board access (`apps.organizations.services.can_dispatch`),
    covering "an internal ops override" per this phase's scope. Raises
    `PermissionError` if neither holds (a plain exception, matching
    `apps.dispatch.exceptions`' "plain exception for control-flow errors"
    convention rather than reaching for Django's `PermissionDenied` in a
    service-layer function).

    Only a single forward step is ever allowed per call — `to_status` must be
    exactly the next status in `COURIER_ADVANCE_SEQUENCE` for the delivery's
    *current* status, not merely "any status eventually reachable." This is a
    stricter guard than `apps.deliveries.state_machine.ALLOWED_TRANSITIONS`
    alone provides (that dict only rejects genuinely illegal transitions) —
    deliberately, so a single client request can never skip an intermediate
    step even though nothing else in this module would stop it. Raises
    `apps.deliveries.exceptions.InvalidTransitionError` otherwise (from this
    guard directly, or bubbled up from `transition_delivery_request` itself).
    """
    delivery_request = DeliveryRequest.objects.select_for_update().get(pk=delivery_id)
    active_assignment = _active_assignment_for(delivery_request)
    is_assigned_courier = (
        active_assignment is not None and active_assignment.courier_id == courier.pk
    )
    is_ops_override = False
    if not is_assigned_courier and actor is not None:
        from apps.organizations.services import can_dispatch

        is_ops_override = can_dispatch(actor)
    if not (is_assigned_courier or is_ops_override):
        raise PermissionError(
            f"Courier {courier.pk} is not the assigned courier for delivery {delivery_id} "
            "and has no ops override access."
        )

    expected_next = COURIER_ADVANCE_SEQUENCE.get(delivery_request.status)
    if expected_next is None or expected_next != to_status:
        raise InvalidTransitionError(
            f"Cannot advance delivery {delivery_id} from {delivery_request.status!r} to "
            f"{to_status!r} via the courier pickup/transit workflow."
        )

    return transition_delivery_request(delivery_request, to_status, actor=actor)


__all__ = ["COURIER_ADVANCE_SEQUENCE", "advance_delivery_status", "can_access_courier_portal"]
