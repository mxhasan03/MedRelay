"""Minimal server-rendered views for delivery requests, tenant-scoped.

Same pattern as apps/organizations/views.py and apps/facilities/views.py:
every fetch is scoped through `apps.organizations.services`, and creation
goes through the `apps.deliveries.services` service layer rather than a bare
`ModelForm.save()`, since creating a delivery request also creates its
stops/packages/identifiers/attestation in one transaction.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView
from django.views.generic.edit import FormView

from apps.accounts.models import User
from apps.deliveries.forms import DeliveryRequestForm
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.deliveries.services import (
    cancel_delivery_request,
    create_delivery_request,
    submit_delivery_request,
)
from apps.deliveries.state_machine import validate_ready_for_dispatch
from apps.organizations.models import Organization
from apps.organizations.services import can_create_delivery_requests, can_view_organization


def _actor(request: HttpRequest) -> User:
    """Narrow `request.user` (typed `AbstractBaseUser | AnonymousUser` by Django's
    stubs) to the concrete `User` model. Safe to call in any view here because
    every view/action in this module is behind `LoginRequiredMixin`, which
    guarantees an authenticated (non-anonymous) user at runtime by the time this
    is called."""
    user = request.user
    assert isinstance(user, User)
    return user


class DeliveryRequestListView(LoginRequiredMixin, ListView):
    template_name = "deliveries/deliveryrequest_list.html"
    context_object_name = "delivery_requests"

    def get_queryset(self) -> Any:
        return (
            DeliveryRequest.objects.for_user(self.request.user)
            .select_related("organization", "cargo_class", "temperature_profile")
            .prefetch_related("stops__facility")
        )


class DeliveryRequestDetailView(LoginRequiredMixin, DetailView):
    template_name = "deliveries/deliveryrequest_detail.html"
    context_object_name = "delivery_request"

    def get_object(self, queryset: Any = None) -> DeliveryRequest:
        obj = get_object_or_404(
            DeliveryRequest.objects.select_related(
                "organization", "cargo_class", "temperature_profile"
            ).prefetch_related("stops__facility", "packages__identifier", "status_transitions"),
            pk=self.kwargs["pk"],
        )
        if not can_view_organization(self.request.user, obj.organization_id):
            raise PermissionDenied("You do not have access to this delivery request.")
        return obj

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        delivery_request = self.object
        context["can_manage"] = can_create_delivery_requests(
            self.request.user, delivery_request.organization_id
        )
        context["quote"] = getattr(delivery_request, "quote", None)
        context["packaging_attestation"] = getattr(delivery_request, "packaging_attestation", None)
        context["validation_errors"] = self._pending_validation_errors(delivery_request)
        return context

    @staticmethod
    def _pending_validation_errors(delivery_request: DeliveryRequest) -> list[str]:
        if delivery_request.status != DeliveryStatus.VALIDATION_REQUIRED:
            return []
        try:
            validate_ready_for_dispatch(delivery_request)
        except ValidationError as exc:
            return list(exc.messages)
        return []


class DeliveryRequestCreateView(LoginRequiredMixin, FormView):
    form_class = DeliveryRequestForm
    template_name = "deliveries/deliveryrequest_form.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        self.organization = get_object_or_404(Organization, pk=kwargs["organization_pk"])
        if request.user.is_authenticated and not can_create_delivery_requests(
            request.user, self.organization.pk
        ):
            raise PermissionDenied(
                "You do not have permission to create delivery requests for this organization."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        return context

    def form_valid(self, form: DeliveryRequestForm) -> HttpResponse:
        data = form.cleaned_data
        actor = _actor(self.request)
        delivery_request = create_delivery_request(
            organization=self.organization,
            created_by=actor,
            service_level=data["service_level"],
            pickup_facility=data["pickup_facility"],
            destination_facility=data["destination_facility"],
            pickup_window_start=data["pickup_window_start"],
            pickup_window_end=data["pickup_window_end"],
            required_delivery_by=data["required_delivery_by"],
            cargo_class=data["cargo_class"],
            temperature_profile=data["temperature_profile"],
            package_count=data["package_count"],
            approximate_weight_kg=data["approximate_weight_kg"],
            approximate_length_cm=data["approximate_length_cm"],
            approximate_width_cm=data["approximate_width_cm"],
            approximate_height_cm=data["approximate_height_cm"],
            sender_contact_name=data["sender_contact_name"],
            sender_contact_phone=data["sender_contact_phone"],
            sender_contact_role=data["sender_contact_role"],
            recipient_contact_name=data["recipient_contact_name"],
            recipient_contact_phone=data["recipient_contact_phone"],
            recipient_contact_role=data["recipient_contact_role"],
            recipient_verification_method=data["recipient_verification_method"],
            facility_instructions=data["facility_instructions"],
            attest_packaging=data["attest_packaging"],
            attestation_notes=data["attestation_notes"],
        )
        self.created_object = delivery_request
        submit_delivery_request(delivery_request, actor=actor)
        delivery_request.refresh_from_db()
        if delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH:
            messages.success(self.request, "Delivery request created and ready for dispatch.")
        else:
            messages.warning(
                self.request,
                "Delivery request created but is awaiting validation — see the errors below.",
            )
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        return reverse("deliveryrequest-detail", kwargs={"pk": self.created_object.pk})


class DeliveryRequestActionView(LoginRequiredMixin, View):
    """Shared base for the submit/cancel POST-only action views."""

    def get_delivery_request(self) -> DeliveryRequest:
        obj = get_object_or_404(DeliveryRequest, pk=self.kwargs["pk"])
        if not can_create_delivery_requests(self.request.user, obj.organization_id):
            raise PermissionDenied("You do not have permission to act on this delivery request.")
        return obj


class DeliveryRequestSubmitView(DeliveryRequestActionView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        delivery_request = self.get_delivery_request()
        submit_delivery_request(delivery_request, actor=_actor(request))
        return redirect("deliveryrequest-detail", pk=delivery_request.pk)


class DeliveryRequestCancelView(DeliveryRequestActionView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        delivery_request = self.get_delivery_request()
        cancel_delivery_request(delivery_request, actor=_actor(request))
        return redirect("deliveryrequest-detail", pk=delivery_request.pk)
