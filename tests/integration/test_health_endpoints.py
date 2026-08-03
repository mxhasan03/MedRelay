"""Integration smoke tests for the liveness/readiness endpoints."""

from django.test import Client, TestCase


class HealthEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_healthz_returns_200(self) -> None:
        response = self.client.get("/healthz/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readyz_returns_200_when_db_and_cache_reachable(self) -> None:
        response = self.client.get("/readyz/")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["checks"]["database"] == "ok"
        assert payload["checks"]["cache"] == "ok"
