"""HTTP-level tests for the anonymous recipient tracking page, including
the hard "expired recipient links rejected" acceptance criterion exercised
through a real request, not just the token module directly."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.custody.services import generate_recipient_pin
from apps.deliveries.models import RecipientVerificationMethod
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.recipient.models import RecipientLinkAccessLog, RecipientLinkAccessOutcome
from apps.recipient.tokens import RECIPIENT_LINK_MAX_AGE_SECONDS, generate_recipient_tracking_token

pytestmark = pytest.mark.django_db


def test_get_with_a_fresh_token_returns_200_and_masked_status() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)

    client = Client()
    response = client.get(reverse("recipient-tracking", kwargs={"token": token}))

    assert response.status_code == 200
    assert response.context["courier_label"] in (
        "Your assigned courier",
        "A courier will be assigned soon",
    )
    # Never expose a real name/phone anywhere in the rendered response.
    assert b"555-" not in response.content


def test_get_with_an_expired_token_is_rejected_with_403_never_granting_access() -> None:
    delivery_request = DeliveryRequestFactory()
    past_epoch = time.time() - (RECIPIENT_LINK_MAX_AGE_SECONDS + 60)
    with patch("django.core.signing.time.time", return_value=past_epoch):
        token = generate_recipient_tracking_token(delivery_request)

    client = Client()
    response = client.get(reverse("recipient-tracking", kwargs={"token": token}))

    assert response.status_code == 403
    assert (
        RecipientLinkAccessLog.objects.filter(
            outcome=RecipientLinkAccessOutcome.EXPIRED_TOKEN_REJECTED
        ).count()
        == 1
    )


def test_get_with_a_malformed_token_is_rejected_with_404() -> None:
    client = Client()
    response = client.get(reverse("recipient-tracking", kwargs={"token": "not-a-real-token"}))
    assert response.status_code == 404


def test_post_with_correct_pin_verifies_recipient() -> None:
    delivery_request = DeliveryRequestFactory(
        recipient_verification_method=RecipientVerificationMethod.PIN
    )
    _, plaintext_pin = generate_recipient_pin(delivery_request)
    token = generate_recipient_tracking_token(delivery_request)

    client = Client()
    response = client.post(
        reverse("recipient-tracking", kwargs={"token": token}), {"pin": plaintext_pin}
    )

    assert response.status_code == 302
    delivery_request.refresh_from_db()
    assert delivery_request.recipient_verification.is_verified is True


def test_post_with_wrong_pin_does_not_verify_and_returns_400() -> None:
    delivery_request = DeliveryRequestFactory(
        recipient_verification_method=RecipientVerificationMethod.PIN
    )
    generate_recipient_pin(delivery_request)
    token = generate_recipient_tracking_token(delivery_request)

    client = Client()
    response = client.post(reverse("recipient-tracking", kwargs={"token": token}), {"pin": "0000"})

    assert response.status_code == 400
    assert delivery_request.recipient_verification.is_verified is False


def test_post_with_an_expired_token_is_rejected_never_verifying_the_pin() -> None:
    delivery_request = DeliveryRequestFactory(
        recipient_verification_method=RecipientVerificationMethod.PIN
    )
    _, plaintext_pin = generate_recipient_pin(delivery_request)
    past_epoch = time.time() - (RECIPIENT_LINK_MAX_AGE_SECONDS + 60)
    with patch("django.core.signing.time.time", return_value=past_epoch):
        token = generate_recipient_tracking_token(delivery_request)

    client = Client()
    response = client.post(
        reverse("recipient-tracking", kwargs={"token": token}), {"pin": plaintext_pin}
    )

    assert response.status_code == 403
    delivery_request.refresh_from_db()
    assert delivery_request.recipient_verification.is_verified is False
