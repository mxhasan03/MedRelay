"""Courier-facing PWA views: job offers (accept/decline), the active
delivery (pickup/transit status advancement, package scan, status
timeline).

Mobile-first, server-rendered Django + HTMX/Alpine-flavored plain templates
per docs/CURRENT_STATUS.md "Phase 5" — matching the plain-template
convention every prior phase's CRUD UI already used (a real design system is
still Phase 8's job), but genuinely mobile-first this time since this is
explicitly the courier's phone-based tool.

Every state-mutating endpoint here (`JobOfferAcceptView`, `JobOfferDeclineView`,
`DeliveryStatusAdvanceView`, `PackageScanView`) is JSON-in/JSON-out and
requires an `Idempotency-Key` (header or body/form field) — see
`apps.couriers.idempotency.idempotent_call`. This is a deliberate choice:
these endpoints are meant to be driven by `fetch()` from the courier's
browser (including retries replayed from the offline event queue,
`static/js/offline-queue.js`, once connectivity returns), not plain
full-page form navigation — the same reasoning that makes a real camera-
based QR scan or `navigator.geolocation.watchPosition` JS-only in any
browser. The manual package-code-entry *fallback* is still a genuinely
plain HTML `<input>` field, always present and always functional even
without JS having decoded a camera image — only the network transport
(fetch vs. full-page POST) is JS-required, not the fallback input method
itself.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import User
from apps.cargo.services import PackageScanError, confirm_package_scan
from apps.couriers.idempotency import idempotent_call
from apps.couriers.services import (
    COURIER_ADVANCE_SEQUENCE,
    advance_delivery_status,
    can_access_courier_portal,
)
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryRequest
from apps.dispatch.exceptions import (
    AssignmentConflictError,
    IneligibleCourierError,
    JobOfferOwnershipError,
)
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment, JobOffer, JobOfferStatus
from apps.dispatch.services import accept_job_offer, decline_job_offer


def _actor(request: HttpRequest) -> User:
    """Narrow `request.user` to the concrete `User` model — safe here because
    every view in this module is behind `CourierPermissionMixin`
    (`LoginRequiredMixin` + `can_access_courier_portal`), matching
    `apps.deliveries.views._actor`'s/`apps.dispatch.views._actor`'s exact
    pattern."""
    user = request.user
    assert isinstance(user, User)
    return user


def _idempotency_key(request: HttpRequest, payload: dict[str, Any] | None = None) -> str:
    """Extract the client-generated Idempotency-Key from the request header
    (the primary path, used by every JS `fetch()` call in this app), a JSON
    body field, or a plain form field — in that order."""
    header_key = request.headers.get("Idempotency-Key")
    if header_key:
        return header_key
    if payload and payload.get("idempotency_key"):
        return str(payload["idempotency_key"])
    return request.POST.get("idempotency_key", "")


def _json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body or b"{}")
        return body if isinstance(body, dict) else {}
    except json.JSONDecodeError:
        return {}


class CourierPermissionMixin(LoginRequiredMixin):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        if request.user.is_authenticated and not can_access_courier_portal(request.user):
            raise PermissionDenied("Courier portal access required.")
        return super().dispatch(request, *args, **kwargs)


class CourierHomeView(CourierPermissionMixin, TemplateView):
    template_name = "couriers/home.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        courier = _actor(self.request).courier_profile
        context["open_offer_count"] = JobOffer.objects.filter(
            courier=courier, status=JobOfferStatus.OFFERED
        ).count()
        context["active_assignments"] = list(
            DeliveryAssignment.objects.filter(
                courier=courier, status=AssignmentStatus.ACTIVE
            ).select_related("delivery_request")
        )
        return context


class JobOfferListView(CourierPermissionMixin, TemplateView):
    template_name = "couriers/job_offer_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        courier = _actor(self.request).courier_profile
        context["offers"] = (
            JobOffer.objects.filter(courier=courier, status=JobOfferStatus.OFFERED)
            .select_related("delivery_request")
            .prefetch_related("delivery_request__stops__facility")
        )
        return context


class JobOfferAcceptView(CourierPermissionMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        courier = _actor(request).courier_profile
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        def _do() -> dict[str, Any]:
            assignment = accept_job_offer(pk, courier.pk, actor=_actor(request))
            return {
                "assignment_id": assignment.pk,
                "delivery_request_id": str(assignment.delivery_request_id),
            }

        try:
            data, status_code = idempotent_call(
                courier=courier,
                endpoint="job_offer_accept",
                key=key,
                fn=_do,
                status_code=201,
            )
        except JobOfferOwnershipError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except (AssignmentConflictError, IneligibleCourierError) as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        return JsonResponse(data, status=status_code)


class JobOfferDeclineView(CourierPermissionMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        courier = _actor(request).courier_profile
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        reason = payload.get("reason") or request.POST.get("reason", "")
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        def _do() -> dict[str, Any]:
            offer = decline_job_offer(pk, courier.pk, reason=reason)
            return {"offer_id": offer.pk, "status": offer.status}

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="job_offer_decline", key=key, fn=_do
            )
        except JobOfferOwnershipError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except AssignmentConflictError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        return JsonResponse(data, status=status_code)


class ActiveDeliveryView(CourierPermissionMixin, DetailView):
    model = DeliveryRequest
    template_name = "couriers/active_delivery.html"
    context_object_name = "delivery_request"

    def get_object(self, queryset: Any = None) -> DeliveryRequest:
        delivery_request = get_object_or_404(
            DeliveryRequest.objects.select_related("organization").prefetch_related(
                "stops__facility", "packages__identifier", "status_transitions"
            ),
            pk=self.kwargs["pk"],
        )
        courier = _actor(self.request).courier_profile
        active_assignment = (
            DeliveryAssignment.objects.select_related("courier")
            .filter(delivery_request=delivery_request, status=AssignmentStatus.ACTIVE)
            .first()
        )
        if active_assignment is None or active_assignment.courier_id != courier.pk:
            raise PermissionDenied("This delivery is not currently assigned to you.")
        self.active_assignment = active_assignment
        return delivery_request

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        delivery_request = self.object
        context["active_assignment"] = self.active_assignment
        context["next_status"] = COURIER_ADVANCE_SEQUENCE.get(delivery_request.status)
        context["transitions"] = delivery_request.status_transitions.all()
        context["packages"] = delivery_request.packages.all()
        return context


class DeliveryStatusAdvanceView(CourierPermissionMixin, View):
    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        to_status = payload.get("to_status") or request.POST.get("to_status", "")
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)
        if not to_status:
            return JsonResponse({"error": "to_status is required."}, status=400)

        def _do() -> dict[str, Any]:
            delivery_request = advance_delivery_status(
                pk, courier, to_status, actor=_actor(request)
            )
            return {"status": delivery_request.status}

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="delivery_status_advance", key=key, fn=_do
            )
        except PermissionError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except InvalidTransitionError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        return JsonResponse(data, status=status_code)


class PackageScanView(CourierPermissionMixin, View):
    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        code = payload.get("code") or request.POST.get("code", "")
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        delivery_request = get_object_or_404(DeliveryRequest, pk=pk)
        active_assignment = DeliveryAssignment.objects.filter(
            delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
        ).first()
        if active_assignment is None or active_assignment.courier_id != courier.pk:
            raise PermissionDenied("This delivery is not currently assigned to you.")

        def _do() -> dict[str, Any]:
            package = confirm_package_scan(delivery_request, code, actor=_actor(request))
            assert package.scanned_at is not None
            return {
                "package_id": package.pk,
                "sequence_number": package.sequence_number,
                "scanned_at": package.scanned_at.isoformat(),
            }

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="package_scan", key=key, fn=_do
            )
        except PackageScanError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        return JsonResponse(data, status=status_code)
