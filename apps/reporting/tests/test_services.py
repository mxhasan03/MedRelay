"""Tests for apps.reporting.services: export-job dedup, live-rendered
report content, and the operational-metrics summary. See test_views.py for
the full HTTP-level, end-to-end tenant-scoping test."""

from __future__ import annotations

import pytest

from apps.billing.services import generate_invoice_for_delivery
from apps.custody.services import capture_proof_of_delivery, capture_proof_of_pickup, record_event
from apps.deliveries.models import DeliveryStatus, DeliveryStatusTransition
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.incidents.tests.factories import IncidentFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.reporting.models import ExportFormat, ExportJob
from apps.reporting.reports import ReportType, on_time_performance_rows
from apps.reporting.services import (
    UnknownReportTypeError,
    get_or_create_export_job,
    operational_metrics,
    render_export_job,
    render_report_csv,
    render_report_html,
)

pytestmark = pytest.mark.django_db


def test_get_or_create_export_job_dedupes_identical_requests() -> None:
    org = OrganizationFactory()
    first = get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    second = get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    assert first.pk == second.pk
    assert ExportJob.objects.count() == 1


def test_get_or_create_export_job_does_not_dedupe_across_different_report_types() -> None:
    org = OrganizationFactory()
    get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    get_or_create_export_job(
        organization=org, report_type=ReportType.INCIDENT_SUMMARY, export_format=ExportFormat.CSV
    )
    assert ExportJob.objects.count() == 2


def test_get_or_create_export_job_does_not_dedupe_across_organizations() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    get_or_create_export_job(
        organization=org_a, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    get_or_create_export_job(
        organization=org_b, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    assert ExportJob.objects.count() == 2


def test_unknown_report_type_is_rejected() -> None:
    org = OrganizationFactory()
    with pytest.raises(UnknownReportTypeError):
        get_or_create_export_job(
            organization=org, report_type="not_a_real_report", export_format=ExportFormat.CSV
        )
    with pytest.raises(UnknownReportTypeError):
        render_report_csv("not_a_real_report", org.pk)


def test_render_export_job_renders_live_data_scoped_to_the_jobs_organization() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    job = get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )

    content = render_export_job(job)

    assert str(delivery_request.pk) in content


def test_render_export_job_reflects_data_added_after_the_job_was_created() -> None:
    """ExportJob never caches rendered content — a delivery created after
    the job row exists must still appear when the job is downloaded,
    proving the export is generated live, not from a stale snapshot."""
    org = OrganizationFactory()
    job = get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )
    later_delivery = DeliveryRequestFactory(organization=org)

    content = render_export_job(job)

    assert str(later_delivery.pk) in content


def test_custody_timeline_report_reuses_custody_events_not_a_recomputation() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    record_event(delivery_request, "request_created", actor_type="system")

    content = render_report_csv(ReportType.CUSTODY_TIMELINE, org.pk)

    assert "Request Created" in content


def test_incident_summary_report_reflects_real_incidents() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    IncidentFactory(delivery_request=delivery_request)

    content = render_report_csv(ReportType.INCIDENT_SUMMARY, org.pk)

    assert str(delivery_request.pk) in content


def test_proof_of_delivery_report_includes_both_pickup_and_delivery_proof() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    capture_proof_of_pickup(delivery_request, actor=None, sender_name="Front Desk (Test)")
    capture_proof_of_delivery(delivery_request, actor=None, delivered_to_name="R. Recipient")

    content = render_report_csv(ReportType.PROOF_OF_DELIVERY, org.pk)

    assert content.count(str(delivery_request.pk)) == 2
    assert "pickup" in content
    assert "delivery" in content


def test_on_time_performance_report_flags_on_time_and_late_deliveries() -> None:
    from datetime import timedelta

    from django.utils import timezone

    org = OrganizationFactory()
    # required_delivery_by comfortably after "now" -> the DELIVERED
    # transition (auto_now_add, i.e. "now") lands before it -> on time.
    on_time_delivery = DeliveryRequestFactory(
        organization=org,
        status=DeliveryStatus.DELIVERED,
        required_delivery_by=timezone.now() + timedelta(hours=6),
    )
    DeliveryStatusTransition.objects.create(
        delivery_request=on_time_delivery, to_status=DeliveryStatus.DELIVERED
    )
    # required_delivery_by already in the past -> the transition (now) lands
    # after it -> late.
    late_delivery = DeliveryRequestFactory(
        organization=org,
        status=DeliveryStatus.DELIVERED,
        required_delivery_by=timezone.now() - timedelta(hours=6),
    )
    DeliveryStatusTransition.objects.create(
        delivery_request=late_delivery, to_status=DeliveryStatus.DELIVERED
    )

    rows = {row["delivery_id"]: row["on_time"] for row in on_time_performance_rows(org.pk)}

    assert rows[str(on_time_delivery.pk)] is True
    assert rows[str(late_delivery.pk)] is False


def test_invoice_summary_report_reflects_real_invoices() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    invoice = generate_invoice_for_delivery(delivery_request)

    content = render_report_csv(ReportType.INVOICE_SUMMARY, org.pk)

    assert invoice.invoice_number in content


def test_render_report_html_renders_a_real_html_table() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)

    content = render_report_html(ReportType.DELIVERY_SUMMARY, org.pk)

    assert "<table" in content
    assert str(delivery_request.pk) in content


def test_render_export_job_renders_html_when_the_job_format_is_html() -> None:
    org = OrganizationFactory()
    DeliveryRequestFactory(organization=org)
    job = get_or_create_export_job(
        organization=org, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.HTML
    )

    content = render_export_job(job)

    assert "<table" in content


def test_operational_metrics_counts_deliveries_and_incidents() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    IncidentFactory(delivery_request=delivery_request)

    metrics = operational_metrics(org.pk)

    assert metrics["delivery_volume"] == 1
    assert metrics["incident_count"] == 1
    assert metrics["delivered_count"] == 0
    assert metrics["on_time_percentage"] is None
