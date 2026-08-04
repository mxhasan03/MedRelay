"""The incident console (docs/PRODUCT_REQUIREMENTS.md section 7): list open
incidents, view one incident's detail/action history, and resolve it with a
required resolution note.

Access is internal-ops only (`apps.organizations.services.can_dispatch` — the
same allowlist the dispatch board uses; an incident console is exactly the
kind of cross-organization operational tool a dispatcher/ops manager/system
administrator needs, not a customer-org-facing page). A real design pass
(the roadmap's Phase 8) is out of scope here — this is the same
plain-server-rendered-template convention every prior phase's ops UI used.
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

from apps.accounts.models import User
from apps.incidents.models import Incident, IncidentStatus
from apps.incidents.services import IncidentAlreadyResolvedError, resolve_incident
from apps.organizations.services import can_dispatch


def _actor(request: HttpRequest) -> User:
    user = request.user
    assert isinstance(user, User)
    return user


class IncidentConsolePermissionMixin(LoginRequiredMixin):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        if request.user.is_authenticated and not can_dispatch(request.user):
            raise PermissionDenied("Incident console access requires dispatch/ops access.")
        return super().dispatch(request, *args, **kwargs)


class IncidentListView(IncidentConsolePermissionMixin, ListView):
    template_name = "incidents/incident_list.html"
    context_object_name = "incidents"

    def get_queryset(self) -> Any:
        return (
            Incident.objects.filter(status=IncidentStatus.OPEN)
            .select_related("delivery_request", "package")
            .order_by("-opened_at")
        )


class IncidentDetailView(IncidentConsolePermissionMixin, DetailView):
    template_name = "incidents/incident_detail.html"
    context_object_name = "incident"

    def get_object(self, queryset: Any = None) -> Incident:
        return get_object_or_404(
            Incident.objects.select_related("delivery_request", "package").prefetch_related(
                "actions"
            ),
            pk=self.kwargs["pk"],
        )


class IncidentResolveView(IncidentConsolePermissionMixin, View):
    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        incident = get_object_or_404(Incident, pk=pk)
        resolution_type = request.POST.get("resolution_type", "")
        resolution_note = request.POST.get("resolution_note", "")
        try:
            resolve_incident(
                incident,
                resolution_type=resolution_type,
                resolution_note=resolution_note,
                actor=_actor(request),
            )
            messages.success(request, "Incident resolved.")
        except (ValidationError, IncidentAlreadyResolvedError) as exc:
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, message)
        return redirect(reverse("incident-detail", kwargs={"pk": incident.pk}))
