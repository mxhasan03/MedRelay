"""HTTP-level, end-to-end tests for report exports.

**Hard acceptance criterion (Phase 7): exports are tenant-scoped.** These
tests build genuinely mixed-tenant data (organization A and organization B
each with their own deliveries) and confirm: (1) an org-A-scoped user's
rendered export contains ONLY org A's records, never org B's; (2) that same
user cannot pull org B's data by requesting an export scoped to org B's ID
directly, nor by fetching an `ExportJob` id that belongs to org B — both are
rejected with a real permission error, never a silently-empty result that
could be mistaken for "org B just has no data".
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.organizations.models import CustomerRole
from apps.organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from apps.reporting.models import ExportFormat, ExportJob
from apps.reporting.reports import ReportType
from apps.reporting.services import get_or_create_export_job

pytestmark = pytest.mark.django_db


def _member(organization, role=CustomerRole.OWNER):
    user = UserFactory()
    OrganizationMembershipFactory(user=user, organization=organization, role=role)
    return user


def test_export_request_view_requires_login() -> None:
    org = OrganizationFactory()
    client = Client()
    response = client.get(reverse("organization-reports", kwargs={"organization_id": org.pk}))
    assert response.status_code == 302


def test_org_scoped_user_can_view_their_own_dashboard() -> None:
    org = OrganizationFactory()
    user = _member(org)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("organization-reports", kwargs={"organization_id": org.pk}))

    assert response.status_code == 200


def test_org_scoped_user_cannot_view_another_orgs_dashboard() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    user = _member(org_a)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("organization-reports", kwargs={"organization_id": org_b.pk}))

    assert response.status_code == 403


def test_end_to_end_delivery_summary_export_contains_only_the_requesting_orgs_records() -> None:
    """The genuine end-to-end test: mixed-tenant data, a real HTTP request
    by an org-A-scoped user, and an assertion on the actual rendered CSV
    bytes — not just a queryset assertion."""
    org_a = OrganizationFactory(name="Org A (Demo)")
    org_b = OrganizationFactory(name="Org B (Demo)")
    delivery_a1 = DeliveryRequestFactory(organization=org_a)
    delivery_a2 = DeliveryRequestFactory(organization=org_a)
    delivery_b1 = DeliveryRequestFactory(organization=org_b)

    user = _member(org_a)
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("organization-reports", kwargs={"organization_id": org_a.pk}),
        {"report_type": ReportType.DELIVERY_SUMMARY, "export_format": ExportFormat.CSV},
    )
    assert response.status_code == 302
    download_response = client.get(response.url)
    assert download_response.status_code == 200

    content = download_response.content.decode()
    assert str(delivery_a1.pk) in content
    assert str(delivery_a2.pk) in content
    assert str(delivery_b1.pk) not in content
    assert org_b.name not in content


def test_requesting_an_export_for_another_organization_by_id_is_rejected_not_empty() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    DeliveryRequestFactory(organization=org_b)
    user = _member(org_a)
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("organization-reports", kwargs={"organization_id": org_b.pk}),
        {"report_type": ReportType.DELIVERY_SUMMARY, "export_format": ExportFormat.CSV},
    )

    assert response.status_code == 403
    assert ExportJob.objects.filter(organization=org_b).count() == 0


def test_downloading_another_orgs_export_job_by_id_is_rejected_not_empty() -> None:
    """Even if the org-A user somehow learns org B's real ExportJob id
    (e.g. by guessing a sequential-looking value), the download endpoint
    must independently re-check tenant access rather than trusting that the
    job id alone proves authorization."""
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    DeliveryRequestFactory(organization=org_b)
    job_b = get_or_create_export_job(
        organization=org_b, report_type=ReportType.DELIVERY_SUMMARY, export_format=ExportFormat.CSV
    )

    user = _member(org_a)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("export-download", kwargs={"job_id": job_b.pk}))

    assert response.status_code == 403


def test_internal_ops_with_cross_org_read_access_can_export_any_organization() -> None:
    from apps.accounts.models import InternalRole
    from apps.accounts.tests.factories import InternalRoleAssignmentFactory

    org = OrganizationFactory()
    DeliveryRequestFactory(organization=org)
    staff = UserFactory()
    InternalRoleAssignmentFactory(user=staff, role=InternalRole.OPERATIONS_MANAGER)

    client = Client()
    client.force_login(staff)
    response = client.get(reverse("organization-reports", kwargs={"organization_id": org.pk}))

    assert response.status_code == 200


def test_courier_onboarding_reviewer_has_no_cross_org_export_access() -> None:
    """The one internal role explicitly excluded from cross-org read access
    (apps.organizations.services.CROSS_ORG_READ_ROLES) must not be able to
    export any organization's data either."""
    from apps.accounts.models import InternalRole
    from apps.accounts.tests.factories import InternalRoleAssignmentFactory

    org = OrganizationFactory()
    staff = UserFactory()
    InternalRoleAssignmentFactory(user=staff, role=InternalRole.COURIER_ONBOARDING_REVIEWER)

    client = Client()
    client.force_login(staff)
    response = client.get(reverse("organization-reports", kwargs={"organization_id": org.pk}))

    assert response.status_code == 403
