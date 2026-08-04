"""Export-job dedup, report rendering, and the operational-metrics summary.

**Hard acceptance criterion (Phase 7): exports are tenant-scoped.** Every
function here that renders a report takes an `organization_id` that must
already have passed `apps.organizations.services.can_export_reports` in the
caller (see `apps.reporting.views`) — nothing in this module accepts an
organization ID "blindly" from a client; it only ever receives one the view
layer has already checked. `render_report`/`render_report_csv`/
`render_report_html` never take more than one organization at a time,
structurally ruling out an export that accidentally spans two tenants.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.reporting.models import ExportJob
from apps.reporting.rendering import rows_to_csv, rows_to_html
from apps.reporting.reports import REPORT_REGISTRY

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.organizations.models import Organization

# How long an identical repeated export request reuses the same ExportJob
# audit-log row rather than writing a new one. Deliberately short (this is
# an audit-log dedup window, not a content cache — see model docstring);
# the export's own *content* is always regenerated live regardless of this
# window.
EXPORT_DEDUPE_WINDOW = timedelta(minutes=5)


class UnknownReportTypeError(Exception):
    """Raised for a `report_type` not in `apps.reporting.reports.REPORT_REGISTRY`."""


def _params_hash(report_type: str, export_format: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"report_type": report_type, "export_format": export_format, "params": params},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_create_export_job(
    *,
    organization: Organization,
    report_type: str,
    export_format: str,
    params: dict[str, Any] | None = None,
    requested_by: User | None = None,
) -> ExportJob:
    """Return an existing `ExportJob` if an identical one (same
    organization/report_type/export_format/params) was requested within
    `EXPORT_DEDUPE_WINDOW`, otherwise create a new one."""
    if report_type not in REPORT_REGISTRY:
        raise UnknownReportTypeError(report_type)

    params = params or {}
    params_hash = _params_hash(report_type, export_format, params)
    cutoff = timezone.now() - EXPORT_DEDUPE_WINDOW
    existing = (
        ExportJob.objects.filter(
            organization=organization,
            report_type=report_type,
            export_format=export_format,
            params_hash=params_hash,
            requested_at__gte=cutoff,
        )
        .order_by("-requested_at")
        .first()
    )
    if existing is not None:
        return existing

    return ExportJob.objects.create(
        organization=organization,
        report_type=report_type,
        export_format=export_format,
        params=params,
        params_hash=params_hash,
        requested_by=requested_by,
    )


def render_report_csv(report_type: str, organization_id: Any) -> str:
    """Render `report_type`'s current data for exactly one organization as
    CSV. `organization_id` must already have been permission-checked by the
    caller — see module docstring."""
    if report_type not in REPORT_REGISTRY:
        raise UnknownReportTypeError(report_type)
    fields, row_builder, title = REPORT_REGISTRY[report_type]
    return rows_to_csv(fields, row_builder(organization_id), title=title)


def render_report_html(report_type: str, organization_id: Any) -> str:
    if report_type not in REPORT_REGISTRY:
        raise UnknownReportTypeError(report_type)
    fields, row_builder, title = REPORT_REGISTRY[report_type]
    return rows_to_html(fields, row_builder(organization_id), title=title)


def render_export_job(job: ExportJob) -> str:
    """Render `job`'s report, live, scoped to `job.organization_id` — the
    tenant-scoping enforcement point for the *download* path (as distinct
    from the *request* path, which is enforced in
    `apps.reporting.views.ExportRequestView`). Even a caller who already
    knows another organization's `ExportJob` id cannot use this function to
    read that organization's data unless the view layer has independently
    confirmed access to `job.organization_id` first."""
    from apps.reporting.models import ExportFormat

    if job.export_format == ExportFormat.HTML:
        return render_report_html(job.report_type, job.organization_id)
    return render_report_csv(job.report_type, job.organization_id)


def operational_metrics(organization_id: Any) -> dict[str, Any]:
    """A cheap, directly-derivable summary: delivery volume, on-time %,
    incident count, average quote value — per docs/IMPLEMENTATION_ROADMAP.md
    Phase 7's "operational metrics"."""
    from decimal import Decimal

    from apps.deliveries.models import DeliveryRequest
    from apps.incidents.models import Incident
    from apps.reporting.reports import on_time_performance_rows

    delivery_qs = DeliveryRequest.objects.filter(organization_id=organization_id)
    delivery_volume = delivery_qs.count()

    on_time_rows = on_time_performance_rows(organization_id)
    delivered_count = len(on_time_rows)
    on_time_count = sum(1 for row in on_time_rows if row["on_time"])
    on_time_percentage = (
        round((on_time_count / delivered_count) * 100, 1) if delivered_count else None
    )

    incident_count = Incident.objects.filter(
        delivery_request__organization_id=organization_id
    ).count()

    quoted: list[Decimal] = [
        d.estimated_price for d in delivery_qs if d.estimated_price is not None
    ]
    average_quote_value = round(sum(quoted, Decimal("0")) / len(quoted), 2) if quoted else None

    return {
        "delivery_volume": delivery_volume,
        "delivered_count": delivered_count,
        "on_time_count": on_time_count,
        "on_time_percentage": on_time_percentage,
        "incident_count": incident_count,
        "average_quote_value": average_quote_value,
    }


__all__ = [
    "EXPORT_DEDUPE_WINDOW",
    "UnknownReportTypeError",
    "get_or_create_export_job",
    "operational_metrics",
    "render_export_job",
    "render_report_csv",
    "render_report_html",
]
