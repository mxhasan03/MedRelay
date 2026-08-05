"""Server-rendered dispatch board / control-tower views
(docs/PRODUCT_REQUIREMENTS.md section 7 "Operations control center").

Scoping decision (docs/CURRENT_STATUS.md "Phase 4" section has the full
write-up): a literal live map (MapLibre) is explicitly **not** built — a
bigger, separate effort out of scope for this UI-cleanup pass too.

## UI cleanup pass (post-roadmap, project-owner-requested)

This module now also wires up data that had real models
(`apps.tracking.CourierLocationPing`, `apps.incidents.Incident`,
`apps.temperature.TemperatureExcursion`) but was never surfaced on this
page, plus plain query-param sort/filter for the two board-list tables and
the ranked-candidates list — no JS, no architecture change to how
assign/reassign/offer work (still POST-and-redirect with Django
`messages`), consistent with this page's existing convention. See the
per-view docstrings below for the exact params each one reads.

Access is unchanged: every view here still requires `can_dispatch`, which is
already a cross-organization allowlist (`apps.organizations.services.
DISPATCH_ROLES`) — a dispatcher legitimately sees every organization's open
deliveries, so the new organization filter below is a display convenience,
not a tenant-scoping boundary; no new query in this module needs
`scope_queryset_to_user_orgs` for that reason (every queryset here was
already intentionally cross-tenant, gated at the view level, before this
pass).
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
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
from apps.incidents.models import IncidentStatus
from apps.organizations.models import Organization
from apps.organizations.services import can_dispatch
from apps.tracking.models import CourierLocationPing

if TYPE_CHECKING:
    from apps.dispatch.scoring import DispatchCandidate

# Fields a dispatcher may sort either board-list table by (query-param
# values, `-`-prefixed for descending) — a plain allowlist so an
# unrecognized/garbage `sort`/`?..._sort=` value can never become a SQL
# injection surface or crash `order_by()`; it just falls back to the
# default ordering below.
_DELIVERY_SORT_FIELDS = frozenset({"required_delivery_by", "service_level"})
_DEFAULT_DELIVERY_ORDER = "-created_at"  # DeliveryRequest.Meta.ordering's own default

# Candidate-list sort keys for the detail page (query-param values, same
# `-`-prefix-for-descending convention as the board-list tables' `sort`
# params above, for one consistent mental model across this whole page).
_CANDIDATE_SORT_KEYS: dict[str, Any] = {
    "score": lambda c: c.total_score if c.total_score is not None else -1,
    "eta": lambda c: c.eta_to_pickup_minutes,
    "sla_slack": lambda c: c.sla_slack_minutes,
}


def _sorted_deliveries(queryset: Any, sort_param: str) -> Any:
    if sort_param.lstrip("-") in _DELIVERY_SORT_FIELDS:
        field = sort_param
    else:
        field = _DEFAULT_DELIVERY_ORDER
    return queryset.order_by(field)


def _sorted_candidates(
    candidates: list[DispatchCandidate], sort_param: str
) -> list[DispatchCandidate]:
    if not sort_param:
        return candidates  # apps.dispatch.scoring.rank_candidates' own ranking, unchanged.
    descending = sort_param.startswith("-")
    key_fn = _CANDIDATE_SORT_KEYS.get(sort_param.lstrip("-"))
    if key_fn is None:
        return candidates
    return sorted(candidates, key=key_fn, reverse=descending)


def _with_incident_and_temperature_counts(queryset: Any) -> Any:
    """Annotate `open_incident_count`/`open_temp_excursion_count` onto a
    `DeliveryRequest` queryset — one query, not one per row (see the
    `Count(..., distinct=True)` note below for why `distinct=True` matters
    once both annotations are combined in a single `.annotate()` call: each
    `Count` joins a different reverse FK, and without `distinct=True` the
    join fan-out from combining both filters in one query would inflate
    both counts)."""
    return queryset.annotate(
        open_incident_count=Count(
            "incidents", filter=Q(incidents__status=IncidentStatus.OPEN), distinct=True
        ),
        open_temp_excursion_count=Count(
            "temperature_excursions",
            filter=Q(temperature_excursions__incident__status=IncidentStatus.OPEN),
            distinct=True,
        ),
    )


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
    """The two board-list tables (unassigned/offered, assigned), each with
    its own independent query-param sort/filter so clicking a header or
    filter on one table never resets the other's state:

    - `unassigned_sort`/`assigned_sort`: `required_delivery_by` or
      `service_level`, `-`-prefixed for descending (see
      `_DELIVERY_SORT_FIELDS`/`_sorted_deliveries`); default is
      `DeliveryRequest.Meta.ordering` (`-created_at`), unchanged from before
      this pass.
    - `unassigned_org`/`assigned_org`: filter either table to one
      `Organization` by ID.
    - `unassigned_at_risk`: `"1"` shows only unassigned/offered deliveries
      `apps.dispatch.services.sla_risk_by_delivery_id` flags as `at_risk`/
      `infeasible`. Not offered on the assigned table — an SLA risk
      computed before a courier existed is far less actionable once a
      delivery already has one; `apps.dispatch.services.recommend_couriers`'s
      own current-state ranking is the right tool for an already-assigned
      delivery's real-time status, not this page's `at_risk` filter.
    """

    template_name = "dispatch/board_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        get = self.request.GET

        unassigned_sort = get.get("unassigned_sort", "")
        unassigned_org = get.get("unassigned_org", "")
        unassigned_at_risk_only = get.get("unassigned_at_risk") == "1"

        unassigned_qs = _with_incident_and_temperature_counts(
            DeliveryRequest.objects.filter(
                status__in=(DeliveryStatus.READY_FOR_DISPATCH, DeliveryStatus.OFFERED)
            )
            .select_related("organization")
            .prefetch_related("stops__facility")
        )
        if unassigned_org:
            unassigned_qs = unassigned_qs.filter(organization_id=unassigned_org)
        unassigned_qs = _sorted_deliveries(unassigned_qs, unassigned_sort)

        sla_risk_by_delivery_id = services.sla_risk_by_delivery_id()
        unassigned = list(unassigned_qs)
        if unassigned_at_risk_only:
            unassigned = [d for d in unassigned if d.pk in sla_risk_by_delivery_id]

        assigned_sort = get.get("assigned_sort", "")
        assigned_org = get.get("assigned_org", "")
        assigned_qs = _with_incident_and_temperature_counts(
            DeliveryRequest.objects.filter(status=DeliveryStatus.ASSIGNED)
            .select_related("organization")
            .prefetch_related("assignments__courier__user", "stops__facility")
        )
        if assigned_org:
            assigned_qs = assigned_qs.filter(organization_id=assigned_org)
        assigned_qs = _sorted_deliveries(assigned_qs, assigned_sort)
        assigned = list(assigned_qs)

        context["unassigned_deliveries"] = unassigned
        context["assigned_deliveries"] = assigned
        context["sla_risk_by_delivery_id"] = sla_risk_by_delivery_id
        context["at_risk_ids"] = set(sla_risk_by_delivery_id)  # kept for template back-compat
        context["organizations"] = Organization.objects.order_by("name")
        context["unassigned_sort"] = unassigned_sort
        context["unassigned_org"] = unassigned_org
        context["unassigned_at_risk_only"] = unassigned_at_risk_only
        context["assigned_sort"] = assigned_sort
        context["assigned_org"] = assigned_org
        return context


class DispatchBoardDetailView(DispatchPermissionMixin, DetailView):
    """The ranked-candidates list supports:

    - `candidate_sort`: `score`, `eta`, or `sla_slack`, `-`-prefixed for
      descending (see `_CANDIDATE_SORT_KEYS`/`_sorted_candidates`) — bare
      field name sorts ascending, matching the board-list tables' sort
      convention. Default (no param) is `apps.dispatch.scoring.
      rank_candidates`'s own ranking (eligible first, highest score first),
      unchanged from before this pass.
    - `eligible_only=1`: hides ineligible candidates entirely rather than
      only visually de-emphasizing them. Chosen as an opt-in (not the
      default) because a dispatcher explicitly asking "why wasn't courier X
      recommended" (docs/PRODUCT_REQUIREMENTS.md section 11's own framing
      for `hard_failure_reasons`) needs the ineligible rows still visible
      by default; the existing de-emphasized styling already keeps them out
      of the way without hiding them.

    Each candidate's most recent `apps.tracking.CourierLocationPing` (if
    any — see module docstring on this being courier-level data, not
    delivery-level) is fetched in one extra query for the whole page (never
    N+1, one query for every candidate courier at once).
    """

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
        ranked_candidates = services.recommend_couriers(
            delivery_request.pk, actor=_actor(self.request)
        )
        # Computed from the *original* top-ranked-eligible-first order,
        # before the display-only eligible_only/candidate_sort params below
        # are applied — the delivery's own SLA-risk badge must stay a fixed
        # fact about the delivery (matching the board list's
        # `sla_risk_by_delivery_id`), not something that changes depending
        # on how a dispatcher currently has this table sorted/filtered.
        sla_feasibility = services.best_candidate_sla_feasibility(ranked_candidates)

        get = self.request.GET
        eligible_only = get.get("eligible_only") == "1"
        candidates = (
            [c for c in ranked_candidates if c.eligible] if eligible_only else ranked_candidates
        )
        candidate_sort = get.get("candidate_sort", "")
        candidates = _sorted_candidates(candidates, candidate_sort)

        courier_ids = [c.courier.pk for c in candidates]
        # One query for every candidate's latest ping, not one per
        # candidate: ordered so the first row seen per courier_id (via
        # `setdefault`) is that courier's most recent ping. Deliberately
        # not `.distinct("courier_id")` — that's PostgreSQL-only, and
        # `config.settings.test` (this project's test database, see
        # CLAUDE.md) is plain SQLite.
        latest_ping_by_courier_id: dict[Any, CourierLocationPing] = {}
        for ping in CourierLocationPing.objects.filter(courier_id__in=courier_ids).order_by(
            "courier_id", "-recorded_at"
        ):
            latest_ping_by_courier_id.setdefault(ping.courier_id, ping)

        open_incidents = list(
            delivery_request.incidents.filter(status=IncidentStatus.OPEN).order_by("-opened_at")
        )
        open_temp_excursion_count = delivery_request.temperature_excursions.filter(
            incident__status=IncidentStatus.OPEN
        ).count()

        context["candidates"] = candidates
        context["candidate_sort"] = candidate_sort
        context["eligible_only"] = eligible_only
        context["latest_ping_by_courier_id"] = latest_ping_by_courier_id
        context["open_incidents"] = open_incidents
        context["open_temp_excursion_count"] = open_temp_excursion_count
        context["sla_feasibility"] = sla_feasibility
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
