"""Hard acceptance criterion: expired recipient links are rejected.

Uses a genuine time-based expiry check, not a hardcoded fake: the token is
signed with `django.core.signing`'s real `TimestampSigner`, and the clock
`TimestampSigner.timestamp()` reads (`django.core.signing.time.time`) is
patched to a point in the past at *signing* time only — `resolve_...` is
then called with the real, un-patched clock, so the `max_age` check inside
Django's own `TimestampSigner.unsign` is what actually raises. This is the
same standard technique used to test any `TimestampSigner`-based expiry
without a third-party time-travel dependency.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import patch

import pytest

from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.recipient.tokens import (
    RECIPIENT_LINK_MAX_AGE_SECONDS,
    RecipientLinkExpiredError,
    RecipientLinkInvalidError,
    generate_recipient_tracking_token,
    resolve_recipient_tracking_token,
)

pytestmark = pytest.mark.django_db


def test_a_fresh_token_resolves_to_the_correct_delivery_request() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)

    resolved = resolve_recipient_tracking_token(token)

    assert resolved.pk == delivery_request.pk


def test_an_expired_token_is_rejected_via_real_clock_manipulation() -> None:
    """Sign the token as if it were issued `max_age + 1` seconds ago (by
    patching the real clock `TimestampSigner` reads at signing time only),
    then resolve it with the real, current clock. The real `max_age` check
    inside Django's `TimestampSigner.unsign` must reject it."""
    delivery_request = DeliveryRequestFactory()
    past_epoch = time.time() - (RECIPIENT_LINK_MAX_AGE_SECONDS + 60)

    with patch("django.core.signing.time.time", return_value=past_epoch):
        token = generate_recipient_tracking_token(delivery_request)

    # Real, unpatched current time from here on.
    with pytest.raises(RecipientLinkExpiredError):
        resolve_recipient_tracking_token(token)


def test_a_token_issued_just_inside_the_window_still_resolves() -> None:
    """Guards against an off-by-one that would reject everything —
    a token issued `max_age - 60` seconds ago must still be accepted."""
    delivery_request = DeliveryRequestFactory()
    recent_past_epoch = time.time() - (RECIPIENT_LINK_MAX_AGE_SECONDS - 60)

    with patch("django.core.signing.time.time", return_value=recent_past_epoch):
        token = generate_recipient_tracking_token(delivery_request)

    resolved = resolve_recipient_tracking_token(token)
    assert resolved.pk == delivery_request.pk


def test_a_tampered_token_is_rejected_as_invalid_not_expired() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    with pytest.raises(RecipientLinkInvalidError):
        resolve_recipient_tracking_token(tampered)


def test_a_well_formed_token_for_a_deleted_delivery_is_rejected_as_invalid() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)
    delivery_request.delete()

    with pytest.raises(RecipientLinkInvalidError):
        resolve_recipient_tracking_token(token)


def test_a_token_for_a_nonexistent_delivery_uuid_is_rejected_as_invalid() -> None:
    from django.core.signing import TimestampSigner

    from apps.recipient.tokens import RECIPIENT_LINK_SALT

    fake_token = TimestampSigner(salt=RECIPIENT_LINK_SALT).sign(str(uuid.uuid4()))
    with pytest.raises(RecipientLinkInvalidError):
        resolve_recipient_tracking_token(fake_token)
