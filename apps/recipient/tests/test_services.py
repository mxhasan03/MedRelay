"""Tests for apps.recipient.services: the masked tracking context and link
issuance (+ its in-app notification hook)."""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.custody.services import generate_recipient_pin, record_event
from apps.deliveries.models import RecipientVerificationMethod
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.dispatch.tests.factories import DeliveryAssignmentFactory
from apps.notifications.models import Notification, NotificationType
from apps.recipient.services import build_masked_tracking_context, issue_recipient_link

pytestmark = pytest.mark.django_db


def test_masked_context_has_no_pin_required_when_method_is_not_pin() -> None:
    delivery_request = DeliveryRequestFactory(
        recipient_verification_method=RecipientVerificationMethod.NONE
    )
    context = build_masked_tracking_context(delivery_request)
    assert context["pin_required"] is False
    assert context["pin_verified"] is False


def test_masked_context_requires_pin_until_verified() -> None:
    delivery_request = DeliveryRequestFactory(
        recipient_verification_method=RecipientVerificationMethod.PIN
    )
    generate_recipient_pin(delivery_request)

    context = build_masked_tracking_context(delivery_request)
    assert context["pin_required"] is True
    assert context["pin_verified"] is False


def test_masked_context_shows_assigned_courier_label_generically() -> None:
    assignment = DeliveryAssignmentFactory()
    context = build_masked_tracking_context(assignment.delivery_request)
    assert context["courier_label"] == "Your assigned courier"
    # Never the courier's real name.
    assert str(assignment.courier) not in context["courier_label"]


def test_masked_context_timeline_carries_event_types_not_payloads() -> None:
    delivery_request = DeliveryRequestFactory()
    record_event(
        delivery_request,
        "request_created",
        actor_type="system",
        payload={"internal_detail": "should never surface here"},
    )

    context = build_masked_tracking_context(delivery_request)
    assert len(context["timeline"]) == 1
    entry = context["timeline"][0]
    assert set(entry) == {"event_type", "occurred_at"}


def test_issue_recipient_link_returns_a_resolvable_token() -> None:
    from apps.recipient.tokens import resolve_recipient_tracking_token

    delivery_request = DeliveryRequestFactory()
    token = issue_recipient_link(delivery_request)

    resolved = resolve_recipient_tracking_token(token)
    assert resolved.pk == delivery_request.pk


def test_issue_recipient_link_notifies_the_issuing_user() -> None:
    delivery_request = DeliveryRequestFactory()
    issuer = UserFactory()

    issue_recipient_link(delivery_request, issued_by=issuer)

    notification = Notification.objects.get(recipient=issuer)
    assert notification.notification_type == NotificationType.RECIPIENT_LINK_ISSUED
    assert notification.payload["delivery_id"] == str(delivery_request.pk)
