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
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.custody.services import PinVerificationError, verify_recipient_pin
from apps.recipient.models import RecipientLinkAccessOutcome
from apps.recipient.services import build_masked_tracking_context, log_access
from apps.recipient.tokens import (
    RecipientLinkExpiredError,
    RecipientLinkInvalidError,
    resolve_recipient_tracking_token,
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
