"""Real TOTP MFA tests — a genuine `django_otp` TOTPDevice and a genuine
generated code (via `django_otp.oath.totp`, the same primitive
`TOTPDevice.verify_token` itself uses), never a hardcoded stub.

Covers the Phase 8 acceptance criterion: "a privileged user can enroll a
TOTP device, and a subsequent login genuinely requires the TOTP code."
"""

from __future__ import annotations

import pytest
from django.contrib.auth import SESSION_KEY
from django.test import Client
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.mfa import DEVICE_NAME, PENDING_MFA_SESSION_KEY
from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import CustomerRole
from apps.organizations.tests.factories import OrganizationMembershipFactory

pytestmark = pytest.mark.django_db

DEMO_PASSWORD = "MfaPhase8Test!2026"  # pragma: allowlist secret


def _current_code(device: TOTPDevice) -> str:
    return str(totp(device.bin_key)).zfill(device.digits)


def _set_password(user) -> None:
    user.set_password(DEMO_PASSWORD)
    user.save()


def test_ordinary_customer_org_user_without_a_managing_role_cannot_enroll() -> None:
    membership = OrganizationMembershipFactory(
        user=UserFactory(), role=CustomerRole.REQUESTER_DISPATCHER
    )
    client = Client()
    _set_password(membership.user)
    client.login(username=membership.user.username, password=DEMO_PASSWORD)

    response = client.get(reverse("mfa-enroll"))
    assert response.status_code == 403


def test_org_owner_can_enroll_and_confirm_with_a_real_generated_code() -> None:
    membership = OrganizationMembershipFactory(user=UserFactory(), role=CustomerRole.OWNER)
    client = Client()
    _set_password(membership.user)
    client.login(username=membership.user.username, password=DEMO_PASSWORD)

    get_response = client.get(reverse("mfa-enroll"))
    assert get_response.status_code == 200
    assert get_response.context["already_enrolled"] is False

    device = TOTPDevice.objects.get(user=membership.user, name=DEVICE_NAME, confirmed=False)
    code = _current_code(device)

    post_response = client.post(reverse("mfa-enroll"), {"code": code})
    assert post_response.status_code == 200
    assert post_response.context["just_enrolled"] is True

    device.refresh_from_db()
    assert device.confirmed is True


def test_internal_staff_can_enroll() -> None:
    user = UserFactory()
    InternalRoleAssignment.objects.create(user=user, role=InternalRole.DISPATCHER)
    client = Client()
    _set_password(user)
    client.login(username=user.username, password=DEMO_PASSWORD)

    response = client.get(reverse("mfa-enroll"))
    assert response.status_code == 200


def test_enrollment_rejects_an_incorrect_code() -> None:
    membership = OrganizationMembershipFactory(user=UserFactory(), role=CustomerRole.OWNER)
    client = Client()
    _set_password(membership.user)
    client.login(username=membership.user.username, password=DEMO_PASSWORD)
    client.get(reverse("mfa-enroll"))

    response = client.post(reverse("mfa-enroll"), {"code": "000000"})

    assert response.status_code == 400
    device = TOTPDevice.objects.get(user=membership.user, name=DEVICE_NAME)
    assert device.confirmed is False


def test_login_without_an_enrolled_device_does_not_require_mfa() -> None:
    user = UserFactory()
    _set_password(user)
    client = Client()

    response = client.post(reverse("login"), {"username": user.username, "password": DEMO_PASSWORD})

    assert response.status_code == 302
    assert response["Location"] != reverse("mfa-verify")
    assert SESSION_KEY in client.session


def test_login_with_an_enrolled_device_defers_the_session_until_verify() -> None:
    user = UserFactory()
    _set_password(user)
    TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=True)

    client = Client()
    login_response = client.post(
        reverse("login"), {"username": user.username, "password": DEMO_PASSWORD}
    )

    # Password was correct, but the session must NOT be authenticated yet.
    assert login_response.status_code == 302
    assert login_response["Location"] == reverse("mfa-verify")
    assert SESSION_KEY not in client.session
    assert client.session[PENDING_MFA_SESSION_KEY] == user.pk

    # Any page requiring login must still bounce to the login flow.
    protected_response = client.get(reverse("organization-list"))
    assert protected_response.status_code == 302


def test_mfa_verify_rejects_a_wrong_code_without_completing_login() -> None:
    user = UserFactory()
    _set_password(user)
    TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=True)
    client = Client()
    client.post(reverse("login"), {"username": user.username, "password": DEMO_PASSWORD})

    wrong_response = client.post(reverse("mfa-verify"), {"code": "000000"})

    assert wrong_response.status_code == 400
    assert SESSION_KEY not in client.session


def test_mfa_verify_accepts_the_real_generated_code_and_completes_login() -> None:
    # A fresh device/session per test (rather than chaining a wrong attempt
    # immediately before this one) deliberately avoids colliding with
    # django-otp's own real, empirically-observed throttling
    # (`TOTPDevice`'s `ThrottlingMixin` — a failed attempt imposes a short
    # cooldown before the *next* attempt, correct or not, is even
    # evaluated) — see docs/CURRENT_STATUS.md "Phase 8" for the write-up.
    user = UserFactory()
    _set_password(user)
    device = TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=True)
    client = Client()
    client.post(reverse("login"), {"username": user.username, "password": DEMO_PASSWORD})

    correct_response = client.post(reverse("mfa-verify"), {"code": _current_code(device)})

    assert correct_response.status_code == 302
    assert correct_response["Location"] == reverse("organization-list")
    assert SESSION_KEY in client.session
    assert PENDING_MFA_SESSION_KEY not in client.session

    # The now-fully-authenticated session can reach a protected page.
    final_response = client.get(reverse("organization-list"))
    assert final_response.status_code == 200


def test_mfa_verify_with_no_pending_session_redirects_to_login() -> None:
    client = Client()
    response = client.get(reverse("mfa-verify"))
    assert response.status_code == 302
    assert response["Location"] == reverse("login")
