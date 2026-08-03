"""Integration tests for the Phase 5 PWA shell (manifest + service worker
routes) and the courier base template's registration script.

Honesty note (docs/CURRENT_STATUS.md "Phase 5" has the full write-up): these
tests verify what can genuinely be verified at the Django view/response
level — correct routes, correct content-type, correct content, and that the
HTML includes the service-worker registration `<script>`. They do **not**
prove a real browser actually registers the worker, populates its cache, or
serves anything from that cache while offline — that would require real
browser automation. See docs/CURRENT_STATUS.md for exactly what (if
anything) Playwright/real browser automation was able to verify in this
environment beyond this level.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_manifest_route_returns_valid_manifest_json() -> None:
    client = Client()
    response = client.get(reverse("web-manifest"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    payload = json.loads(response.content)
    assert payload["name"] == "MedRelay Courier (Demo Prototype)"
    assert payload["start_url"] == "/couriers/"
    assert payload["display"] == "standalone"
    assert len(payload["icons"]) >= 1


def test_service_worker_route_returns_javascript_with_cache_first_logic() -> None:
    client = Client()
    response = client.get(reverse("service-worker"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/javascript"
    content = response.content.decode()
    assert 'self.addEventListener("install"' in content
    assert 'self.addEventListener("fetch"' in content
    assert "caches.open" in content


def test_courier_base_template_registers_the_service_worker(client: Client) -> None:
    """A real courier-portal page (any view extending templates/couriers/base.html)
    must include the registration <script> — proven here by rendering the
    real courier home page, not a synthetic template fixture."""
    from apps.couriers.tests.factories import CourierProfileFactory

    courier = CourierProfileFactory()
    client.force_login(courier.user)

    response = client.get(reverse("courier-home"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "serviceWorker" in content
    assert "navigator.serviceWorker.register" in content
    assert reverse("service-worker") in content
    assert reverse("web-manifest") in content
    assert 'name="csrf-token"' in content
    assert 'name="viewport"' in content  # mobile-first: inherited from templates/base.html
