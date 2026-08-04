"""`ExportJob` — an audit-log row for one report-export request.

Per docs/ARCHITECTURE_AND_DATA_MODEL.md's "Commercial and system" entity
list and section 9's "deduplicate... exports" idempotency note.

Design decision: `ExportJob` never stores the rendered CSV/HTML content
itself — it is purely a request/audit-log row ("who requested what export,
scoped to which organization, with which parameters, when"). The actual
export content is always generated fresh, from the live database, at
download time (`apps.reporting.services.render_report`) — this sidesteps
any staleness concern a cached/materialized export would raise (an org's
data can change between request and download) while still giving
"deduplicate exports" a real, testable meaning: **re-requesting the
identical export (same organization, report type, format, and parameters)
within a short window returns the existing `ExportJob` row instead of
writing a new audit-log entry**, rather than affecting what data the export
itself contains. See `apps.reporting.services.get_or_create_export_job`.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models


class ExportFormat(models.TextChoices):
    CSV = "csv", "CSV"
    HTML = "html", "HTML"


class ExportJobQuerySet(models.QuerySet["ExportJob"]):
    def for_user(self, user: Any) -> Any:
        from apps.organizations.services import scope_queryset_to_user_orgs

        return scope_queryset_to_user_orgs(self, user, org_field="organization_id")


class ExportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="export_jobs"
    )
    report_type = models.CharField(
        max_length=32, help_text="An apps.reporting.reports.ReportType value."
    )
    export_format = models.CharField(max_length=8, choices=ExportFormat.choices)
    params = models.JSONField(default=dict, blank=True)
    params_hash = models.CharField(
        max_length=64,
        help_text="sha256 of (report_type, export_format, sorted params) — the dedup key, "
        "scoped per organization.",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="export_jobs_requested",
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    objects = ExportJobQuerySet.as_manager()

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["organization", "report_type", "export_format", "params_hash"])
        ]

    def __str__(self) -> str:
        return f"{self.report_type} ({self.export_format}) for {self.organization_id}"


__all__ = ["ExportFormat", "ExportJob"]
