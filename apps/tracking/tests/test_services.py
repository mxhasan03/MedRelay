"""Tests for apps.tracking.services — the location-ping recording and the
hard "location stops after terminal state" acceptance criterion.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.dispatch.models import AssignmentStatus
from apps.dispatch.tests.factories import DeliveryAssignmentFactory
from apps.tracking.models import CourierLocationPing
from apps.tracking.services import (
    TERMINAL_DELIVERY_STATUSES,
    LocationPingRejectedError,
    is_terminal,
    record_location_ping,
)

pytestmark = pytest.mark.django_db


def test_record_location_ping_succeeds_for_an_active_non_terminal_assignment() -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)

    ping = record_location_ping(
        assignment,
        courier=assignment.courier,
        latitude=Decimal("40.7128"),
        longitude=Decimal("-74.0060"),
    )

    assert ping.pk is not None
    assert CourierLocationPing.objects.filter(assignment=assignment).count() == 1


def test_record_location_ping_rejected_when_assignment_not_active() -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.REASSIGNED)

    with pytest.raises(LocationPingRejectedError):
        record_location_ping(
            assignment,
            courier=assignment.courier,
            latitude=Decimal("40.7128"),
            longitude=Decimal("-74.0060"),
        )

    assert not CourierLocationPing.objects.filter(assignment=assignment).exists()


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_DELIVERY_STATUSES))
def test_record_location_ping_rejected_once_delivery_reaches_any_terminal_status(
    terminal_status: str,
) -> None:
    """Hard acceptance criterion: location stops after terminal state."""
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    delivery_request = assignment.delivery_request
    delivery_request.status = terminal_status
    delivery_request.save(update_fields=["status"])

    assert is_terminal(assignment) is True
    with pytest.raises(LocationPingRejectedError):
        record_location_ping(
            assignment,
            courier=assignment.courier,
            latitude=Decimal("40.7128"),
            longitude=Decimal("-74.0060"),
        )

    assert not CourierLocationPing.objects.filter(assignment=assignment).exists()


def test_is_terminal_false_for_active_assignment_on_a_non_terminal_delivery() -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    assignment.delivery_request.status = "assigned"
    assignment.delivery_request.save(update_fields=["status"])

    assert is_terminal(assignment) is False
