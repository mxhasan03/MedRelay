"""Minimal server-rendered CRUD views for Facility, tenant-scoped.

Every fetch here is scoped by `apps.organizations.services` permission
checks (via `Facility.objects.for_user(...)` for lists, and explicit
`can_view_organization`/`can_manage_facilities` checks for single-object
views) — this is what the Phase 1 cross-tenant HTTP-level tests exercise.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.facilities.forms import FacilityForm
from apps.facilities.models import Facility
from apps.organizations.models import Organization
from apps.organizations.services import can_manage_facilities, can_view_organization


class FacilityListView(LoginRequiredMixin, ListView):
    template_name = "facilities/facility_list.html"
    context_object_name = "facilities"

    def get_queryset(self) -> Any:
        return Facility.objects.for_user(self.request.user).select_related("organization")


class FacilityDetailView(LoginRequiredMixin, DetailView):
    template_name = "facilities/facility_detail.html"
    context_object_name = "facility"

    def get_object(self, queryset: Any = None) -> Facility:
        obj = get_object_or_404(
            Facility.objects.select_related("organization"), pk=self.kwargs["pk"]
        )
        if not can_view_organization(self.request.user, obj.organization_id):
            raise PermissionDenied("You do not have access to this facility.")
        return obj

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["can_manage"] = can_manage_facilities(
            self.request.user, self.object.organization_id
        )
        context["contacts"] = self.object.contacts.all()
        context["receiving_rules"] = self.object.receiving_rules.all()
        return context


class FacilityCreateView(LoginRequiredMixin, CreateView):
    form_class = FacilityForm
    template_name = "facilities/facility_form.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        self.organization = get_object_or_404(Organization, pk=kwargs["organization_pk"])
        if request.user.is_authenticated and not can_manage_facilities(
            request.user, self.organization.pk
        ):
            raise PermissionDenied(
                "You do not have permission to add facilities for this organization."
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: FacilityForm) -> Any:
        form.instance.organization = self.organization
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        return context

    def get_success_url(self) -> str:
        assert self.object is not None
        return reverse("facility-detail", kwargs={"pk": self.object.pk})


class FacilityUpdateView(LoginRequiredMixin, UpdateView):
    form_class = FacilityForm
    template_name = "facilities/facility_form.html"

    def get_object(self, queryset: Any = None) -> Facility:
        obj = get_object_or_404(Facility, pk=self.kwargs["pk"])
        if not can_manage_facilities(self.request.user, obj.organization_id):
            raise PermissionDenied("You do not have permission to edit this facility.")
        return obj

    def get_success_url(self) -> str:
        assert self.object is not None
        return reverse("facility-detail", kwargs={"pk": self.object.pk})


class FacilityDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "facilities/facility_confirm_delete.html"
    success_url = reverse_lazy("facility-list")

    def get_object(self, queryset: Any = None) -> Facility:
        obj = get_object_or_404(Facility, pk=self.kwargs["pk"])
        if not can_manage_facilities(self.request.user, obj.organization_id):
            raise PermissionDenied("You do not have permission to delete this facility.")
        return obj
