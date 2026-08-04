"""`GET/POST /recipient/<token>/` — the anonymous recipient tracking page.

Hard acceptance criterion (Phase 7): expired recipient links are rejected,
never granting access. This view returns a clean `403` for an expired
token and `404` for every other rejection (bad signature, malformed
token, or a delivery that no longer exists) — see
`apps.recipient.tokens.RecipientLinkInvalidError`'s docstring for why those
are deliberately not distinguished further. `403` is used for "this was a
real link once, but it's timed out" (a demo-honest response — expiry is not
a secret) while `404` is used for "nothing to see here" whenever telling the
caller anything more specific could leak information about token validity.
No view in this module requires a login — this is the one genuinely
anonymous, public-facing surface in this codebase.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from apps.custody.services import PinVerificationError, verify_recipient_pin
from apps.recipient.models import RecipientLinkAccessOutcome
from apps.recipient.services import build_masked_tracking_context, log_access
from apps.recipient.tokens import (
    RecipientLinkExpiredError,
    RecipientLinkInvalidError,
    resolve_recipient_tracking_token,
)


def ratelimited_view(request: HttpRequest, exception: Ratelimited) -> HttpResponse:
    """`settings.RATELIMIT_VIEW` — turns django-ratelimit's raised
    `Ratelimited` (a `PermissionDenied` subclass, which Django would
    otherwise render as a plain 403) into an explicit 429 response, on the
    one genuinely public/anonymous surface in this codebase
    (docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4: "rate limiting for
    public/recipient endpoints"). Used for both the token-resolution GET and
    the PIN-verification POST below.
    """
    return JsonResponse(
        {"detail": "Too many requests. Please wait before trying again."}, status=429
    )


@method_decorator(
    ratelimit(key="ip", rate="30/m", method="GET", block=True), name="get"
)
@method_decorator(
    # Keyed on IP + the token itself: this is the actual PIN-guessing defense
    # (docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 4) — a low, per-token
    # rate limit means an attacker who somehow obtained one valid tracking
    # token still cannot brute-force a 4-6 digit PIN in any useful time
    # window, and a shared-IP false-positive (e.g. an office NAT) cannot
    # lock out every *other* token's legitimate recipient.
    ratelimit(key="ip", rate="10/m", method="POST", block=True),
    name="post",
)
@method_decorator(
    ratelimit(
        key=lambda group, request: request.resolver_match.kwargs.get("token", ""),
        rate="5/m",
        method="POST",
        block=True,
    ),
    name="post",
)
class RecipientTrackingView(View):
    """No `LoginRequiredMixin` here by design — see module docstring."""

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        delivery_request = self._resolve_or_reject(token)
        log_access(delivery_request, RecipientLinkAccessOutcome.VIEWED)
        context = build_masked_tracking_context(delivery_request)
        context["token"] = token
        return render(request, "recipient/tracking.html", context)

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        delivery_request = self._resolve_or_reject(token)
        submitted_pin = request.POST.get("pin", "")
        try:
            verify_recipient_pin(delivery_request, submitted_pin, actor=None)
        except PinVerificationError as exc:
            log_access(delivery_request, RecipientLinkAccessOutcome.PIN_FAILED)
            context = build_masked_tracking_context(delivery_request)
            context["token"] = token
            context["pin_error"] = str(exc)
            return render(request, "recipient/tracking.html", context, status=400)
        log_access(delivery_request, RecipientLinkAccessOutcome.PIN_VERIFIED)
        return redirect(reverse("recipient-tracking", kwargs={"token": token}))

    @staticmethod
    def _resolve_or_reject(token: str) -> Any:
        try:
            return resolve_recipient_tracking_token(token)
        except RecipientLinkExpiredError:
            log_access(None, RecipientLinkAccessOutcome.EXPIRED_TOKEN_REJECTED)
            raise PermissionDenied("This tracking link has expired.") from None
        except RecipientLinkInvalidError:
            log_access(None, RecipientLinkAccessOutcome.INVALID_TOKEN_REJECTED)
            raise Http404("This tracking link is not valid.") from None
