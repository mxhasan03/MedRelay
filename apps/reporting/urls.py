"""URLconf for the operational-metrics dashboard and report exports."""

from __future__ import annotations

from django.urls import path

from apps.reporting import views

urlpatterns = [
    path(
        "organizations/<int:organization_id>/",
        views.OrganizationReportsView.as_view(),
        name="organization-reports",
    ),
    path(
        "exports/<uuid:job_id>/download/",
        views.ExportDownloadView.as_view(),
        name="export-download",
    ),
]
