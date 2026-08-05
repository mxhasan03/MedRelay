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
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import User
from apps.cargo.models import Package
from apps.cargo.services import PackageScanError, confirm_package_scan, record_condition_check
from apps.couriers.idempotency import idempotent_call
from apps.couriers.services import (
    COURIER_ADVANCE_SEQUENCE,
    advance_delivery_status,
    can_access_courier_portal,
    cargo_handling_boundary_text,
    credential_expiration_summary,
    delivery_timeline_steps,
    update_courier_availability,
)
from apps.custody.services import (
    PinVerificationError,
    ProofAlreadyCapturedError,
    capture_proof_of_delivery,
    capture_proof_of_pickup,
    verify_recipient_pin,
)
from apps.custody.validators import SignatureTooLargeError
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryRequest, RecipientVerificationMethod
from apps.deliveries.state_machine import transition_delivery_request
from apps.dispatch.exceptions import (
    AssignmentConflictError,
    IneligibleCourierError,
    JobOfferOwnershipError,
)
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment, JobOffer, JobOfferStatus
from apps.dispatch.services import accept_job_offer, decline_job_offer
from apps.incidents.services import open_incident


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


class CourierAvailabilityView(CourierPermissionMixin, TemplateView):
    """`GET /couriers/availability/` — the logged-in courier's own
    online/offline, shift window, current service zone, and configured
    capacity (docs/PRODUCT_REQUIREMENTS.md section 6 "Availability"), read
    from `apps.couriers.models.CourierAvailability`. Update happens via
    `CourierAvailabilityUpdateView` below."""

    template_name = "couriers/availability.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from apps.couriers.models import CourierAvailability
        from apps.facilities.models import ServiceZone

        context = super().get_context_data(**kwargs)
        courier = _actor(self.request).courier_profile
        availability, _ = CourierAvailability.objects.get_or_create(courier=courier)
        context["availability"] = availability
        context["service_zones"] = ServiceZone.objects.filter(is_active=True)
        return context


class CourierAvailabilityUpdateView(CourierPermissionMixin, View):
    """`POST /couriers/availability/update/` — update the logged-in courier's
    OWN `CourierAvailability` only (the courier is always derived from
    `request.user.courier_profile`, never from a client-supplied id, so a
    courier can never target another courier's row). JSON in/out,
    Idempotency-Key protected per this app's established pattern, submitted
    via `MedRelayCourier.submitAction` from `couriers/availability.html`."""

    def post(self, request: HttpRequest) -> HttpResponse:
        courier = _actor(request).courier_profile
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        def _do() -> dict[str, Any]:
            availability = update_courier_availability(
                courier,
                is_online=bool(payload.get("is_online", False)),
                current_service_zone_id=payload.get("current_service_zone_id") or None,
                shift_start=payload.get("shift_start") or None,
                shift_end=payload.get("shift_end") or None,
                max_concurrent_deliveries=payload.get("max_concurrent_deliveries"),
            )
            return {
                "is_online": availability.is_online,
                "current_service_zone_id": availability.current_service_zone_id,
                "shift_start": (
                    availability.shift_start.isoformat() if availability.shift_start else None
                ),
                "shift_end": (
                    availability.shift_end.isoformat() if availability.shift_end else None
                ),
                "max_concurrent_deliveries": availability.max_concurrent_deliveries,
            }

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="availability_update", key=key, fn=_do
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            return JsonResponse({"error": message}, status=400)
        return JsonResponse(data, status=status_code)


class CourierProfileView(CourierPermissionMixin, TemplateView):
    """`GET /couriers/profile/` — read-only onboarding/profile screen: identity
    review/driver-license/insurance status, vehicles, equipment, cargo
    authorizations, training records, and credential expiration warnings
    (docs/PRODUCT_REQUIREMENTS.md section 6 "Onboarding profile"). No
    submission/upload path exists here or anywhere in this app — real
    credential documents are never stored, per
    docs/SECURITY_COMPLIANCE_BOUNDARIES.md."""

    template_name = "couriers/profile.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        courier = _actor(self.request).courier_profile
        context["courier"] = courier
        context["vehicles"] = courier.vehicles.filter(is_active=True)
        context["equipment"] = courier.equipment.filter(is_active=True)
        context["cargo_authorizations"] = courier.cargo_authorizations.filter(
            is_active=True
        ).select_related("cargo_class")
        context["training_records"] = courier.training_records.all()
        context["credential_summary"] = credential_expiration_summary(courier=courier)
        context["credentials"] = courier.credentials.all()
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
                "stops__facility", "packages__identifier"
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
        from apps.deliveries.models import DeliveryStatus
        from apps.incidents.models import IncidentCategory, IncidentSeverity

        context = super().get_context_data(**kwargs)
        delivery_request = self.object
        context["active_assignment"] = self.active_assignment
        context["next_status"] = COURIER_ADVANCE_SEQUENCE.get(delivery_request.status)
        context["packages"] = delivery_request.packages.all()
        # Phase 6: proof/condition/incident capture context.
        context["has_proof_of_pickup"] = hasattr(delivery_request, "proof_of_pickup")
        context["has_proof_of_delivery"] = hasattr(delivery_request, "proof_of_delivery")
        context["at_destination"] = delivery_request.status == DeliveryStatus.AT_DESTINATION
        context["recipient_verification_method"] = delivery_request.recipient_verification_method
        context["incident_categories"] = IncidentCategory.choices
        context["incident_severities"] = IncidentSeverity.choices
        context["cargo_handling_boundary"] = cargo_handling_boundary_text(delivery_request)
        context["timeline_steps"] = delivery_timeline_steps(delivery_request)
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


def _assigned_delivery_or_403(pk: Any, courier: Any) -> DeliveryRequest:
    """Fetch `pk`'s `DeliveryRequest` and confirm `courier` currently holds its
    ACTIVE assignment — the same ownership check every Phase 6 courier proof/
    condition/incident endpoint below needs, factored out of `PackageScanView`'s
    inline version (Phase 5) since four more views now need it too."""
    delivery_request = get_object_or_404(DeliveryRequest, pk=pk)
    active_assignment = DeliveryAssignment.objects.filter(
        delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
    ).first()
    if active_assignment is None or active_assignment.courier_id != courier.pk:
        raise PermissionDenied("This delivery is not currently assigned to you.")
    return delivery_request


class CapturePickupProofView(CourierPermissionMixin, View):
    """Phase 6: sender hand-off proof capture at pickup (signature/typed-name
    only — see apps.custody.models module docstring for why no PIN here)."""

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        delivery_request = _assigned_delivery_or_403(pk, courier)
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        def _do() -> dict[str, Any]:
            proof = capture_proof_of_pickup(
                delivery_request,
                actor=_actor(request),
                sender_name=payload.get("sender_name", ""),
                sender_role=payload.get("sender_role", ""),
                signature_data_url=payload.get("signature_data_url", ""),
                typed_signature_name=payload.get("typed_signature_name", ""),
            )
            return {"proof_of_pickup_id": proof.pk}

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="pickup_proof_capture", key=key, fn=_do, status_code=201
            )
        except ProofAlreadyCapturedError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except SignatureTooLargeError as exc:
            return JsonResponse({"error": str(exc)}, status=413)
        return JsonResponse(data, status=status_code)


class CaptureConditionCheckView(CourierPermissionMixin, View):
    """Phase 6: package condition/seal checklist, at pickup or delivery."""

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        delivery_request = _assigned_delivery_or_403(pk, courier)
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)
        package = get_object_or_404(
            Package,
            pk=payload.get("package_id") or request.POST.get("package_id"),
            delivery_request=delivery_request,
        )
        stage = payload.get("stage") or request.POST.get("stage", "")
        if not stage:
            return JsonResponse({"error": "stage is required."}, status=400)

        def _do() -> dict[str, Any]:
            check = record_condition_check(
                package,
                stage=stage,
                actor=_actor(request),
                seal_status=payload.get("seal_status", "not_applicable"),
                physical_damage_observed=bool(payload.get("physical_damage_observed", False)),
                damage_description=payload.get("damage_description", ""),
                temperature_indicator_status=payload.get(
                    "temperature_indicator_status", "not_applicable"
                ),
                notes=payload.get("notes", ""),
            )
            return {"condition_check_id": check.pk, "has_any_concern": check.has_any_concern}

        data, status_code = idempotent_call(
            courier=courier, endpoint="condition_check", key=key, fn=_do, status_code=201
        )
        return JsonResponse(data, status=status_code)


class CompleteDeliveryView(CourierPermissionMixin, View):
    """Phase 6: capture recipient proof of delivery (PIN and/or signature, per
    `delivery_request.recipient_verification_method`) and attempt the
    `AT_DESTINATION -> DELIVERED` transition in one action — the courier
    endpoint that finally completes the delivery lifecycle Phase 5 stopped
    short of. See `apps.deliveries.state_machine.validate_delivered` for the
    hard gate this transition goes through (proof of delivery must exist,
    no open severe incident)."""

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        delivery_request = _assigned_delivery_or_403(pk, courier)
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        def _do() -> dict[str, Any]:
            actor = _actor(request)
            if delivery_request.recipient_verification_method == RecipientVerificationMethod.PIN:
                verify_recipient_pin(delivery_request, payload.get("pin", ""), actor=actor)
            capture_proof_of_delivery(
                delivery_request,
                actor=actor,
                delivered_to_name=payload.get("delivered_to_name", ""),
                signature_data_url=payload.get("signature_data_url", ""),
                typed_signature_name=payload.get("typed_signature_name", ""),
            )
            updated = transition_delivery_request(
                delivery_request, "delivered", actor=actor, reason="Recipient proof captured."
            )
            return {"status": updated.status}

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="delivery_complete", key=key, fn=_do
            )
        except PinVerificationError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        except ProofAlreadyCapturedError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except SignatureTooLargeError as exc:
            return JsonResponse({"error": str(exc)}, status=413)
        except (InvalidTransitionError, ValidationError) as exc:
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            return JsonResponse({"error": message}, status=409)
        return JsonResponse(data, status=status_code)


class ReportIncidentView(CourierPermissionMixin, View):
    """Phase 6: courier-initiated incident report from the active-delivery page."""

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        courier = _actor(request).courier_profile
        delivery_request = _assigned_delivery_or_403(pk, courier)
        payload = _json_body(request)
        key = _idempotency_key(request, payload)
        category = payload.get("category") or request.POST.get("category", "")
        severity = payload.get("severity") or request.POST.get("severity", "")
        summary = payload.get("summary") or request.POST.get("summary", "")
        if not key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)
        if not (category and severity and summary):
            return JsonResponse(
                {"error": "category, severity, and summary are required."}, status=400
            )

        def _do() -> dict[str, Any]:
            incident = open_incident(
                delivery_request,
                category=category,
                severity=severity,
                summary=summary,
                actor=_actor(request),
            )
            return {
                "incident_id": str(incident.pk),
                "placed_on_hold": incident.placed_delivery_on_hold,
            }

        try:
            data, status_code = idempotent_call(
                courier=courier, endpoint="incident_report", key=key, fn=_do, status_code=201
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            return JsonResponse({"error": message}, status=400)
        return JsonResponse(data, status=status_code)
