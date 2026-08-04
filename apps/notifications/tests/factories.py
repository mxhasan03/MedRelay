"""factory_boy factories for notifications-app tests."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification, NotificationType


class NotificationFactory(DjangoModelFactory):
    class Meta:
        model = Notification

    recipient = factory.SubFactory(UserFactory)
    notification_type = NotificationType.GENERIC
    payload = factory.LazyFunction(dict)
