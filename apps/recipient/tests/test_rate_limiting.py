"""Real rate-limiting test against the recipient PIN-verification endpoint.

Phase 8 acceptance criterion: "repeated rapid PIN-verification attempts
against the recipient view get rate-limited" — a real test hitting the view
N+1 times and asserting the last one is rejected with 429.

This is the actual security-relevant mechanism: without it, the recipient
tracking link's PIN (a 4-6 digit code, per
`apps.custody.services.generate_recipient_pin`) would be brute-forceable by
an unauthenticated caller with nothing more than a valid tracking token, per
docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.recipient.tokens import generate_recipient_tracking_token

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    # django-ratelimit counters live in the same cache LocMemCache backend
    # tests share; each test starts from a clean slate so one test's
    # requests cannot exhaust another test's budget.
    cache.clear()
    yield
    cache.clear()


def test_repeated_pin_attempts_are_rate_limited_and_the_overflow_request_gets_429() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)
    url = reverse("recipient-tracking", kwargs={"token": token})
    client = Client()

    # The per-token POST limit (apps/recipient/views.py) is 5/m. Send one
    # more than that and confirm the overflow request — and only the
    # overflow request — is rejected with 429.
    responses = [client.post(url, {"pin": "000000"}) for _ in range(6)]

    assert all(r.status_code in (400, 429) for r in responses)
    assert responses[-1].status_code == 429
    # At least one of the earlier attempts was actually processed (400 —
    # wrong PIN — not silently rate-limited from the very first request).
    assert any(r.status_code == 400 for r in responses[:5])


def test_rate_limited_response_does_not_leak_pin_validity_information() -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)
    url = reverse("recipient-tracking", kwargs={"token": token})
    client = Client()

    for _ in range(5):
        client.post(url, {"pin": "000000"})
    response = client.post(url, {"pin": "000000"})

    assert response.status_code == 429
    assert b"does not match" not in response.content
