"""Model-level tests for Notification dedup and mark_read."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification, NotificationType

pytestmark = pytest.mark.django_db


def test_mark_read_sets_is_read_and_read_at() -> None:
    user = UserFactory()
    notification = Notification.objects.create(
        recipient=user, notification_type=NotificationType.GENERIC, payload={}
    )
    assert notification.is_read is False
    assert notification.read_at is None

    notification.mark_read()

    notification.refresh_from_db()
    assert notification.is_read is True
    assert notification.read_at is not None


def test_mark_read_is_a_no_op_when_already_read() -> None:
    user = UserFactory()
    notification = Notification.objects.create(
        recipient=user, notification_type=NotificationType.GENERIC, payload={}
    )
    notification.mark_read()
    first_read_at = notification.read_at

    notification.mark_read()

    assert notification.read_at == first_read_at


def test_dedupe_key_is_unique_per_recipient_and_type() -> None:
    user = UserFactory()
    Notification.objects.create(
        recipient=user,
        notification_type=NotificationType.INVOICE_ISSUED,
        payload={},
        dedupe_key="invoice:1",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.create(
            recipient=user,
            notification_type=NotificationType.INVOICE_ISSUED,
            payload={},
            dedupe_key="invoice:1",
        )


def test_blank_dedupe_key_does_not_collide() -> None:
    """Two notifications with a blank dedupe_key for the same recipient/type
    must NOT be treated as duplicates — only a non-empty dedupe_key
    participates in the unique constraint (see the model's Meta.constraints
    `~models.Q(dedupe_key="")` condition)."""
    user = UserFactory()
    Notification.objects.create(
        recipient=user, notification_type=NotificationType.GENERIC, payload={}
    )
    Notification.objects.create(
        recipient=user, notification_type=NotificationType.GENERIC, payload={}
    )
    assert Notification.objects.filter(recipient=user).count() == 2
