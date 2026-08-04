"""Incident open/resolve, incident actions, and the return-to-sender flow.

Cross-app note: this module imports `apps.deliveries.state_machine` and
`apps.custody.services` at module scope (safe — neither of those modules
needs to import `apps.incidents` back at module scope). The *reverse*
direction (`apps.deliveries.state_machine.validate_delivered` needing to
know whether an open severe incident exists) uses a lazy, in-function
import of `apps.incidents.models`, exactly the same "one real import
direction, one lazy" convention `apps.dispatch`/`apps.couriers.eligibility`
already established (see `apps.dispatch.models`'s module docstring) — this
avoids a hard bidirectional import cycle between `apps.deliveries` and
`apps.incidents`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.custody.models import CustodyActorType, CustodyEventType
from apps.custody.services import record_event
from apps.deliveries.models import DeliveryRequest
from apps.deliveries.state_machine import transition_delivery_request
from apps.incidents.models import (
    HOLD_SEVERITIES,
    Incident,
    IncidentAction,
    IncidentActionType,
    IncidentResolutionType,
    IncidentStatus,
    ReturnResolution,
    ReturnResolutionStatus,
)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.cargo.models import Package
    from apps.facilities.models import Facility


class IncidentAlreadyResolvedError(Exception):
    """Raised by `resolve_incident` when called on an incident that is not open."""


@transaction.atomic
def open_incident(
    delivery_request: DeliveryRequest,
    *,
    category: str,
    severity: str,
    summary: str,
    actor: User | None,
    package: Package | None = None,
) -> Incident:
    """Open a new incident for `delivery_request`.

    If `severity` is in `HOLD_SEVERITIES` (SEVERE/CRITICAL), this places the
    delivery on `INCIDENT_HOLD` (docs/PRODUCT_REQUIREMENTS.md section 13:
    "Severe incidents suspend normal completion") and snapshots the
    delivery's current status so `resolve_incident` can restore it later.
    Minor/moderate incidents are still recorded (and get a real
    `INCIDENT_OPENED` custody event) but do not change the delivery's
    status.

    A second (or third...) severe incident opened while the delivery is
    *already* `INCIDENT_HOLD` (e.g. two temperature excursions in a row)
    does not attempt a redundant, illegal `INCIDENT_HOLD -> INCIDENT_HOLD`
    transition — it is still recorded with `placed_delivery_on_hold=True`,
    inheriting the *original* pre-hold status snapshot from whichever
    still-open incident first placed the hold, so `resolve_incident` can
    still restore the right status later regardless of which of the
    concurrent incidents gets resolved.
    """
    from apps.deliveries.models import DeliveryStatus

    if len(summary) > Incident.SUMMARY_MAX_LENGTH:
        raise ValidationError(
            {
                "summary": (
                    f"Summary is too long (max {Incident.SUMMARY_MAX_LENGTH:,} characters) — "
                    "this is an operational note, not a clinical record."
                )
            }
        )

    locked_delivery_request = DeliveryRequest.objects.select_for_update().get(
        pk=delivery_request.pk
    )
    places_on_hold = severity in HOLD_SEVERITIES
    already_on_hold = locked_delivery_request.status == DeliveryStatus.INCIDENT_HOLD
    should_transition = places_on_hold and not already_on_hold

    if not places_on_hold:
        status_before_hold = ""
    elif should_transition:
        status_before_hold = locked_delivery_request.status
    else:
        # Already on hold from a still-open prior incident — inherit its
        # snapshot rather than recording "incident_hold" as the thing to
        # resume to.
        prior_hold_incident = (
            Incident.objects.filter(
                delivery_request=locked_delivery_request,
                status=IncidentStatus.OPEN,
                placed_delivery_on_hold=True,
            )
            .order_by("-opened_at")
            .first()
        )
        status_before_hold = (
            prior_hold_incident.delivery_status_before_hold if prior_hold_incident else ""
        )

    incident = Incident.objects.create(
        delivery_request=locked_delivery_request,
        package=package,
        category=category,
        severity=severity,
        status=IncidentStatus.OPEN,
        summary=summary,
        opened_by=actor,
        placed_delivery_on_hold=places_on_hold,
        delivery_status_before_hold=status_before_hold,
    )

    record_event(
        locked_delivery_request,
        CustodyEventType.INCIDENT_OPENED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        package=package,
        payload={
            "incident_id": str(incident.pk),
            "category": category,
            "severity": severity,
            "summary": summary,
        },
    )

    if should_transition:
        transition_delivery_request(
            locked_delivery_request,
            DeliveryStatus.INCIDENT_HOLD,
            actor=actor,
            reason=f"Incident {incident.pk} ({category}, {severity}) opened.",
        )

    return incident


def add_incident_action(
    incident: Incident, *, action_type: str, note: str = "", actor: User | None
) -> IncidentAction:
    """Append an `IncidentAction` row and an `INCIDENT_UPDATED` custody event."""
    action = IncidentAction.objects.create(
        incident=incident, action_type=action_type, note=note, actor=actor
    )
    record_event(
        incident.delivery_request,
        CustodyEventType.INCIDENT_UPDATED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={"incident_id": str(incident.pk), "action_type": action_type, "note": note},
    )
    return action


@transaction.atomic
def resolve_incident(
    incident: Incident,
    *,
    resolution_type: str,
    resolution_note: str,
    actor: User | None,
) -> Incident:
    """Resolve an open incident, requiring a non-blank resolution note
    (docs/PRODUCT_REQUIREMENTS.md section 13: "Severe incidents suspend
    normal completion until an authorized resolution is recorded").

    If this incident is the one currently holding the delivery
    (`placed_delivery_on_hold` and the delivery is still `INCIDENT_HOLD`),
    the delivery is moved on per `resolution_type`:

    - `RESUMED`: back to `delivery_status_before_hold` (the status snapshot
      taken when the hold was placed).
    - `RETURN_TO_SENDER`: to `RETURNING`, via `initiate_return`.
    - `CANCELLED`: to `CANCELLED`.
    - `OTHER`: no automatic transition (a human follow-up action is
      expected — the incident is still marked resolved either way).
    """
    if not resolution_note or not resolution_note.strip():
        raise ValidationError("A resolution note is required to resolve an incident.")
    if incident.status != IncidentStatus.OPEN:
        raise IncidentAlreadyResolvedError(f"Incident {incident.pk} is already resolved.")

    incident.status = IncidentStatus.RESOLVED
    incident.resolved_by = actor
    incident.resolved_at = timezone.now()
    incident.resolution_type = resolution_type
    incident.resolution_note = resolution_note
    incident.save(
        update_fields=["status", "resolved_by", "resolved_at", "resolution_type", "resolution_note"]
    )

    add_incident_action(
        incident, action_type=IncidentActionType.RESOLUTION, note=resolution_note, actor=actor
    )
    record_event(
        incident.delivery_request,
        CustodyEventType.INCIDENT_RESOLVED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={
            "incident_id": str(incident.pk),
            "resolution_type": resolution_type,
            "resolution_note": resolution_note,
        },
    )

    delivery_request = DeliveryRequest.objects.select_for_update().get(
        pk=incident.delivery_request_id
    )
    from apps.deliveries.models import DeliveryStatus

    if incident.placed_delivery_on_hold and delivery_request.status == DeliveryStatus.INCIDENT_HOLD:
        if resolution_type == IncidentResolutionType.RESUMED:
            transition_delivery_request(
                delivery_request,
                incident.delivery_status_before_hold or DeliveryStatus.AT_DESTINATION,
                actor=actor,
                reason=f"Incident {incident.pk} resolved — resuming normal delivery.",
            )
        elif resolution_type == IncidentResolutionType.RETURN_TO_SENDER:
            initiate_return(
                delivery_request, incident=incident, reason=resolution_note, actor=actor
            )
        elif resolution_type == IncidentResolutionType.CANCELLED:
            transition_delivery_request(
                delivery_request,
                DeliveryStatus.CANCELLED,
                actor=actor,
                reason=f"Incident {incident.pk} resolved — delivery cancelled.",
            )
        # OTHER: no automatic transition — left on INCIDENT_HOLD for a
        # human follow-up action, deliberately (see docstring).

    return incident


@transaction.atomic
def initiate_return(
    delivery_request: DeliveryRequest,
    *,
    reason: str,
    actor: User | None,
    incident: Incident | None = None,
    return_facility: Facility | None = None,
) -> ReturnResolution:
    """Transition `delivery_request` to `RETURNING` and create a
    `ReturnResolution` row (docs/PRODUCT_REQUIREMENTS.md section 7
    "return-to-sender... resolution")."""
    from apps.deliveries.models import DeliveryStatus

    locked_delivery_request = DeliveryRequest.objects.select_for_update().get(
        pk=delivery_request.pk
    )
    transition_delivery_request(
        locked_delivery_request, DeliveryStatus.RETURNING, actor=actor, reason=reason
    )
    if return_facility is None:
        pickup_stop = locked_delivery_request.pickup_stop
        return_facility = pickup_stop.facility if pickup_stop is not None else None
    resolution = ReturnResolution.objects.create(
        delivery_request=locked_delivery_request,
        incident=incident,
        return_facility=return_facility,
        status=ReturnResolutionStatus.INITIATED,
        reason=reason,
        initiated_by=actor,
    )
    record_event(
        locked_delivery_request,
        CustodyEventType.RETURN_INITIATED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={"reason": reason, "incident_id": str(incident.pk) if incident else None},
    )
    return resolution


@transaction.atomic
def complete_return(return_resolution: ReturnResolution, *, actor: User | None) -> ReturnResolution:
    """Transition the delivery to `RETURNED` and mark the `ReturnResolution` complete."""
    from apps.deliveries.models import DeliveryStatus

    delivery_request = DeliveryRequest.objects.select_for_update().get(
        pk=return_resolution.delivery_request_id
    )
    transition_delivery_request(
        delivery_request,
        DeliveryStatus.RETURNED,
        actor=actor,
        reason=f"Return resolution {return_resolution.pk} completed.",
    )
    return_resolution.status = ReturnResolutionStatus.COMPLETED
    return_resolution.completed_by = actor
    return_resolution.completed_at = timezone.now()
    return_resolution.save(update_fields=["status", "completed_by", "completed_at"])

    record_event(
        delivery_request,
        CustodyEventType.RETURN_COMPLETED,
        actor_type=CustodyActorType.INTERNAL_OPS if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={"return_resolution_id": return_resolution.pk},
    )
    return return_resolution


__all__ = [
    "IncidentAlreadyResolvedError",
    "add_incident_action",
    "complete_return",
    "initiate_return",
    "open_incident",
    "resolve_incident",
]
