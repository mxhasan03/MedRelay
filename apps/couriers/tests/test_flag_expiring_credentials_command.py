"""Tests for the `flag_expiring_credentials` management command.

Per docs/PRODUCT_REQUIREMENTS.md section 6, this is query/flagging logic
only — the command must never send a real notification (that is Phase 7
work). These tests only assert on stdout content.
"""

from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.core.management import call_command

from apps.couriers.models import CourierCredentialStatus, CourierCredentialType
from apps.couriers.tests.factories import CourierCredentialFactory, CourierProfileFactory

pytestmark = pytest.mark.django_db


def test_flags_expired_and_soon_expiring_credentials() -> None:
    today = datetime.date.today()
    courier = CourierProfileFactory()
    expired = CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=5),
    )
    soon = CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.INSURANCE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today + datetime.timedelta(days=10),
    )
    stdout = StringIO()

    call_command("flag_expiring_credentials", "--within-days", "30", stdout=stdout)

    output = stdout.getvalue()
    assert str(expired.courier) in output
    assert str(soon.courier) in output
    assert "Already expired" in output
    assert "Expiring within 30 day(s)" in output


def test_reports_none_when_nothing_expiring() -> None:
    stdout = StringIO()

    call_command("flag_expiring_credentials", stdout=stdout)

    output = stdout.getvalue()
    assert "(none)" in output
