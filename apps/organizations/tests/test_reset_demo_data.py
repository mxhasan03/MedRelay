"""Tests for the `reset_demo_data` management command (Phase 9 quota/abuse safeguard)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.billing.models import Invoice
from apps.deliveries.models import DeliveryRequest
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_reset_demo_data_requires_confirmation_without_yes_flag(monkeypatch) -> None:
    call_command("seed_demo_data", stdout=StringIO())
    call_command("seed_full_demo", stdout=StringIO())

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    out = StringIO()
    call_command("reset_demo_data", stdout=out)

    assert "Aborted" in out.getvalue()
    # Nothing was touched.
    assert Organization.objects.count() == 3
    assert DeliveryRequest.objects.count() == 5


def test_reset_demo_data_yes_flag_wipes_and_reseeds() -> None:
    call_command("seed_demo_data", stdout=StringIO())
    call_command("seed_full_demo", stdout=StringIO())
    assert DeliveryRequest.objects.count() == 5
    assert Invoice.objects.count() == 1
    original_org_ids = set(Organization.objects.values_list("pk", flat=True))

    call_command("reset_demo_data", "--yes", stdout=StringIO())

    # A fresh, deterministic dataset — same shape, brand-new rows.
    assert Organization.objects.count() == 3
    assert DeliveryRequest.objects.count() == 5
    assert Invoice.objects.count() == 1
    new_org_ids = set(Organization.objects.values_list("pk", flat=True))
    assert original_org_ids.isdisjoint(new_org_ids)


def test_reset_demo_data_never_touches_a_non_demo_superuser() -> None:
    call_command("seed_demo_data", stdout=StringIO())
    call_command("seed_full_demo", stdout=StringIO())
    superuser = User.objects.create_superuser(
        username="real_operator",
        email="operator@example.com",
        password="not-a-demo-password",  # pragma: allowlist secret
    )

    call_command("reset_demo_data", "--yes", stdout=StringIO())

    assert User.objects.filter(pk=superuser.pk).exists()
