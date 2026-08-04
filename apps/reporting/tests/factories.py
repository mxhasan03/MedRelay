"""factory_boy factories for reporting-app tests."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.organizations.tests.factories import OrganizationFactory
from apps.reporting.models import ExportFormat, ExportJob
from apps.reporting.reports import ReportType


class ExportJobFactory(DjangoModelFactory):
    class Meta:
        model = ExportJob

    organization = factory.SubFactory(OrganizationFactory)
    report_type = ReportType.DELIVERY_SUMMARY
    export_format = ExportFormat.CSV
    params_hash = "test-hash"
