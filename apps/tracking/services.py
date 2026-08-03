"""Location-ping recording and the terminal-state cutoff.

Hard acceptance criterion (docs/IMPLEMENTATION_ROADMAP.md Phase 5):
"location stops after terminal state" — once the assignment/delivery reaches
a terminal state, further location pings for that assignment must be
rejected, not silently accepted-and-persisted. This module implements that
as an explicit **rejection** (raising `LocationPingRejectedError`, mapped to
HTTP 409 by `apps.tracking.views.LocationPingView`), not a silent no-op —
chosen so the courier's offline-event-queue client
(`static/js/offline-queue.js`) gets an unambiguous signal to stop retrying
and drop the queued ping, rather than endlessly retrying a ping that will
never succeed. This is a deliberate pick between the two options the
acceptance criterion allows ("reject/no-op") — see
`apps/tracking/tests/test_services.py` for the test proving it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from apps.deliveries.models import DeliveryStatus
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment
from apps.tracking.models import CourierLocationPing

if TYPE_CHECKING:
    from apps.couriers.models import CourierProfile

# Delivery statuses at which a courier's location is no longer meaningful to
# track. AT_DESTINATION is the one reachable through Phase 5's own pickup/
# transit workflow (apps.couriers.services.advance_delivery_status); the rest
# are not reachable by any code path in this phase (DELIVERED is Phase 6;
# CANCELLED is reachable via the state machine's cancellation escape hatch;
# REJECTED/FAILED/RETURNED are placeholder enum values with no transition
# into them anywhere yet) but are included defensively so this stays correct
# the moment a later phase reaches them. INCIDENT_HOLD/RETURNING are
# deliberately *not* included — a courier already en route may still be
# moving (returning cargo, awaiting an incident resolution) and neither is
# reachable via any Phase 5 code path anyway, so there is nothing to
# defensively guard against yet.
TERMINAL_DELIVERY_STATUSES = frozenset(
    {
        DeliveryStatus.AT_DESTINATION,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.CANCELLED,
        DeliveryStatus.REJECTED,
        DeliveryStatus.FAILED,
        DeliveryStatus.RETURNED,
    }
)


class LocationPingRejectedError(Exception):
    """Raised by `record_location_ping` when the assignment/delivery has
    already reached a terminal state, or the assignment is no longer ACTIVE.
    See module docstring for why this is a real rejection, not a silent
    no-op."""


def is_terminal(assignment: DeliveryAssignment) -> bool:
    """True if `assignment` should no longer receive location pings: the
    assignment itself is no longer ACTIVE (reassigned/completed/cancelled),
    or its delivery request has reached a terminal delivery status."""
    if assignment.status != AssignmentStatus.ACTIVE:
        return True
    return assignment.delivery_request.status in TERMINAL_DELIVERY_STATUSES


def record_location_ping(
    assignment: DeliveryAssignment,
    *,
    courier: CourierProfile,
    latitude: Decimal,
    longitude: Decimal,
    accuracy_meters: Decimal | None = None,
) -> CourierLocationPing:
    """Record one location ping for `assignment`, or raise
    `LocationPingRejectedError` if it has already reached a terminal state.

    Ownership (the ping must belong to the courier holding this assignment)
    is checked by the caller (`apps.tracking.views.LocationPingView`) before
    this is even called — the same division of labor
    `apps.couriers.services.advance_delivery_status` uses for its own
    authorization check.
    """
    if is_terminal(assignment):
        raise LocationPingRejectedError(
            f"Assignment {assignment.pk} has reached a terminal state "
            f"(assignment status: {assignment.status!r}, delivery status: "
            f"{assignment.delivery_request.status!r}); no further location pings are accepted."
        )
    return CourierLocationPing.objects.create(
        assignment=assignment,
        courier=courier,
        latitude=latitude,
        longitude=longitude,
        accuracy_meters=accuracy_meters,
    )


__all__ = [
    "TERMINAL_DELIVERY_STATUSES",
    "LocationPingRejectedError",
    "is_terminal",
    "record_location_ping",
]
