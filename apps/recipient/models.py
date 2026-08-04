"""An audit log of recipient tracking-link access attempts.

Deliberately minimal: no IP address, user agent, or any other
client-identifying data is stored (data minimization —
docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 2 — and there is no present
need for it in this prototype). Just enough to see, per delivery, how the
link was used: viewed, expired-token rejection, invalid-token rejection,
PIN verified, PIN attempt failed.
"""

from __future__ import annotations

import uuid

from django.db import models


class RecipientLinkAccessOutcome(models.TextChoices):
    VIEWED = "viewed", "Viewed"
    EXPIRED_TOKEN_REJECTED = "expired_token_rejected", "Expired Token Rejected"
    INVALID_TOKEN_REJECTED = "invalid_token_rejected", "Invalid Token Rejected"
    PIN_VERIFIED = "pin_verified", "PIN Verified"
    PIN_FAILED = "pin_failed", "PIN Attempt Failed"


class RecipientLinkAccessLog(models.Model):
    """One access attempt against a recipient tracking link.

    `delivery_request` is nullable because an expired/invalid token may not
    resolve to a real delivery at all (or may resolve to one this row
    deliberately does not link to, for `INVALID_TOKEN_REJECTED` caused by a
    malformed/tampered token rather than a genuinely-expired real one) — see
    `apps.recipient.views` for exactly which case sets it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="recipient_link_accesses",
    )
    outcome = models.CharField(max_length=32, choices=RecipientLinkAccessOutcome.choices)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.get_outcome_display()} @ {self.occurred_at} ({self.delivery_request_id})"
