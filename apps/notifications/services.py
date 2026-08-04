"""The single call site for creating in-app notifications and sending
email/SMS/webhook notification-log rows.

Every function here funnels its payload through
`apps.notifications.payload.build_notification_payload` before it ever
touches a model field — see that module's docstring for the hard "no
sensitive data in notification logs" acceptance criterion this enforces.
Never construct a `Notification`/`EmailLogEntry`/`SmsLogEntry`/
`WebhookDelivery` directly outside this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import IntegrityError

from apps.notifications.models import (
    EmailLogEntry,
    Notification,
    NotificationType,
    SmsLogEntry,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.notifications.payload import build_notification_payload
from apps.notifications.providers import EmailNotificationProvider, SimulatedSmsProvider
from apps.notifications.rendering import render_body, render_sms_summary, render_subject

if TYPE_CHECKING:
    from apps.accounts.models import User


def create_notification(
    *,
    recipient: User,
    notification_type: str,
    fields: dict[str, Any] | None = None,
    dedupe_key: str = "",
) -> Notification:
    """Create (or, if `dedupe_key` repeats, replay) one in-app notification.

    `dedupe_key`, when non-empty, is combined with `(recipient,
    notification_type)` under `Notification`'s real database
    `UniqueConstraint` — a second call with the same three values returns
    the existing row rather than creating a duplicate (the "deduplicate
    notifications" idempotency note, docs/ARCHITECTURE_AND_DATA_MODEL.md
    section 9). Two concurrent callers racing the same key are resolved the
    same way `apps.couriers.idempotency` resolves a concurrent
    Idempotency-Key: whichever `INSERT` wins, both callers end up returning
    that same winning row.
    """
    payload = build_notification_payload(fields or {})
    if dedupe_key:
        existing = Notification.objects.filter(
            recipient=recipient, notification_type=notification_type, dedupe_key=dedupe_key
        ).first()
        if existing is not None:
            return existing
        try:
            return Notification.objects.create(
                recipient=recipient,
                notification_type=notification_type,
                payload=payload,
                dedupe_key=dedupe_key,
            )
        except IntegrityError:
            winner = Notification.objects.filter(
                recipient=recipient, notification_type=notification_type, dedupe_key=dedupe_key
            ).first()
            if winner is not None:
                return winner
            raise
    return Notification.objects.create(
        recipient=recipient, notification_type=notification_type, payload=payload
    )


def send_email_notification(
    *,
    recipient: User,
    notification_type: str,
    fields: dict[str, Any] | None = None,
    notification: Notification | None = None,
) -> EmailLogEntry:
    """Send (or attempt) one email via `EmailNotificationProvider` (real
    local SMTP to Mailpit) and persist an `EmailLogEntry` audit row.

    A missing/blank `recipient.email` is not an exception — it is a real,
    common demo-data condition (not every seeded user has an email set) and
    is recorded as an unsuccessful attempt with a warning, exactly like a
    provider that could not reach a real recipient would be.
    """
    payload = build_notification_payload(fields or {})
    provider = EmailNotificationProvider()
    result = provider.send(
        notification_type=notification_type,
        payload=payload,
        to_email=recipient.email or "",
        subject=render_subject(notification_type),
        body=render_body(notification_type, payload),
    )
    return EmailLogEntry.objects.create(
        notification=notification,
        recipient=recipient,
        to_email=recipient.email or "",
        notification_type=notification_type,
        payload=payload,
        subject=render_subject(notification_type),
        provider_name=result.provider_name,
        mode=result.mode,
        correlation_id=result.correlation_id,
        success=result.success,
        warnings=result.warnings,
    )


def send_sms_notification(
    *,
    notification_type: str,
    fields: dict[str, Any] | None = None,
    recipient: User | None = None,
    recipient_label: str = "",
    notification: Notification | None = None,
) -> SmsLogEntry:
    """Log one **simulated** SMS event via `SimulatedSmsProvider`. Never
    calls a real SMS API — see that provider's docstring."""
    payload = build_notification_payload(fields or {})
    provider = SimulatedSmsProvider()
    result = provider.send(notification_type=notification_type, payload=payload)
    return SmsLogEntry.objects.create(
        notification=notification,
        recipient=recipient,
        recipient_label=recipient_label,
        notification_type=notification_type,
        payload=payload,
        message_summary=render_sms_summary(notification_type, payload),
        provider_name=result.provider_name,
        mode=result.mode,
        correlation_id=result.correlation_id,
        success=result.success,
        warnings=result.warnings,
    )


def record_webhook_delivery_attempt(
    *,
    endpoint: WebhookEndpoint,
    notification_type: str,
    fields: dict[str, Any] | None = None,
) -> WebhookDelivery:
    """Record one **simulated** outbound-webhook-attempt log row.

    This function performs **no network call of any kind** — see
    `apps.notifications.models.WebhookDelivery`'s docstring for the full
    SSRF-avoidance rationale. `endpoint` must already be a persisted,
    organization-registered `WebhookEndpoint` row (never a raw,
    caller-supplied URL accepted at call time), so there is no code path
    here that could be used to make this server fetch an arbitrary
    attacker-supplied address.
    """
    payload = build_notification_payload(fields or {})
    from apps.notifications.providers import new_correlation_id

    return WebhookDelivery.objects.create(
        endpoint=endpoint,
        notification_type=notification_type,
        payload=payload,
        correlation_id=new_correlation_id(),
        simulated=True,
        success=True,
    )


def notify_invoice_issued(*, recipient: User, invoice: Any) -> Notification:
    """Convenience wrapper wiring `apps.billing`'s invoice issuance to an
    in-app notification (+ email) — see docs/CURRENT_STATUS.md "Phase 7"
    "Known gaps" for which lifecycle events this phase does and does not
    wire a notification trigger into."""
    fields = {
        "invoice_id": str(invoice.pk),
        "invoice_number": invoice.invoice_number,
        "organization_id": invoice.organization_id,
        "delivery_id": str(invoice.delivery_request_id),
        "amount": str(invoice.total),
        "payment_status": invoice.payment_status,
    }
    notification = create_notification(
        recipient=recipient,
        notification_type=NotificationType.INVOICE_ISSUED,
        fields=fields,
        dedupe_key=f"invoice:{invoice.pk}",
    )
    send_email_notification(
        recipient=recipient,
        notification_type=NotificationType.INVOICE_ISSUED,
        fields=fields,
        notification=notification,
    )
    return notification


def notify_recipient_link_issued(
    *, recipient: User, delivery_request: Any, expires_at: Any = None
) -> Notification:
    """Convenience wrapper wiring `apps.recipient`'s tracking-link issuance
    to an in-app notification for the organization user who triggered it
    (the anonymous package recipient has no MedRelay account to notify
    in-app — see docs/CURRENT_STATUS.md "Phase 7" for the honest limitation
    that this prototype has no automated out-of-band channel to the
    recipient themselves; the link is relayed the same out-of-band way
    Phase 6's recipient PIN already is)."""
    fields = {
        "delivery_id": str(delivery_request.pk),
        "organization_id": delivery_request.organization_id,
        "status": delivery_request.status,
    }
    if expires_at is not None:
        fields["expires_at"] = expires_at.isoformat()
    return create_notification(
        recipient=recipient,
        notification_type=NotificationType.RECIPIENT_LINK_ISSUED,
        fields=fields,
    )


__all__ = [
    "create_notification",
    "notify_invoice_issued",
    "notify_recipient_link_issued",
    "record_webhook_delivery_attempt",
    "send_email_notification",
    "send_sms_notification",
]
