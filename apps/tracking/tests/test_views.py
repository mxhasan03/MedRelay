"""HTTP-level tests for the location-ping endpoint: authorization, the
Idempotency-Key mechanism (retries do not duplicate events), and the
terminal-state rejection (location stops after terminal state).
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.couriers.tests.factories import CourierProfileFactory
from apps.dispatch.models import AssignmentStatus
from apps.dispatch.tests.factories import DeliveryAssignmentFactory
from apps.tracking.models import CourierLocationPing

pytestmark = pytest.mark.django_db


def _ping_url(assignment_id: int) -> str:
    return reverse("location-ping", kwargs={"assignment_id": assignment_id})


def test_location_ping_requires_login(client: Client) -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-1",
    )
    assert response.status_code == 403


def test_location_ping_requires_courier_portal_access(client: Client) -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    non_courier = UserFactory()
    client.force_login(non_courier)

    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-1",
    )

    assert response.status_code == 403


def test_location_ping_rejects_wrong_courier(client: Client) -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    other_courier = CourierProfileFactory()
    client.force_login(other_courier.user)

    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-1",
    )

    assert response.status_code == 403
    assert not CourierLocationPing.objects.filter(assignment=assignment).exists()


def test_location_ping_requires_idempotency_key(client: Client) -> None:
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    client.force_login(assignment.courier.user)

    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert not CourierLocationPing.objects.filter(assignment=assignment).exists()


def test_location_ping_success_creates_a_row() -> None:
    client = Client()
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    client.force_login(assignment.courier.user)

    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00", "accuracy_meters": "5"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-abc",
    )

    assert response.status_code == 201
    assert CourierLocationPing.objects.filter(assignment=assignment).count() == 1


def test_location_ping_same_idempotency_key_twice_does_not_duplicate() -> None:
    """Hard acceptance criterion: reruns/retries do not duplicate events."""
    client = Client()
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    client.force_login(assignment.courier.user)
    body = json.dumps({"latitude": "40.71", "longitude": "-74.00"})

    first = client.post(
        _ping_url(assignment.pk),
        data=body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )
    second = client.post(
        _ping_url(assignment.pk),
        data=body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert CourierLocationPing.objects.filter(assignment=assignment).count() == 1


def test_location_ping_rejected_after_terminal_state_hard_acceptance_criterion() -> None:
    """Hard acceptance criterion: location stops after terminal state — once
    the delivery reaches AT_DESTINATION, further pings for that assignment
    must be rejected (this project's chosen behavior: an explicit HTTP 409,
    not a silent no-op — see apps.tracking.services' module docstring)."""
    client = Client()
    assignment = DeliveryAssignmentFactory(status=AssignmentStatus.ACTIVE)
    assignment.delivery_request.status = "at_destination"
    assignment.delivery_request.save(update_fields=["status"])
    client.force_login(assignment.courier.user)

    response = client.post(
        _ping_url(assignment.pk),
        data=json.dumps({"latitude": "40.71", "longitude": "-74.00"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="key-after-terminal",
    )

    assert response.status_code == 409
    assert not CourierLocationPing.objects.filter(assignment=assignment).exists()
