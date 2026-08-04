"""Tests for `open_incident`/`resolve_incident`/`add_incident_action`/
`initiate_return`/`complete_return` — the incident-hold invariant and the
return-to-sender flow.

Delivery requests here are built with their `status` set directly via the
factory (rather than walked through every real upstream transition) since
these tests exercise `apps.incidents.services` in isolation, not the full
delivery lifecycle — `apps.deliveries.tests.test_state_machine` and
`apps.couriers.tests.test_services` already cover the real end-to-end
transition sequencing.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.custody.models import CustodyEventType, ProofOfDelivery
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryStatus
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.incidents.models import (
    IncidentActionType,
    IncidentCategory,
    IncidentResolutionType,
    IncidentSeverity,
    IncidentStatus,
    ReturnResolution,
    ReturnResolutionStatus,
)
from apps.incidents.services import (
    IncidentAlreadyResolvedError,
    add_incident_action,
    complete_return,
    initiate_return,
    open_incident,
    resolve_incident,
)

pytestmark = pytest.mark.django_db


def _in_transit_delivery_request():
    return DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT)


# --- open_incident: hold placement is severity-gated ------------------------


def test_open_incident_with_severe_severity_places_delivery_on_hold() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.BROKEN_SEAL,
        severity=IncidentSeverity.SEVERE,
        summary="Seal was broken on arrival at facility.",
        actor=None,
    )

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD
    assert incident.placed_delivery_on_hold is True
    assert incident.delivery_status_before_hold == DeliveryStatus.IN_TRANSIT

    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.event_type == CustodyEventType.INCIDENT_OPENED


def test_open_incident_with_critical_severity_also_places_delivery_on_hold() -> None:
    delivery_request = _in_transit_delivery_request()
    open_incident(
        delivery_request,
        category=IncidentCategory.VEHICLE_ACCIDENT,
        severity=IncidentSeverity.CRITICAL,
        summary="Vehicle accident en route.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD


def test_open_incident_with_minor_severity_does_not_change_delivery_status() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.MISSED_SLA,
        severity=IncidentSeverity.MINOR,
        summary="Running 10 minutes behind.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.IN_TRANSIT
    assert incident.placed_delivery_on_hold is False
    assert incident.delivery_status_before_hold == ""


# --- resolve_incident: required note, status, and transition-back paths -----


def test_resolve_incident_requires_a_non_blank_resolution_note() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.LOST_PACKAGE,
        severity=IncidentSeverity.SEVERE,
        summary="Package cannot be located.",
        actor=None,
    )
    with pytest.raises(ValidationError):
        resolve_incident(
            incident,
            resolution_type=IncidentResolutionType.RESUMED,
            resolution_note="   ",
            actor=None,
        )
    incident.refresh_from_db()
    assert incident.status == IncidentStatus.OPEN


def test_resolve_incident_twice_raises_already_resolved() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.LOST_PACKAGE,
        severity=IncidentSeverity.MINOR,
        summary="Briefly misplaced.",
        actor=None,
    )
    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.OTHER,
        resolution_note="Found it.",
        actor=None,
    )
    with pytest.raises(IncidentAlreadyResolvedError):
        resolve_incident(
            incident,
            resolution_type=IncidentResolutionType.OTHER,
            resolution_note="Again.",
            actor=None,
        )


def test_resolve_incident_resumed_restores_the_pre_hold_status() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.TEMPERATURE_EXCURSION,
        severity=IncidentSeverity.SEVERE,
        summary="Temperature briefly out of range.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD

    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.RESUMED,
        resolution_note="Temperature back in range; continuing delivery.",
        actor=None,
    )

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.IN_TRANSIT
    incident.refresh_from_db()
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.resolved_at is not None
    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.event_type == CustodyEventType.INCIDENT_RESOLVED


def test_resolve_incident_cancelled_transitions_delivery_to_cancelled() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.LEAK_SPILL,
        severity=IncidentSeverity.CRITICAL,
        summary="Container leaked.",
        actor=None,
    )
    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.CANCELLED,
        resolution_note="Delivery cannot be salvaged.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.CANCELLED


def test_resolve_incident_other_leaves_delivery_on_hold_for_a_human_follow_up() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.SUSPECTED_TAMPERING,
        severity=IncidentSeverity.CRITICAL,
        summary="Package appears tampered with.",
        actor=None,
    )
    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.OTHER,
        resolution_note="Escalated to compliance for manual review.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD
    incident.refresh_from_db()
    assert incident.status == IncidentStatus.RESOLVED


# --- The hard, end-to-end DELIVERED-blocking acceptance criterion -----------


def test_open_severe_incident_blocks_delivered_and_resolution_unblocks_it() -> None:
    """The full acceptance-criterion flow: get a delivery to AT_DESTINATION,
    open a severe incident (delivery goes to INCIDENT_HOLD), confirm
    DELIVERED is rejected, resolve the incident (resumes to AT_DESTINATION),
    capture proof of delivery, and confirm DELIVERED now succeeds."""
    delivery_request = DeliveryRequestFactory(status=DeliveryStatus.AT_DESTINATION)

    incident = open_incident(
        delivery_request,
        category=IncidentCategory.INCORRECT_RECIPIENT,
        severity=IncidentSeverity.SEVERE,
        summary="Recipient identity could not be confirmed.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD

    # Even with a ProofOfDelivery already on file, DELIVERED must still be
    # rejected while the incident remains open (and while status is
    # structurally not even AT_DESTINATION anymore).
    ProofOfDelivery.objects.create(delivery_request=delivery_request, typed_signature_name="X")
    with pytest.raises(InvalidTransitionError):  # no INCIDENT_HOLD -> DELIVERED edge exists
        transition_delivery_request(delivery_request, DeliveryStatus.DELIVERED, actor=None)

    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.RESUMED,
        resolution_note="Recipient identity confirmed via callback.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.AT_DESTINATION

    transition_delivery_request(delivery_request, DeliveryStatus.DELIVERED, actor=None)
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.DELIVERED


# --- add_incident_action: append-only ---------------------------------------


def test_add_incident_action_is_append_only() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.COURIER_INJURY_EXPOSURE,
        severity=IncidentSeverity.MODERATE,
        summary="Minor courier injury reported.",
        actor=None,
    )
    action = add_incident_action(
        incident,
        action_type=IncidentActionType.CUSTOMER_NOTIFIED,
        note="Customer informed.",
        actor=None,
    )
    with pytest.raises(ValidationError):
        action.note = "edited"
        action.save()
    with pytest.raises(ValidationError):
        action.delete()


# --- Return-to-sender flow ---------------------------------------------------


def test_resolve_incident_return_to_sender_initiates_return_flow() -> None:
    delivery_request = _in_transit_delivery_request()
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.RECIPIENT_UNAVAILABLE,
        severity=IncidentSeverity.SEVERE,
        summary="Recipient permanently unavailable after repeated attempts.",
        actor=None,
    )
    resolve_incident(
        incident,
        resolution_type=IncidentResolutionType.RETURN_TO_SENDER,
        resolution_note="Recipient unreachable; returning to sender.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.RETURNING

    return_resolution = ReturnResolution.objects.get(delivery_request=delivery_request)
    assert return_resolution.incident_id == incident.pk
    assert return_resolution.status == ReturnResolutionStatus.INITIATED


def test_complete_return_transitions_to_returned() -> None:
    delivery_request = _in_transit_delivery_request()
    return_resolution = initiate_return(
        delivery_request, reason="Wrong destination — returning.", actor=None
    )

    complete_return(return_resolution, actor=None)

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.RETURNED
    return_resolution.refresh_from_db()
    assert return_resolution.status == ReturnResolutionStatus.COMPLETED
    assert return_resolution.completed_at is not None
    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.event_type == CustodyEventType.RETURN_COMPLETED


def test_initiate_return_without_incident_is_allowed() -> None:
    """A return can be initiated directly (incident=None) — see
    apps.incidents.models.ReturnResolution's module docstring."""
    delivery_request = _in_transit_delivery_request()
    return_resolution = initiate_return(delivery_request, reason="Wrong address.", actor=None)
    assert return_resolution.incident is None
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.RETURNING


def test_second_severe_incident_while_already_on_hold_does_not_error() -> None:
    """A second severe incident opened while the delivery is already
    INCIDENT_HOLD must not attempt an illegal INCIDENT_HOLD -> INCIDENT_HOLD
    transition, and should inherit the original pre-hold snapshot."""
    delivery_request = _in_transit_delivery_request()
    first = open_incident(
        delivery_request,
        category=IncidentCategory.TEMPERATURE_EXCURSION,
        severity=IncidentSeverity.SEVERE,
        summary="First excursion.",
        actor=None,
    )
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD

    second = open_incident(
        delivery_request,
        category=IncidentCategory.TEMPERATURE_EXCURSION,
        severity=IncidentSeverity.SEVERE,
        summary="Second excursion, still on hold.",
        actor=None,
    )

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD
    assert second.placed_delivery_on_hold is True
    assert (
        second.delivery_status_before_hold
        == first.delivery_status_before_hold
        == DeliveryStatus.IN_TRANSIT
    )
