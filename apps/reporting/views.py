"""Report/export request + download views, and the operational-metrics
dashboard. Every view checks `apps.organizations.services.can_export_reports`
against an explicit `organization_id` — never trusting a client-supplied
organization or export-job ID without that check (the hard "exports are
tenant-scoped" acceptance criterion)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.organizations.models import Organization
from apps.organizations.services import can_export_reports
from apps.reporting.models import ExportFormat, ExportJob
from apps.reporting.reports import REPORT_TYPE_CHOICES
from apps.reporting.services import (
    get_or_create_export_job,
    operational_metrics,
    render_export_job,
)


def _organization_or_403(request: HttpRequest, organization_id: Any) -> Organization:
    organization = get_object_or_404(Organization, pk=organization_id)
    if not can_export_reports(request.user, organization.pk):
        raise PermissionDenied("You do not have report-export access for this organization.")
    return organization


class OrganizationReportsView(LoginRequiredMixin, View):
    """`GET/POST /reporting/organizations/<int:organization_id>/` — the
    metrics dashboard plus the export-request form."""

    def get(self, request: HttpRequest, organization_id: int) -> HttpResponse:
        organization = _organization_or_403(request, organization_id)
        context = {
            "organization": organization,
            "metrics": operational_metrics(organization.pk),
            "report_type_choices": REPORT_TYPE_CHOICES,
            "recent_jobs": ExportJob.objects.filter(organization=organization)[:20],
        }
        return render(request, "reporting/dashboard.html", context)

    def post(self, request: HttpRequest, organization_id: int) -> HttpResponse:
        organization = _organization_or_403(request, organization_id)
        report_type = request.POST.get("report_type", "")
        export_format = request.POST.get("export_format", ExportFormat.CSV)
        actor = request.user if request.user.is_authenticated else None
        job = get_or_create_export_job(
            organization=organization,
            report_type=report_type,
            export_format=export_format,
            requested_by=actor,
        )
        return redirect(reverse("export-download", kwargs={"job_id": job.pk}))


class ExportDownloadView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, job_id: Any) -> HttpResponse:
        job = get_object_or_404(ExportJob, pk=job_id)
        # Tenant-scoping check at *download* time, independent of whatever
        # check ran at request time — see apps.reporting.services.
        # render_export_job's docstring for why this matters even for a
        # caller who already knows another organization's job id.
        if not can_export_reports(request.user, job.organization_id):
            raise PermissionDenied("You do not have report-export access for this organization.")
        content = render_export_job(job)
        content_type = "text/html" if job.export_format == ExportFormat.HTML else "text/csv"
        response = HttpResponse(content, content_type=content_type)
        if job.export_format == ExportFormat.CSV:
            response["Content-Disposition"] = f'attachment; filename="{job.report_type}.csv"'
        return response
