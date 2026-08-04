"""The unified, read-only audit viewer UI.

Scope: internal ops/compliance-reviewer roles only
(`apps.organizations.services.can_view_audit_log` —
`compliance_reviewer`/`operations_manager`/`system_administrator`), matching
the same allowlist-function pattern every other cross-cutting permission in
this codebase already uses.

This page shows two things side by side, deliberately not merged into one
table (see `apps/audit/models.py`'s architecture-decision docstring):

1. The generic `AuditEvent` log (authentication + role/membership changes —
   the two event families with no existing home elsewhere), optionally
   filtered by event type and/or organization for tenant-aware review.
2. A fixed set of links into the domain-specific event logs that already
   exist elsewhere in the codebase (delivery status transitions, custody
   events, dispatch overrides, incident actions, export jobs) — this view
   never re-queries or duplicates their rows.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.views.generic import ListView

from apps.audit.models import AuditEvent, AuditEventType
from apps.organizations.models import Organization
from apps.organizations.services import can_view_audit_log


class AuditEventListView(LoginRequiredMixin, ListView):
    template_name = "audit/event_list.html"
    context_object_name = "events"
    paginate_by = 50

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        if request.user.is_authenticated and not can_view_audit_log(request.user):
            raise PermissionDenied(
                "Only compliance-reviewer/operations-manager/system-administrator internal "
                "roles may view the audit log."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> Any:
        queryset = AuditEvent.objects.select_related("actor", "organization").all()
        event_type = self.request.GET.get("event_type", "")
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        organization_id = self.request.GET.get("organization", "")
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["event_types"] = AuditEventType.choices
        context["organizations"] = Organization.objects.order_by("name")
        context["selected_event_type"] = self.request.GET.get("event_type", "")
        context["selected_organization"] = self.request.GET.get("organization", "")
        return context
