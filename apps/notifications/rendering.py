"""Renders a short, human-readable subject/body/SMS summary from an
allow-listed notification payload — never from arbitrary caller-supplied
free text, so a rendered message can never smuggle in anything outside
`apps.notifications.payload.ALLOWED_NOTIFICATION_FIELDS`.
"""

from __future__ import annotations

from typing import Any

from apps.notifications.models import NotificationType

_TITLES: dict[str, str] = {
    NotificationType.DELIVERY_STATUS_CHANGED: "Delivery status update",
    NotificationType.JOB_OFFER_AVAILABLE: "New job offer available",
    NotificationType.INCIDENT_OPENED: "Incident opened",
    NotificationType.INCIDENT_RESOLVED: "Incident resolved",
    NotificationType.INVOICE_ISSUED: "Invoice issued",
    NotificationType.RECIPIENT_LINK_ISSUED: "Tracking link issued",
    NotificationType.CREDENTIAL_EXPIRING: "Credential expiring soon",
    NotificationType.EXPORT_READY: "Export ready",
    NotificationType.GENERIC: "MedRelay notification",
}


def render_subject(notification_type: str) -> str:
    return f"[MedRelay Demo] {_TITLES.get(notification_type, 'Notification')}"


def _fields_line(payload: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(payload.items()))


def render_body(notification_type: str, payload: dict[str, Any]) -> str:
    """A short, template-only body built strictly from the allow-listed
    payload's own key/value pairs — never arbitrary free text. This is
    intentionally terse and unstyled (Phase 8 is the real UX/design pass);
    the point here is that it is provably safe, not that it reads well."""
    title = _TITLES.get(notification_type, "MedRelay notification")
    fields = _fields_line(payload)
    return (
        f"{title}\n\n"
        f"Operational reference(s): {fields or '(none)'}\n\n"
        "This is an automated message from the MedRelay demo prototype "
        "(synthetic data only)."
    )


def render_sms_summary(notification_type: str, payload: dict[str, Any]) -> str:
    """A single-line SMS-length summary, same data-minimization guarantee as
    `render_body` — never anything beyond the allow-listed payload."""
    title = _TITLES.get(notification_type, "MedRelay notification")
    fields = _fields_line(payload)
    summary = f"MedRelay: {title}. {fields}".strip()
    return summary[:200]


__all__ = ["render_body", "render_sms_summary", "render_subject"]
