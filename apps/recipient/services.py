"""Building the masked, recipient-facing tracking context, and issuing links.

Per docs/PRODUCT_REQUIREMENTS.md section 8 ("masked communication
placeholder") and docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4 ("never
expose courier/customer personal contact data unnecessarily"): the recipient
page never shows a courier's real name/phone, a sender/recipient contact
name/phone, or raw `CustodyEvent.payload`/`device_metadata` — only the
delivery's status, a coarse ETA, and the *type* + timestamp of each custody
event (never its payload).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apps.dispatch.models import AssignmentStatus
from apps.recipient.models import RecipientLinkAccessLog, RecipientLinkAccessOutcome

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.deliveries.models import DeliveryRequest


def log_access(delivery_request: Any, outcome: str) -> RecipientLinkAccessLog:
    return RecipientLinkAccessLog.objects.create(delivery_request=delivery_request, outcome=outcome)


def build_masked_tracking_context(delivery_request: DeliveryRequest) -> dict[str, Any]:
    """Everything the recipient tracking page needs, with nothing
    identity-adjacent in it."""
    active_assignment = delivery_request.assignments.filter(status=AssignmentStatus.ACTIVE).first()
    if active_assignment is not None:
        courier_label = "Your assigned courier"
    else:
        courier_label = "A courier will be assigned soon"

    from apps.deliveries.models import RecipientVerificationMethod

    recipient_verification = getattr(delivery_request, "recipient_verification", None)
    pin_required = (
        delivery_request.recipient_verification_method == RecipientVerificationMethod.PIN
        and recipient_verification is not None
        and not recipient_verification.is_verified
    )

    timeline = [
        {"event_type": event.get_event_type_display(), "occurred_at": event.occurred_at}
        for event in delivery_request.custody_events.order_by("sequence")
    ]

    return {
        "delivery_id": delivery_request.pk,
        "status_display": delivery_request.get_status_display(),
        "required_delivery_by": delivery_request.required_delivery_by,
        "courier_label": courier_label,
        "pin_required": pin_required,
        "pin_verified": bool(recipient_verification and recipient_verification.is_verified),
        "timeline": timeline,
    }


def issue_recipient_link(
    delivery_request: DeliveryRequest, *, issued_by: User | None = None
) -> str:
    """Generate a fresh signed token for `delivery_request` and, when
    `issued_by` is given (an authorized customer-org/internal-ops user
    triggering issuance from the delivery detail page), notify them in-app —
    see `apps.notifications.services.notify_recipient_link_issued`. The
    plaintext link itself, exactly like Phase 6's recipient PIN, is relayed
    to the actual package recipient out of band (there is still no
    automated recipient-facing channel in this prototype)."""
    import datetime

    from django.utils import timezone

    from apps.recipient.tokens import (
        RECIPIENT_LINK_MAX_AGE_SECONDS,
        generate_recipient_tracking_token,
    )

    token = generate_recipient_tracking_token(delivery_request)
    if issued_by is not None:
        from apps.notifications.services import notify_recipient_link_issued

        expires_at = timezone.now() + datetime.timedelta(seconds=RECIPIENT_LINK_MAX_AGE_SECONDS)
        notify_recipient_link_issued(
            recipient=issued_by, delivery_request=delivery_request, expires_at=expires_at
        )
    return token


__all__ = [
    "RecipientLinkAccessOutcome",
    "build_masked_tracking_context",
    "issue_recipient_link",
    "log_access",
]
