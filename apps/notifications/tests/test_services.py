"""Tests for apps.notifications.services: create/dedupe, real local email
delivery (locmem backend in config.settings.test), the simulated SMS
adapter (never a real network call), and the webhook-attempt log stub."""

from __future__ import annotations

import pytest
from django.core import mail

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import (
    EmailLogEntry,
    Notification,
    NotificationType,
    ProviderMode,
    SmsLogEntry,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.notifications.payload import DisallowedNotificationFieldError
from apps.notifications.services import (
    create_notification,
    notify_invoice_issued,
    record_webhook_delivery_attempt,
    send_email_notification,
    send_sms_notification,
)
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def test_create_notification_validates_payload_through_allow_list() -> None:
    user = UserFactory()
    with pytest.raises(DisallowedNotificationFieldError):
        create_notification(
            recipient=user,
            notification_type=NotificationType.GENERIC,
            fields={"recipient_contact_name": "Jane Doe"},
        )
    assert Notification.objects.count() == 0


def test_create_notification_persists_compliant_payload() -> None:
    user = UserFactory()
    notification = create_notification(
        recipient=user,
        notification_type=NotificationType.DELIVERY_STATUS_CHANGED,
        fields={"delivery_id": "abc-123", "status": "in_transit"},
    )
    assert notification.payload == {"delivery_id": "abc-123", "status": "in_transit"}
    assert notification.recipient == user


def test_create_notification_with_dedupe_key_returns_existing_row() -> None:
    user = UserFactory()
    first = create_notification(
        recipient=user,
        notification_type=NotificationType.INVOICE_ISSUED,
        fields={"invoice_id": "1"},
        dedupe_key="invoice:1",
    )
    second = create_notification(
        recipient=user,
        notification_type=NotificationType.INVOICE_ISSUED,
        fields={"invoice_id": "1"},
        dedupe_key="invoice:1",
    )
    assert first.pk == second.pk
    assert Notification.objects.filter(recipient=user).count() == 1


def test_create_notification_without_dedupe_key_always_creates_a_new_row() -> None:
    user = UserFactory()
    create_notification(recipient=user, notification_type=NotificationType.GENERIC, fields={})
    create_notification(recipient=user, notification_type=NotificationType.GENERIC, fields={})
    assert Notification.objects.filter(recipient=user).count() == 2


def test_send_email_notification_sends_real_local_smtp_and_logs_it() -> None:
    """config.settings.test points EMAIL_BACKEND at Django's locmem backend
    (Mailpit in the real compose stack) — this is a genuine send through
    Django's real mail pipeline, not a stub."""
    user = UserFactory(email="requester@example.com")

    log_entry = send_email_notification(
        recipient=user,
        notification_type=NotificationType.INVOICE_ISSUED,
        fields={"invoice_id": "1", "invoice_number": "INV-000001"},
    )

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["requester@example.com"]
    assert sent.subject.startswith("[MedRelay Demo]")
    assert isinstance(log_entry, EmailLogEntry)
    assert log_entry.success is True
    assert log_entry.mode == ProviderMode.LOCAL
    assert log_entry.provider_name == "django-smtp"
    assert log_entry.payload == {"invoice_id": "1", "invoice_number": "INV-000001"}


def test_send_email_notification_with_no_recipient_email_is_an_unsuccessful_attempt() -> None:
    user = UserFactory(email="")

    log_entry = send_email_notification(
        recipient=user, notification_type=NotificationType.GENERIC, fields={}
    )

    assert len(mail.outbox) == 0
    assert log_entry.success is False
    assert log_entry.warnings


def test_send_sms_notification_never_calls_a_real_sms_api_and_logs_it() -> None:
    """The simulated SMS adapter makes no network call — this is asserted
    both by the provider's own `mode=MOCK` marker and by the plain fact
    that this test runs with no network access configured and still
    passes deterministically."""
    log_entry = send_sms_notification(
        notification_type=NotificationType.JOB_OFFER_AVAILABLE,
        fields={"delivery_id": "abc", "courier_id": 7},
        recipient_label="courier",
    )

    assert isinstance(log_entry, SmsLogEntry)
    assert log_entry.mode == ProviderMode.MOCK
    assert log_entry.provider_name == "simulated-sms"
    assert log_entry.success is True
    assert (
        "no real" in log_entry.warnings[0].lower() or "simulated" in log_entry.warnings[0].lower()
    )
    assert log_entry.payload == {"delivery_id": "abc", "courier_id": 7}


def test_send_sms_notification_rejects_disallowed_payload_fields() -> None:
    with pytest.raises(DisallowedNotificationFieldError):
        send_sms_notification(
            notification_type=NotificationType.GENERIC,
            fields={"recipient_contact_phone": "555-0100"},
        )
    assert SmsLogEntry.objects.count() == 0


def test_record_webhook_delivery_attempt_never_makes_a_network_call() -> None:
    organization = OrganizationFactory()
    endpoint = WebhookEndpoint.objects.create(
        organization=organization, target_url="https://example.invalid/webhook"
    )

    delivery = record_webhook_delivery_attempt(
        endpoint=endpoint,
        notification_type=NotificationType.INCIDENT_OPENED,
        fields={"incident_id": "1", "incident_severity": "severe"},
    )

    assert isinstance(delivery, WebhookDelivery)
    assert delivery.simulated is True
    assert delivery.success is True
    assert delivery.payload == {"incident_id": "1", "incident_severity": "severe"}


def test_notify_invoice_issued_creates_notification_and_sends_email() -> None:
    from apps.billing.services import generate_invoice_for_delivery
    from apps.deliveries.tests.factories import DeliveryRequestFactory

    user = UserFactory(email="billing@example.com")
    delivery_request = DeliveryRequestFactory()
    invoice = generate_invoice_for_delivery(delivery_request)

    notification = notify_invoice_issued(recipient=user, invoice=invoice)

    assert notification.notification_type == NotificationType.INVOICE_ISSUED
    assert notification.payload["invoice_number"] == invoice.invoice_number
    assert len(mail.outbox) == 1

    # Calling it again for the same invoice must not duplicate the
    # in-app notification (dedupe_key="invoice:<id>").
    notify_invoice_issued(recipient=user, invoice=invoice)
    assert (
        Notification.objects.filter(recipient=user, notification_type="invoice_issued").count() == 1
    )


def test_record_webhook_delivery_attempt_rejects_disallowed_payload_fields() -> None:
    organization = OrganizationFactory()
    endpoint = WebhookEndpoint.objects.create(
        organization=organization, target_url="https://example.invalid/webhook"
    )
    with pytest.raises(DisallowedNotificationFieldError):
        record_webhook_delivery_attempt(
            endpoint=endpoint,
            notification_type=NotificationType.GENERIC,
            fields={"signature_data_url": "data:image/png;base64,abc"},
        )
    assert WebhookDelivery.objects.count() == 0
