"""Minimal server-rendered CRUD views for Organization, tenant-scoped.

This is the "prove tenant-scoping works through real HTTP requests, not
just at the queryset layer" half of the Phase 1 acceptance criteria (see
docs/IMPLEMENTATION_ROADMAP.md Phase 1 and docs/CURRENT_STATUS.md). Styling
is deliberately minimal plain HTML — the real design/accessibility pass is
Phase 8.

No plain function-based "list everything" shortcut is used anywhere here:
every object fetch goes through `apps.organizations.services` permission
checks before a response is returned, exactly like the queryset-layer tests
require.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.organizations.forms import OrganizationForm
from apps.organizations.models import Organization
from apps.organizations.services import (
    can_manage_organization,
    can_view_organization,
    has_cross_org_manage_access,
    organizations_for_user,
)


class OrganizationListView(LoginRequiredMixin, ListView):
    template_name = "organizations/organization_list.html"
    context_object_name = "organizations"

    def get_queryset(self) -> Any:
        return organizations_for_user(self.request.user)


class OrganizationDetailView(LoginRequiredMixin, DetailView):
    template_name = "organizations/organization_detail.html"
    context_object_name = "organization"

    def get_object(self, queryset: Any = None) -> Organization:
        obj = get_object_or_404(Organization, pk=self.kwargs["pk"])
        if not can_view_organization(self.request.user, obj.pk):
            raise PermissionDenied("You do not have access to this organization.")
        return obj

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["can_manage"] = can_manage_organization(self.request.user, self.object.pk)
        context["memberships"] = self.object.memberships.select_related("user").all()
        return context


class OrganizationCreateView(LoginRequiredMixin, CreateView):
    form_class = OrganizationForm
    template_name = "organizations/organization_form.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        if request.user.is_authenticated and not has_cross_org_manage_access(request.user):
            raise PermissionDenied(
                "Only internal operations staff with cross-org manage access may create "
                "new organizations in this prototype."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self) -> str:
        assert self.object is not None
        return reverse("organization-detail", kwargs={"pk": self.object.pk})


class OrganizationUpdateView(LoginRequiredMixin, UpdateView):
    form_class = OrganizationForm
    template_name = "organizations/organization_form.html"

    def get_object(self, queryset: Any = None) -> Organization:
        obj = get_object_or_404(Organization, pk=self.kwargs["pk"])
        if not can_manage_organization(self.request.user, obj.pk):
            raise PermissionDenied("You do not have permission to edit this organization.")
        return obj

    def get_success_url(self) -> str:
        assert self.object is not None
        return reverse("organization-detail", kwargs={"pk": self.object.pk})
