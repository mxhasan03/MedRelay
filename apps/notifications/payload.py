"""The data-minimization boundary for every notification/SMS/email/webhook log.

Hard acceptance criterion (Phase 7, docs/IMPLEMENTATION_ROADMAP.md): "no
sensitive data in notification logs." Every notification/SMS/email log
record must only ever reference **operational identifiers** — delivery ID,
package barcode/identifier, organization/facility ID, status names — never a
raw sender/recipient contact name+phone+address bundle, never PHI-adjacent
free text from delivery instructions, never a raw PIN or signature payload.

`build_notification_payload` is the single, explicit, testable choke point
every notification-creating call in this app must go through
(`apps.notifications.services.create_notification`/`send_email_notification`/
`send_sms_notification`/`record_webhook_delivery_attempt`) — never construct
a `Notification`/`SmsLogEntry`/`EmailLogEntry`/`WebhookDelivery.payload`
dict by hand elsewhere. It **rejects** (raises), rather than silently
stripping, any field not on the explicit allow-list below: a loud failure
during development is far more useful than a quietly-truncated log record
that looks complete but silently dropped a field a future caller expected to
be there.
"""

from __future__ import annotations

from typing import Any

# Every key a notification/SMS/email/webhook payload may ever carry.
# Deliberately: operational identifiers, status/category enum values, and
# amounts/dates — never a name, phone number, address, free-text
# instructions field, PIN, or signature payload. Extend this list
# deliberately (one reviewed line at a time), never widen it just to make a
# caller's payload pass.
ALLOWED_NOTIFICATION_FIELDS: frozenset[str] = frozenset(
    {
        "delivery_id",
        "package_code",
        "organization_id",
        "facility_id",
        "courier_id",
        "assignment_id",
        "job_offer_id",
        "recommendation_id",
        "incident_id",
        "incident_category",
        "incident_severity",
        "invoice_id",
        "invoice_number",
        "payment_status",
        "amount",
        "status",
        "previous_status",
        "event_type",
        "service_level",
        "credential_type",
        "expires_at",
        "occurred_at",
        "message_code",
        "export_job_id",
        "report_type",
    }
)


class DisallowedNotificationFieldError(Exception):
    """Raised by `build_notification_payload` when a caller supplies a field
    name that is not on `ALLOWED_NOTIFICATION_FIELDS` — e.g. a contact name,
    phone number, address, free-text instructions field, PIN, or signature
    payload. This is the real, testable boundary the "no sensitive data in
    notification logs" acceptance criterion checks."""


def build_notification_payload(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate `fields` against the allow-list and return a clean payload
    dict (`None` values dropped, so a log record only ever carries the
    identifiers that actually apply to it).

    Raises `DisallowedNotificationFieldError` naming every offending key if
    `fields` contains anything not on `ALLOWED_NOTIFICATION_FIELDS` — the
    caller must remove it, never work around this by renaming it onto an
    already-allowed key that means something else.
    """
    disallowed = sorted(set(fields) - ALLOWED_NOTIFICATION_FIELDS)
    if disallowed:
        raise DisallowedNotificationFieldError(
            "The following notification payload field(s) are not on the "
            f"data-minimization allow-list and were rejected: {disallowed}. "
            "Notification/SMS/email/webhook logs may only carry operational "
            "identifiers and status/category values — see "
            "apps.notifications.payload.ALLOWED_NOTIFICATION_FIELDS."
        )
    return {key: value for key, value in fields.items() if value is not None}


__all__ = [
    "ALLOWED_NOTIFICATION_FIELDS",
    "DisallowedNotificationFieldError",
    "build_notification_payload",
]
