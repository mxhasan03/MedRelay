"""Minimal server-rendered dispatch board / control-tower views
(docs/PRODUCT_REQUIREMENTS.md section 7 "Operations control center").

Scoping decision (docs/CURRENT_STATUS.md "Phase 4" section has the full
write-up): this is a plain-HTML list/table dashboard, matching Phase 1's CRUD
UI convention exactly (no Tailwind CDN/HTMX/JS — that is Phase 8's design
pass) — a literal live map (MapLibre) is explicitly **not** built this phase
(a nice-to-have deferred to Phase 8/9 polish per the roadmap's own framing).
"Control tower" fields this phase can honestly show are limited to what
Phase 4's data model actually has: unassigned deliveries, offered/assigned
deliveries, and at-risk deadlines (from `apps.dispatch.scoring`'s SLA
factor). Courier locations, incidents, and temperature alerts are not shown
— those need `apps.tracking`/`apps.incidents`/`apps.temperature`, none of
which have models yet (later phases).
"""

from __future__ import annotations

import datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import User
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.dispatch import services
from apps.dispatch.exceptions import AssignmentConflictError, IneligibleCourierError
from apps.dispatch.models import AssignmentStatus
from apps.organizations.services import can_dispatch


def _actor(request: HttpRequest) -> User:
    """Narrow `request.user` to the concrete `User` model — safe here because
    every view in this module is behind `DispatchPermissionMixin`
    (`LoginRequiredMixin` + `can_dispatch`), matching
    `apps.deliveries.views._actor`'s exact pattern."""
    user = request.user
    assert isinstance(user, User)
    return user


class DispatchPermissionMixin(LoginRequiredMixin):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        if request.user.is_authenticated and not can_dispatch(request.user):
            raise PermissionDenied("You do not have dispatch board access.")
        return super().dispatch(request, *args, **kwargs)


class DispatchBoardListView(DispatchPermissionMixin, TemplateView):
    template_name = "dispatch/board_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        unassigned = list(
            DeliveryRequest.objects.filter(
                status__in=(DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.OFFERED)
            )
            .select_related("organization")
            .prefetch_related("stops__facility")
        )
        at_risk_ids = services.at_risk_delivery_ids()
        assigned = list(
            DeliveryRequest.objects.filter(status=DeliveryStatus.ASSIGNED)
            .select_related("organization")
            .prefetch_related("assignments__courier__user", "stops__facility")
        )
        context["unassigned_deliveries"] = unassigned
        context["at_risk_ids"] = at_risk_ids
        context["assigned_deliveries"] = assigned
        return context


class DispatchBoardDetailView(DispatchPermissionMixin, DetailView):
    template_name = "dispatch/board_detail.html"
    context_object_name = "delivery_request"

    def get_object(self, queryset: Any = None) -> DeliveryRequest:
        return get_object_or_404(
            DeliveryRequest.objects.select_related("organization").prefetch_related(
                "stops__facility", "assignments__courier__user"
            ),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        delivery_request = self.object
        candidates = services.recommend_couriers(delivery_request.pk, actor=_actor(self.request))
        context["candidates"] = candidates
        context["active_assignment"] = delivery_request.assignments.filter(
            status=AssignmentStatus.ACTIVE
        ).first()
        context["now"] = timezone.now()
        return context


class DispatchActionView(DispatchPermissionMixin, View):
    """Shared base for the assign/reassign/offer POST-only action views."""

    def get_delivery_request(self) -> DeliveryRequest:
        return get_object_or_404(DeliveryRequest, pk=self.kwargs["pk"])

    def redirect_to_detail(self) -> HttpResponse:
        return redirect(reverse("dispatch-board-detail", kwargs={"pk": self.kwargs["pk"]}))


class DispatchAssignView(DispatchActionView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        delivery_request = self.get_delivery_request()
        courier_id = request.POST.get("courier_id")
        reason = request.POST.get("reason", "").strip() or None
        try:
            services.assign_delivery(
                delivery_request.pk, courier_id, _actor(request), reason=reason
            )
        except (IneligibleCourierError, AssignmentConflictError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Delivery assigned.")
        return self.redirect_to_detail()


class DispatchReassignView(DispatchActionView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        delivery_request = self.get_delivery_request()
        courier_id = request.POST.get("courier_id")
        reason = request.POST.get("reason", "").strip()
        try:
            services.reassign_delivery(delivery_request.pk, courier_id, _actor(request), reason)
        except (IneligibleCourierError, AssignmentConflictError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Delivery reassigned.")
        return self.redirect_to_detail()


class DispatchOfferView(DispatchActionView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        delivery_request = self.get_delivery_request()
        candidate_ids = request.POST.getlist("courier_ids")
        minutes = int(request.POST.get("expires_in_minutes") or 30)
        expires_at = timezone.now() + datetime.timedelta(minutes=minutes)
        try:
            services.offer_delivery(
                delivery_request.pk, candidate_ids, expires_at, actor=_actor(request)
            )
        except (IneligibleCourierError, AssignmentConflictError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Offer sent to {len(candidate_ids)} courier(s).")
        return self.redirect_to_detail()
