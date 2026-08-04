"""HTTP-level tests for the in-app notification inbox — proves a user only
ever sees their own notifications, never another user's."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification, NotificationType

pytestmark = pytest.mark.django_db


def test_inbox_requires_login() -> None:
    client = Client()
    response = client.get(reverse("notification-inbox"))
    assert response.status_code == 302


def test_inbox_shows_only_the_logged_in_users_own_notifications() -> None:
    owner = UserFactory(username="owner")
    other = UserFactory(username="other")

    Notification.objects.create(
        recipient=owner,
        notification_type=NotificationType.GENERIC,
        payload={"delivery_id": "mine"},
    )
    Notification.objects.create(
        recipient=other,
        notification_type=NotificationType.GENERIC,
        payload={"delivery_id": "not-mine"},
    )

    client = Client()
    client.force_login(owner)
    response = client.get(reverse("notification-inbox"))

    assert response.status_code == 200
    notifications = list(response.context["notifications"])
    assert len(notifications) == 1
    assert notifications[0].payload["delivery_id"] == "mine"


def test_mark_read_only_affects_the_requesting_users_own_notification() -> None:
    owner = UserFactory(username="owner2")
    other = UserFactory(username="other2")
    mine = Notification.objects.create(
        recipient=owner, notification_type=NotificationType.GENERIC, payload={}
    )
    theirs = Notification.objects.create(
        recipient=other, notification_type=NotificationType.GENERIC, payload={}
    )

    client = Client()
    client.force_login(owner)
    client.post(reverse("notification-mark-read", kwargs={"pk": mine.pk}))

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.is_read is True
    assert theirs.is_read is False


def test_mark_read_on_someone_elses_notification_is_404() -> None:
    owner = UserFactory(username="owner3")
    other = UserFactory(username="other3")
    theirs = Notification.objects.create(
        recipient=other, notification_type=NotificationType.GENERIC, payload={}
    )

    client = Client()
    client.force_login(owner)
    response = client.post(reverse("notification-mark-read", kwargs={"pk": theirs.pk}))

    assert response.status_code == 404
