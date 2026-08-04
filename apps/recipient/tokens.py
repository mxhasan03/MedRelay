"""Short-lived, signed, anonymous recipient tracking tokens.

Per docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4 ("short-lived signed
recipient tokens") and docs/PRODUCT_REQUIREMENTS.md section 8 ("short-lived
tracking link"): a token that lets an anonymous recipient (no MedRelay
account, no login) reach a read-only tracking page and the PIN-confirmation
flow for exactly one delivery, for a limited time.

Why a new app (`apps.recipient`), not an extension of `apps.tracking`:
`apps.tracking` (Phase 5) is the *authenticated courier's* browser
periodically POSTing its own GPS location — a high-frequency, session-
authenticated, courier-owned write path. This is the opposite shape: a
low-frequency, unauthenticated, time-boxed *read* (plus one PIN-confirmation
write) by a party with no MedRelay account at all. Folding an anonymous
public surface into the same app as an authenticated internal one would
blur a security-relevant boundary (which endpoints require login, which
don't) for no code-reuse benefit — the two apps share no models or service
functions. Documented here rather than silently deciding.

Mechanism: `django.core.signing.TimestampSigner` (stdlib/Django — no new
dependency). The signed value is the delivery request's UUID; `max_age` is
enforced by `TimestampSigner.unsign` itself, not by any custom/hand-rolled
timestamp comparison. `RECIPIENT_LINK_MAX_AGE_SECONDS` is deliberately
generous (72 hours) for a demo, since a real deployment would tune this
against how long a delivery is realistically in flight — see
docs/CURRENT_STATUS.md "Phase 7" for the write-up.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

if TYPE_CHECKING:
    from apps.deliveries.models import DeliveryRequest

RECIPIENT_LINK_SALT = "apps.recipient.tracking-link.v1"

# 72 hours: generous enough to cover a scheduled delivery's full window in
# this demo, while still being a genuinely time-limited ("short-lived") link
# rather than a permanent one. Admin-tunable only via a code change in this
# phase — no per-delivery override exists yet (see "Known gaps").
RECIPIENT_LINK_MAX_AGE_SECONDS = 60 * 60 * 72


class RecipientLinkError(Exception):
    """Base class for a rejected recipient tracking token."""


class RecipientLinkExpiredError(RecipientLinkError):
    """The token's signature is valid but it is older than `max_age`."""


class RecipientLinkInvalidError(RecipientLinkError):
    """The token is malformed, has a bad signature, or does not resolve to
    an existing delivery request. Deliberately one error type for all three
    causes — see `apps.recipient.views` for why: a public endpoint must not
    let a caller distinguish "this token was never real" from "this
    delivery no longer exists" from "this signature was tampered with",
    each of which would leak information a probing attacker could use."""


def generate_recipient_tracking_token(delivery_request: DeliveryRequest) -> str:
    """Sign a fresh token for `delivery_request`. Stateless — no database
    row is required to validate it later (though issuance is logged; see
    `apps.recipient.models.RecipientLinkAccessLog` and
    `apps.recipient.services.issue_recipient_link`)."""
    signer = TimestampSigner(salt=RECIPIENT_LINK_SALT)
    return signer.sign(str(delivery_request.pk))


def resolve_recipient_tracking_token(
    token: str, *, max_age_seconds: int = RECIPIENT_LINK_MAX_AGE_SECONDS
) -> DeliveryRequest:
    """Validate `token` and return its `DeliveryRequest`.

    Raises `RecipientLinkExpiredError` if the signature is valid but older
    than `max_age_seconds`, or `RecipientLinkInvalidError` for every other
    failure (bad/missing signature, malformed UUID, or no matching delivery
    request) — see that exception's docstring for why these are not
    further distinguished.
    """
    from apps.deliveries.models import DeliveryRequest

    signer = TimestampSigner(salt=RECIPIENT_LINK_SALT)
    try:
        value = signer.unsign(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise RecipientLinkExpiredError("This tracking link has expired.") from exc
    except BadSignature as exc:
        raise RecipientLinkInvalidError("This tracking link is not valid.") from exc

    try:
        delivery_id = uuid.UUID(value)
    except ValueError as exc:
        raise RecipientLinkInvalidError("This tracking link is not valid.") from exc

    try:
        return DeliveryRequest.objects.get(pk=delivery_id)
    except DeliveryRequest.DoesNotExist as exc:
        raise RecipientLinkInvalidError("This tracking link is not valid.") from exc


__all__ = [
    "RECIPIENT_LINK_MAX_AGE_SECONDS",
    "RecipientLinkError",
    "RecipientLinkExpiredError",
    "RecipientLinkInvalidError",
    "generate_recipient_tracking_token",
    "resolve_recipient_tracking_token",
]
