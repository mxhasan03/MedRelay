"""Tests for the `audit_cost` zero-cost policy management command."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_audit_cost_passes_on_clean_repo() -> None:
    out = StringIO()
    call_command("audit_cost", stdout=out)
    assert "passed" in out.getvalue().lower()


def test_audit_cost_writes_cost_audit_doc() -> None:
    from pathlib import Path

    from django.conf import settings as django_settings

    call_command("audit_cost", stdout=StringIO())
    report_path = Path(django_settings.BASE_DIR) / "docs" / "COST_AUDIT.md"
    assert report_path.exists()
    content = report_path.read_text()
    assert "Result: PASS" in content
    assert "does NOT mean a real operating courier business would cost" in content


def test_audit_cost_fails_closed_on_disallowed_package(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.audit.management.commands import audit_cost

    monkeypatch.setattr(audit_cost, "ALLOWED_PACKAGES", set())
    with pytest.raises(CommandError):
        call_command("audit_cost", stdout=StringIO(), stderr=StringIO())
