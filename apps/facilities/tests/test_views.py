"""HTTP-level tenant-isolation and role-permission tests for the minimal
Facility CRUD UI. See apps/organizations/tests/test_views.py for the
Organization-level equivalents.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.models import CustomerRole, OrganizationMembership
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db

VALID_FACILITY_POST_DATA = {
    "name": "New HTTP Facility (Demo)",
    "facility_type": "clinic_site",
    "address_line1": "1 New Fictional St",
    "address_line2": "",
    "city": "New York",
    "state": "NY",
    "postal_code": "10001",
    "borough": "manhattan",
    "latitude": "",
    "longitude": "",
    "service_zone": "",
    "timezone": "America/New_York",
    "access_instructions": "",
    "verification_requirements": "",
    "is_active": True,
}


def test_facility_list_requires_login(client: Client) -> None:
    response = client.get(reverse("facility-list"))
    assert response.status_code == 302
    assert reverse("login") in response.url


def test_facility_list_scoped_to_own_org(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Fac Org A (Demo)")
    org_b = OrganizationFactory(name="HTTP Fac Org B (Demo)")
    facility_a = FacilityFactory(organization=org_a, name="HTTP Facility A (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="HTTP Facility B (Demo)")

    user_a = UserFactory(username="http_fac_user_a")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("facility-list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert facility_a.name in content
    assert facility_b.name not in content


def test_cannot_view_other_org_facility_detail(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Fac Org A2 (Demo)")
    org_b = OrganizationFactory(name="HTTP Fac Org B2 (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="HTTP Facility B2 (Demo)")

    user_a = UserFactory(username="http_fac_user_a2")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("facility-detail", kwargs={"pk": facility_b.pk}))

    assert response.status_code == 403


def test_cannot_edit_other_org_facility(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Fac Org A3 (Demo)")
    org_b = OrganizationFactory(name="HTTP Fac Org B3 (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="HTTP Facility B3 (Demo)")

    user_a = UserFactory(username="http_fac_user_a3")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)

    get_response = client.get(reverse("facility-update", kwargs={"pk": facility_b.pk}))
    assert get_response.status_code == 403

    post_response = client.post(
        reverse("facility-update", kwargs={"pk": facility_b.pk}), VALID_FACILITY_POST_DATA
    )
    assert post_response.status_code == 403
    facility_b.refresh_from_db()
    assert facility_b.name == "HTTP Facility B3 (Demo)"


def test_cannot_delete_other_org_facility(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Fac Org A4 (Demo)")
    org_b = OrganizationFactory(name="HTTP Fac Org B4 (Demo)")
    facility_b = FacilityFactory(organization=org_b, name="HTTP Facility B4 (Demo)")

    user_a = UserFactory(username="http_fac_user_a4")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.post(reverse("facility-delete", kwargs={"pk": facility_b.pk}))

    assert response.status_code == 403
    assert facility_b.__class__.objects.filter(pk=facility_b.pk).exists()


def test_read_only_auditor_cannot_create_facility(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Fac Org Auditor (Demo)")
    auditor = UserFactory(username="http_fac_auditor")
    OrganizationMembership.objects.create(
        user=auditor, organization=org, role=CustomerRole.READ_ONLY_AUDITOR
    )

    client.force_login(auditor)
    response = client.get(reverse("facility-create", kwargs={"organization_pk": org.pk}))
    assert response.status_code == 403


def test_administrator_can_create_facility_for_own_org(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Fac Org Admin (Demo)")
    admin_user = UserFactory(username="http_fac_admin")
    OrganizationMembership.objects.create(
        user=admin_user, organization=org, role=CustomerRole.ADMINISTRATOR
    )

    client.force_login(admin_user)
    response = client.post(
        reverse("facility-create", kwargs={"organization_pk": org.pk}), VALID_FACILITY_POST_DATA
    )

    assert response.status_code == 302
    assert org.facilities.filter(name="New HTTP Facility (Demo)").exists()


def test_administrator_cannot_create_facility_for_other_org(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Fac Org A5 (Demo)")
    org_b = OrganizationFactory(name="HTTP Fac Org B5 (Demo)")
    admin_user = UserFactory(username="http_fac_admin2")
    OrganizationMembership.objects.create(
        user=admin_user, organization=org_a, role=CustomerRole.ADMINISTRATOR
    )

    client.force_login(admin_user)
    response = client.post(
        reverse("facility-create", kwargs={"organization_pk": org_b.pk}), VALID_FACILITY_POST_DATA
    )

    assert response.status_code == 403
    assert not org_b.facilities.filter(name="New HTTP Facility (Demo)").exists()


def test_internal_dispatcher_can_view_but_not_manage_any_facility(client: Client) -> None:
    """Dispatcher has cross-org READ access but not cross-org MANAGE access."""
    org = OrganizationFactory(name="HTTP Fac Org Dispatch (Demo)")
    facility = FacilityFactory(organization=org, name="HTTP Dispatch Facility (Demo)")

    dispatcher = UserFactory(username="http_dispatcher")
    InternalRoleAssignment.objects.create(user=dispatcher, role=InternalRole.DISPATCHER)

    client.force_login(dispatcher)

    view_response = client.get(reverse("facility-detail", kwargs={"pk": facility.pk}))
    assert view_response.status_code == 200

    edit_response = client.get(reverse("facility-update", kwargs={"pk": facility.pk}))
    assert edit_response.status_code == 403


def test_internal_system_administrator_can_manage_any_facility(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Fac Org SysAdmin (Demo)")
    facility = FacilityFactory(organization=org, name="HTTP SysAdmin Facility (Demo)")

    sysadmin = UserFactory(username="http_sysadmin")
    InternalRoleAssignment.objects.create(user=sysadmin, role=InternalRole.SYSTEM_ADMINISTRATOR)

    client.force_login(sysadmin)
    response = client.get(reverse("facility-update", kwargs={"pk": facility.pk}))
    assert response.status_code == 200


def test_courier_onboarding_reviewer_cannot_view_any_facility(client: Client) -> None:
    """This internal role has no cross-org grant at all (see apps/organizations/services.py)."""
    org = OrganizationFactory(name="HTTP Fac Org Reviewer (Demo)")
    facility = FacilityFactory(organization=org, name="HTTP Reviewer Facility (Demo)")

    reviewer = UserFactory(username="http_courier_reviewer")
    InternalRoleAssignment.objects.create(
        user=reviewer, role=InternalRole.COURIER_ONBOARDING_REVIEWER
    )

    client.force_login(reviewer)
    response = client.get(reverse("facility-detail", kwargs={"pk": facility.pk}))
    assert response.status_code == 403
