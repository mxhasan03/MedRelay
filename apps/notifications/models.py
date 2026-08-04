"""In-app notifications, and outbound email/SMS/webhook delivery logs.

See docs/CURRENT_STATUS.md "Phase 7" section for the full design write-up.
Highlights:

- **Data minimization is enforced at creation time, not just by convention.**
  Every model below stores its `payload` only via
  `apps.notifications.payload.build_notification_payload`, which rejects
  (raises) any field outside an explicit allow-list — see that module's
  docstring and `apps/notifications/tests/test_payload.py` for the hard
  acceptance-criterion test. `apps.notifications.services` is the only
  intended call site that constructs these rows; none of the fields below
  accept arbitrary free text.
- **Two real channel implementations, one simulated.** Email
  (`EmailLogEntry`) sends real local SMTP to Mailpit
  (`apps.notifications.providers.EmailNotificationProvider`, `mode=LOCAL`).
  SMS (`SmsLogEntry`) is a **simulated** adapter (`SimulatedSmsProvider`,
  `mode=MOCK`) that never attempts a real network call — every paid SMS
  provider is prohibited per docs/TECH_STACK_AND_ZERO_COST_POLICY.md.
- **`WebhookDelivery` is a demo-only, no-network-call stub** — see its own
  docstring below for why this phase does not build a real arbitrary-URL
  outbound webhook sender (SSRF risk with no real external customer
  integration to justify it yet).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models


class NotificationType(models.TextChoices):
    """Every notification/SMS/email/webhook event this phase's building
    blocks can represent. Not every value has an automatic trigger wired
    into another app's lifecycle yet — see docs/CURRENT_STATUS.md "Phase 7"
    "Known gaps" for exactly which are wired to a real call site today
    (billing invoice issuance, recipient-link issuance) versus available for
    a later phase to wire into delivery-status/incident/job-offer events."""

    DELIVERY_STATUS_CHANGED = "delivery_status_changed", "Delivery Status Changed"
    JOB_OFFER_AVAILABLE = "job_offer_available", "Job Offer Available"
    INCIDENT_OPENED = "incident_opened", "Incident Opened"
    INCIDENT_RESOLVED = "incident_resolved", "Incident Resolved"
    INVOICE_ISSUED = "invoice_issued", "Invoice Issued"
    RECIPIENT_LINK_ISSUED = "recipient_link_issued", "Recipient Tracking Link Issued"
    CREDENTIAL_EXPIRING = "credential_expiring", "Credential Expiring"
    EXPORT_READY = "export_ready", "Export Ready"
    GENERIC = "generic", "Generic"


class NotificationChannel(models.TextChoices):
    IN_APP = "in_app", "In-App"
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    WEBHOOK = "webhook", "Webhook"


class ProviderMode(models.TextChoices):
    """The model-facing twin of `apps.notifications.providers.ProviderMode`
    (a plain string-constants class, not a Django `TextChoices`, since it is
    never itself stored on a model field)."""

    LOCAL = "local", "Local"
    MOCK = "mock", "Mock"


class NotificationQuerySet(models.QuerySet["Notification"]):
    def for_user(self, user: Any) -> NotificationQuerySet:
        return self.filter(recipient=user)

    def unread(self) -> NotificationQuerySet:
        return self.filter(is_read=False)


class Notification(models.Model):
    """One in-app notification for one recipient `User`.

    `payload` is always the output of
    `apps.notifications.payload.build_notification_payload` — see module
    docstring. `dedupe_key`, when set, makes re-raising the identical event
    for the identical recipient a no-op rather than a duplicate row (the
    "deduplicate notifications" idempotency note,
    docs/ARCHITECTURE_AND_DATA_MODEL.md section 9) — see
    `apps.notifications.services.create_notification`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Allow-listed operational identifiers only — see apps.notifications.payload.",
    )
    dedupe_key = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional caller-supplied key; a second create_notification() call with the "
        "same (recipient, notification_type, dedupe_key) returns the existing row instead of "
        "creating a duplicate.",
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "notification_type", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="unique_notification_dedupe_key_per_recipient_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_notification_type_display()} -> {self.recipient} ({self.pk})"

    def mark_read(self) -> None:
        if self.is_read:
            return
        from django.utils import timezone

        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=["is_read", "read_at"])


class EmailLogEntry(models.Model):
    """One outbound email attempt (real local SMTP to Mailpit).

    `to_email` is the recipient `User`'s own account email address (an
    operational actor identifier for a real account holder — organization
    staff, internal ops, or a courier — never a bundled patient/customer
    contact record), stored only because the SMTP envelope inherently needs
    it; `subject`/`body` are always rendered from the allow-listed
    `payload` by `apps.notifications.rendering`, never arbitrary caller text.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(
        Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_logs"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    to_email = models.EmailField(blank=True)
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    payload = models.JSONField(default=dict, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    provider_name = models.CharField(max_length=64)
    mode = models.CharField(max_length=8, choices=ProviderMode.choices)
    correlation_id = models.CharField(max_length=64)
    success = models.BooleanField(default=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Email[{self.notification_type}] -> {self.recipient_id} ({self.created_at})"


class SmsLogEntry(models.Model):
    """One simulated SMS event (docs/PRODUCT_REQUIREMENTS.md section 15).

    Never a raw phone number: `recipient` is a `User` FK (an operational
    account, not a bundled contact record) and `recipient_label` is a short,
    non-identifying role label (e.g. "courier", "requester") for display
    when there is no `User` row to point at (e.g. the recipient of a
    delivery, who has no MedRelay account at all). **No real SMS API is ever
    called** — see `apps.notifications.providers.SimulatedSmsProvider`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(
        Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    recipient_label = models.CharField(
        max_length=64, blank=True, help_text="e.g. 'courier', 'requester' — never a phone number."
    )
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    payload = models.JSONField(default=dict, blank=True)
    message_summary = models.CharField(
        max_length=200,
        blank=True,
        help_text="Short operational text rendered from the allow-listed payload only.",
    )
    provider_name = models.CharField(max_length=64)
    mode = models.CharField(max_length=8, choices=ProviderMode.choices)
    correlation_id = models.CharField(max_length=64)
    success = models.BooleanField(default=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SMS[{self.notification_type}] -> {self.recipient_label or self.recipient_id}"


class WebhookEndpoint(models.Model):
    """An organization-registered webhook target.

    **Demo-only scope decision** (documented in full in
    `WebhookDelivery`'s docstring below): `target_url` is stored as
    documentation of what a real integration would point at, but
    `apps.notifications.services.record_webhook_delivery_attempt` never
    actually issues an HTTP request to it — there is no real external
    customer integration to test against yet, and building a genuine
    arbitrary-URL outbound HTTP sender here would be a textbook SSRF vector
    (a customer-supplied URL that this server would fetch) for zero present
    benefit. See docs/CURRENT_STATUS.md "Phase 7" for the full write-up.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="webhook_endpoints"
    )
    target_url = models.URLField(
        help_text="Documentation only in this phase — never actually called. See WebhookDelivery."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["organization_id", "id"]

    def __str__(self) -> str:
        return f"Webhook endpoint for {self.organization} -> {self.target_url}"


class WebhookDelivery(models.Model):
    """One **simulated** outbound-webhook-attempt log row.

    Per docs/ARCHITECTURE_AND_DATA_MODEL.md's "Commercial and system" entity
    list, `WebhookDelivery` is a named entity this phase should account for.
    The task's own guidance is explicit that a real arbitrary-URL webhook
    sender would be a demo-inappropriate SSRF vector with no real external
    customer integration to justify it — so this model exists (satisfying
    the architecture doc and giving `apps.reporting`/operational-metrics
    something real to read), but `record_webhook_delivery_attempt` in
    `apps.notifications.services` **never performs any network call of any
    kind** (no `requests`/`urllib`/socket usage anywhere in this module). It
    only records what would have been sent, against a `WebhookEndpoint`
    row an organization admin registered — never a raw, client-supplied URL
    at call time — and always reports a synthetic `success=True`
    `simulated` result. This is documented again in
    `apps.notifications.services.record_webhook_delivery_attempt`'s own
    docstring; a later phase, once there is a real external integration to
    support, should replace this with a genuine outbound HTTP sender behind
    an allowlist/egress-control policy, not extend this stub in place.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    payload = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=64)
    simulated = models.BooleanField(
        default=True, help_text="Always True in this phase — no real HTTP request is ever made."
    )
    success = models.BooleanField(default=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-attempted_at"]

    def __str__(self) -> str:
        return f"Webhook delivery (simulated) to {self.endpoint_id}: {self.notification_type}"


__all__ = [
    "EmailLogEntry",
    "Notification",
    "NotificationChannel",
    "NotificationType",
    "ProviderMode",
    "SmsLogEntry",
    "WebhookDelivery",
    "WebhookEndpoint",
]
