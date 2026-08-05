"""Courier-facing service layer: pickup/transit status advancement,
courier-portal access control, availability self-service updates, the
delivery progress-tracker helper, the cargo-handling boundary statement, and
the shared credential-expiration-summary query (Phase "courier PWA
availability/profile/active-delivery" pass — see docs/CURRENT_STATUS.md for
the full write-up).

Cross-app note (matches `apps.couriers.eligibility`'s exact convention): this
module needs `apps.dispatch.models.DeliveryAssignment` (to find who is
currently assigned) and `apps.organizations.services.can_dispatch` (for the
internal-ops-override check) — both imported lazily inside function bodies,
not at module scope, because `apps.dispatch.services` already imports
`apps.couriers.eligibility`/`apps.couriers.models` at module scope. A
module-scope import here of `apps.dispatch.models` would make the dependency
bidirectional at Python import time; the lazy, in-function import avoids
that, exactly like `apps.couriers.eligibility`'s own lazy imports of
`apps.dispatch.models`/`apps.dispatch.sla`. `apps.cargo.models` and
`apps.facilities.models` are imported at module scope below, matching
`apps.couriers.eligibility`'s own precedent (it already imports
`apps.cargo.models.TemperatureProfileCode` at module scope) — neither app
imports anything from `apps.couriers`, so there is no circularity risk.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.cargo.models import TemperatureProfileCode
from apps.couriers.models import CourierAvailability, CourierCredential
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.deliveries.state_machine import transition_delivery_request
from apps.facilities.models import ServiceZone

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

# Phase 6: which custody-event type (if any) a given courier-driven status
# advance corresponds to (apps.custody.models.CustodyEventType). Not every
# step has one — e.g. ASSIGNED -> COURIER_EN_ROUTE_TO_PICKUP has no
# dedicated event type in docs/PRODUCT_REQUIREMENTS.md section 10's
# vocabulary, so it is absent from this dict on purpose.
_ADVANCE_CUSTODY_EVENT_TYPES: dict[str, str] = {
    DeliveryStatus.AT_PICKUP: "courier_arrived",
    DeliveryStatus.IN_TRANSIT: "route_started",
    DeliveryStatus.AT_DESTINATION: "facility_arrival",
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

    result = transition_delivery_request(delivery_request, to_status, actor=actor)

    custody_event_type = _ADVANCE_CUSTODY_EVENT_TYPES.get(to_status)
    if custody_event_type is not None:
        from apps.custody.models import CustodyActorType
        from apps.custody.services import record_event

        record_event(
            result,
            custody_event_type,
            actor_type=CustodyActorType.COURIER,
            actor_user=actor,
            payload={"to_status": to_status},
        )

    return result


# --- Availability self-service (docs/PRODUCT_REQUIREMENTS.md section 6
# "Availability": online/offline, shift availability, current service zone,
# current capacity) -----------------------------------------------------


def update_courier_availability(
    courier: CourierProfile,
    *,
    is_online: bool,
    current_service_zone_id: Any,
    shift_start: str | None,
    shift_end: str | None,
    max_concurrent_deliveries: Any,
) -> CourierAvailability:
    """Update (creating the row on first use) `courier`'s own
    `CourierAvailability`. Only ever called with `courier` derived from
    `request.user.courier_profile` by the view below — a courier can only
    ever reach their own row through this function, the same "derive the
    scope from the authenticated actor, never trust a client-supplied
    courier id" pattern every other courier-facing mutation in this module
    uses.

    `current_service_zone_id` of `None`/`""`/`0` clears the zone. `shift_start`/
    `shift_end` are `None`/blank to clear, or an ISO `HH:MM[:SS]` string.
    Raises `django.core.exceptions.ValidationError` (caught by the view and
    turned into a 400) on any malformed input — never silently coerces bad
    input into a guessed value.
    """
    availability, _ = CourierAvailability.objects.get_or_create(courier=courier)
    availability.is_online = is_online

    if current_service_zone_id:
        try:
            zone = ServiceZone.objects.get(pk=current_service_zone_id, is_active=True)
        except (ServiceZone.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError("Unknown or inactive service zone.") from exc
        availability.current_service_zone = zone
    else:
        availability.current_service_zone = None

    for field_name, raw_value in (("shift_start", shift_start), ("shift_end", shift_end)):
        if raw_value:
            try:
                setattr(availability, field_name, datetime.time.fromisoformat(raw_value))
            except ValueError as exc:
                raise ValidationError(f"{field_name} must be a valid HH:MM time.") from exc
        else:
            setattr(availability, field_name, None)

    if max_concurrent_deliveries not in (None, ""):
        try:
            parsed_capacity = int(max_concurrent_deliveries)
        except (TypeError, ValueError) as exc:
            raise ValidationError("max_concurrent_deliveries must be an integer.") from exc
        if parsed_capacity < 0:
            raise ValidationError("max_concurrent_deliveries must not be negative.")
        availability.max_concurrent_deliveries = parsed_capacity

    availability.save()
    return availability


# --- Credential-expiration summary — shared by the
# `flag_expiring_credentials` management command and the new courier profile
# screen (docs/PRODUCT_REQUIREMENTS.md section 6 "credential expirations").
# The actual query logic lives on `CourierCredentialQuerySet.expired`/
# `.expiring_within` (apps.couriers.models); this function is the one shared
# call site both consumers use, so the "which credentials count as
# expired/expiring" definition is never duplicated. -------------------------


@dataclass(frozen=True)
class CredentialExpirationSummary:
    expired: list[CourierCredential] = field(default_factory=list)
    expiring_soon: list[CourierCredential] = field(default_factory=list)


def credential_expiration_summary(
    *,
    courier: CourierProfile | None = None,
    within_days: int = 30,
    as_of: datetime.date | None = None,
) -> CredentialExpirationSummary:
    """Already-expired and soon-to-expire credentials, optionally scoped to
    one `courier` (the profile screen's use case) or across every courier
    (the management command's use case, `courier=None`)."""
    queryset = CourierCredential.objects.select_related("courier__user", "reviewed_by")
    if courier is not None:
        queryset = queryset.filter(courier=courier)
    return CredentialExpirationSummary(
        expired=list(queryset.expired(as_of=as_of)),
        expiring_soon=list(queryset.expiring_within(within_days, as_of=as_of)),
    )


# --- Delivery progress-tracker (active-delivery screen) ---------------------

# The happy-path courier-driven sequence, display order only — mirrors
# `COURIER_ADVANCE_SEQUENCE`'s keys plus the final `DELIVERED` state that
# sequence never advances *to* a "next" step from (courier.services.
# advance_delivery_status stops at AT_DESTINATION; DELIVERED is reached via
# apps.couriers.views.CompleteDeliveryView instead). This list is a display
# helper only — it does not gate any action.
COURIER_TIMELINE_STATUSES: list[str] = [
    DeliveryStatus.ASSIGNED,
    DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
    DeliveryStatus.AT_PICKUP,
    DeliveryStatus.PICKED_UP,
    DeliveryStatus.IN_TRANSIT,
    DeliveryStatus.AT_DESTINATION,
    DeliveryStatus.DELIVERED,
]


@dataclass(frozen=True)
class TimelineStep:
    code: str
    label: str
    state: str  # "completed" | "current" | "upcoming"


def delivery_timeline_steps(delivery_request: DeliveryRequest) -> list[TimelineStep]:
    """Build the ordered visual progress-tracker steps for the courier
    active-delivery screen, replacing the old plain bulleted
    `DeliveryStatusTransition` list.

    If the delivery is in a state outside the happy-path sequence above
    (e.g. `INCIDENT_HOLD`, `CANCELLED`, `RETURNING`) every step is rendered
    as "upcoming" rather than guessing a position — an honest "no happy-path
    position for this state" rendering, never a misleading one.
    """
    try:
        current_index = COURIER_TIMELINE_STATUSES.index(delivery_request.status)
    except ValueError:
        current_index = -1

    steps = []
    for index, status in enumerate(COURIER_TIMELINE_STATUSES):
        if current_index == -1 or index > current_index:
            state = "upcoming"
        elif index == current_index:
            state = "current"
        else:
            state = "completed"
        steps.append(TimelineStep(code=status, label=DeliveryStatus(status).label, state=state))
    return steps


# --- Cargo handling boundary statement (active-delivery screen) ------------


def cargo_handling_boundary_text(delivery_request: DeliveryRequest) -> str:
    """A plain-language "what you may/may not do with this specific package"
    statement, derived from the delivery's real `CargoClass`/`CargoPolicy`/
    `TemperatureProfile` rows — never one generic sentence reused for every
    delivery (docs/PRODUCT_REQUIREMENTS.md section 6 "cargo handling
    boundary"). Every clause below is read off this delivery's actual cargo
    class name, its `CargoPolicy.notes`, and its actual temperature profile —
    so a Class 1 ambient delivery and a Class 2 refrigerated delivery render
    genuinely different text.
    """
    cargo_class = delivery_request.cargo_class
    if cargo_class is None:
        return "No cargo class has been recorded for this delivery yet."

    sentences = [f"{cargo_class.name}."]

    temperature_profile = delivery_request.temperature_profile
    if temperature_profile is not None:
        sentences.append(f"{temperature_profile.name}.")
        if temperature_profile.code == TemperatureProfileCode.REFRIGERATED:
            sentences.append(
                "Keep this package in an insulated/refrigerated container at all times and "
                "minimize time outside temperature control."
            )
        else:
            sentences.append("No active temperature control is required for this package.")

    policy = getattr(cargo_class, "policy", None)
    if policy is not None and policy.notes:
        sentences.append(policy.notes)

    sentences.append(
        "You may not open, inspect the contents of, or repack this package under any "
        "circumstances."
    )
    return " ".join(sentences)


__all__ = [
    "COURIER_ADVANCE_SEQUENCE",
    "COURIER_TIMELINE_STATUSES",
    "CredentialExpirationSummary",
    "TimelineStep",
    "advance_delivery_status",
    "can_access_courier_portal",
    "cargo_handling_boundary_text",
    "credential_expiration_summary",
    "delivery_timeline_steps",
    "update_courier_availability",
]
