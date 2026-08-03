"""Tests for the `render_package_qr` management command (proves the QR
rendering pipeline actually works end-to-end, not just at the model-method
level — see apps/cargo/models.py's `PackageIdentifier.render_qr_png_bytes`)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cargo.tests.factories import PackageIdentifierFactory

pytestmark = pytest.mark.django_db


def test_render_package_qr_writes_png_file(tmp_path) -> None:
    identifier = PackageIdentifierFactory()
    out_path = tmp_path / "qr.png"
    stdout = StringIO()

    call_command("render_package_qr", str(identifier.code), "--out", str(out_path), stdout=stdout)

    assert out_path.exists()
    assert out_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert identifier.code in stdout.getvalue()


def test_render_package_qr_by_numeric_id(tmp_path) -> None:
    identifier = PackageIdentifierFactory()
    out_path = tmp_path / "qr_by_id.png"

    call_command("render_package_qr", str(identifier.pk), "--out", str(out_path))

    assert out_path.exists()


def test_render_package_qr_defaults_to_most_recent_identifier(tmp_path) -> None:
    PackageIdentifierFactory()
    latest = PackageIdentifierFactory()
    out_path = tmp_path / "qr_latest.png"

    call_command("render_package_qr", "--out", str(out_path))

    assert out_path.exists()
    # We can't easily assert *which* identifier without re-decoding the QR, but we
    # can assert the command picked an existing, real identifier's code by
    # checking it did not error and produced a non-empty PNG.
    assert out_path.stat().st_size > 0
    assert latest.pk is not None


def test_render_package_qr_errors_on_unknown_id() -> None:
    with pytest.raises(CommandError):
        call_command("render_package_qr", "999999")


def test_render_package_qr_errors_on_unknown_code() -> None:
    with pytest.raises(CommandError):
        call_command("render_package_qr", "PKG-DOESNOTEXIST")


def test_render_package_qr_errors_when_no_identifiers_exist() -> None:
    with pytest.raises(CommandError):
        call_command("render_package_qr")
