"""TOTP MFA for privileged demo accounts (Phase 8).

Per docs/TECH_STACK_AND_ZERO_COST_POLICY.md ("django-otp for optional TOTP
MFA") and docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4 ("TOTP MFA for
privileged roles when enabled"). Scope decision
(`apps.organizations.services.is_mfa_eligible`): internal-ops staff and
customer-org owners/administrators may *enroll*; nobody is forced to. Once a
user has a confirmed `django_otp.plugins.otp_totp.models.TOTPDevice`,
however, login for that specific account genuinely requires a valid code —
this is not cosmetic.

## Login flow

Django's stock `LoginView.form_valid()` calls `auth_login()` immediately on
password success, which fully establishes the session. That is too early
for MFA: a second factor must be checked *before* the session is considered
authenticated. `MedRelayLoginView` below overrides `form_valid()` to:

1. On successful username/password check, if the user has **no** confirmed
   TOTP device: log in immediately (`auth_login`), exactly like the stock
   view — MFA was never enrolled, so nothing more is required.
2. If the user **does** have a confirmed device: do *not* call
   `auth_login()` yet. Stash the user's primary key in
   `request.session[PENDING_MFA_SESSION_KEY]` and redirect to
   `mfa-verify` instead.

`MfaVerifyView` reads that pending user ID, checks the submitted code
against every confirmed device for that user
(`django_otp.plugins.otp_totp.models.TOTPDevice.verify_token`, which
includes django-otp's own built-in throttling on repeated failures via
`ThrottlingMixin` — a real brute-force mitigation, not something this
module adds itself), and only then calls `auth_login()` (establishing the
session) followed by `django_otp.login()` (marking the session
OTP-verified, so `request.user.is_verified()` is meaningful for any future
code that wants to check it).

No enrolled user can reach `LOGIN_REDIRECT_URL` without passing this check —
if `request.session[PENDING_MFA_SESSION_KEY]` is never resolved, the
request never gets a real authenticated session, and Django's
`LoginRequiredMixin`/`login_required` on every other view in this codebase
continues to reject it.
"""

from __future__ import annotations

from typing import Any

import segno
from django import forms
from django.contrib.auth import login as auth_login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.organizations.services import AnyUser, is_mfa_eligible

PENDING_MFA_SESSION_KEY = "mfa_pending_user_id"

# Device "name" used for the single TOTP device this prototype supports per
# user — a real product might let a user register several named devices;
# one is enough to prove the mechanism here.
DEVICE_NAME = "default"


def _confirmed_devices(user: AnyUser) -> Any:
    if isinstance(user, AnonymousUser):
        return TOTPDevice.objects.none()
    return TOTPDevice.objects.filter(user=user, confirmed=True)


def user_has_confirmed_totp_device(user: AnyUser) -> bool:
    return _confirmed_devices(user).exists()


class TOTPCodeForm(forms.Form):
    code = forms.CharField(
        label="6-digit authentication code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}),
    )


class MedRelayLoginView(LoginView):
    """Drop-in replacement for `django.contrib.auth.views.LoginView` (see
    `config/urls.py`) that defers full login for any user with a confirmed
    TOTP device — see module docstring."""

    template_name = "registration/login.html"

    def form_valid(self, form: Any) -> HttpResponse:
        user = form.get_user()
        if user_has_confirmed_totp_device(user):
            self.request.session[PENDING_MFA_SESSION_KEY] = user.pk
            return redirect(reverse("mfa-verify"))
        auth_login(self.request, user)
        return redirect(self.get_success_url())


class MfaVerifyView(View):
    """Second-factor step. Deliberately *not* `LoginRequiredMixin` — the
    whole point is that the user is not fully logged in yet (see module
    docstring); access is instead gated on
    `request.session[PENDING_MFA_SESSION_KEY]` being present."""

    template_name = "registration/mfa_verify.html"

    def _pending_user(self, request: HttpRequest) -> User | None:
        user_id = request.session.get(PENDING_MFA_SESSION_KEY)
        if user_id is None:
            return None
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def get(self, request: HttpRequest) -> HttpResponse:
        if self._pending_user(request) is None:
            return redirect(reverse("login"))
        return render(request, self.template_name, {"form": TOTPCodeForm()})

    def post(self, request: HttpRequest) -> HttpResponse:
        user = self._pending_user(request)
        if user is None:
            return redirect(reverse("login"))

        form = TOTPCodeForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)

        code = form.cleaned_data["code"]
        matched_device = None
        for device in _confirmed_devices(user):
            if device.verify_token(code):
                matched_device = device
                break

        if matched_device is None:
            form.add_error("code", "That code is incorrect or expired. Please try again.")
            return render(request, self.template_name, {"form": form}, status=400)

        del request.session[PENDING_MFA_SESSION_KEY]
        # `auth_login()` requires `user.backend` to be set (normally done by
        # `authenticate()`); this `User` instance was instead re-fetched by
        # primary key from the pending-session ID, so it must be set
        # explicitly. `User`/`AbstractBaseUser` has no such attribute in
        # django-stubs (it is set dynamically by Django itself), hence the
        # targeted ignore rather than a broader suppression.
        user.backend = "django.contrib.auth.backends.ModelBackend"  # type: ignore[attr-defined]
        auth_login(request, user)
        otp_login(request, matched_device)
        return redirect(reverse("organization-list"))


class MfaEnrollView(LoginRequiredMixin, View):  # type: ignore[misc]
    """Enrollment: a privileged user (`is_mfa_eligible`) generates a TOTP
    secret, scans the QR code (rendered with `segno` — already an approved
    zero-cost dependency for QR generation, per
    docs/TECH_STACK_AND_ZERO_COST_POLICY.md — rather than adding a new
    `qrcode` dependency) with any standard authenticator app, and confirms
    enrollment by submitting one real generated code.
    """

    template_name = "registration/mfa_enroll.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.user.is_authenticated and not is_mfa_eligible(request.user):
            raise PermissionDenied(
                "MFA enrollment is only available to internal operations staff and "
                "organization owners/administrators in this prototype."
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> HttpResponse:
        if user_has_confirmed_totp_device(request.user):
            return render(request, self.template_name, {"already_enrolled": True})

        device, _created = TOTPDevice.objects.get_or_create(
            user=request.user, name=DEVICE_NAME, confirmed=False
        )
        qr_svg = segno.make(device.config_url).svg_data_uri(scale=4)
        return render(
            request,
            self.template_name,
            {
                "already_enrolled": False,
                "qr_svg": qr_svg,
                "secret_key": device.key,
                "form": TOTPCodeForm(),
            },
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        if user_has_confirmed_totp_device(request.user):
            return render(request, self.template_name, {"already_enrolled": True})

        device = TOTPDevice.objects.filter(
            user=request.user, name=DEVICE_NAME, confirmed=False
        ).first()
        if device is None:
            return redirect(reverse("mfa-enroll"))

        form = TOTPCodeForm(request.POST)
        if not form.is_valid() or not device.verify_token(form.cleaned_data["code"]):
            if form.is_valid():
                form.add_error("code", "That code is incorrect or expired. Please try again.")
            qr_svg = segno.make(device.config_url).svg_data_uri(scale=4)
            return render(
                request,
                self.template_name,
                {
                    "already_enrolled": False,
                    "qr_svg": qr_svg,
                    "secret_key": device.key,
                    "form": form,
                },
                status=400,
            )

        device.confirmed = True
        device.save(update_fields=["confirmed"])
        return render(
            request, self.template_name, {"already_enrolled": True, "just_enrolled": True}
        )


__all__ = [
    "DEVICE_NAME",
    "PENDING_MFA_SESSION_KEY",
    "MedRelayLoginView",
    "MfaEnrollView",
    "MfaVerifyView",
    "TOTPCodeForm",
    "user_has_confirmed_totp_device",
]
