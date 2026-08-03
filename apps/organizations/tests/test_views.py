"""HTTP-level tenant-isolation and role-permission tests for the minimal
Organization CRUD UI.

These exercise the same guarantees as apps/organizations/tests/test_services.py,
but through real requests via the Django test client, per the Phase 1
acceptance criteria in docs/IMPLEMENTATION_ROADMAP.md ("ideally through any
view/API you add").
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import CustomerRole, OrganizationMembership
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def test_organization_list_requires_login(client: Client) -> None:
    response = client.get(reverse("organization-list"))
    assert response.status_code == 302
    assert reverse("login") in response.url


def test_organization_list_only_shows_own_organizations(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Org A (Demo)")
    org_b = OrganizationFactory(name="HTTP Org B (Demo)")
    user_a = UserFactory(username="http_user_a")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("organization-list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert org_a.name in content
    assert org_b.name not in content


def test_cannot_view_other_org_detail_via_http(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Org A2 (Demo)")
    org_b = OrganizationFactory(name="HTTP Org B2 (Demo)")
    user_a = UserFactory(username="http_user_a2")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("organization-detail", kwargs={"pk": org_b.pk}))

    assert response.status_code == 403


def test_can_view_own_org_detail_via_http(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Org A3 (Demo)")
    user_a = UserFactory(username="http_user_a3")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("organization-detail", kwargs={"pk": org_a.pk}))

    assert response.status_code == 200
    assert org_a.name in response.content.decode()


def test_read_only_auditor_cannot_edit_org_via_http(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Org Auditor (Demo)")
    auditor = UserFactory(username="http_auditor")
    OrganizationMembership.objects.create(
        user=auditor, organization=org, role=CustomerRole.READ_ONLY_AUDITOR
    )

    client.force_login(auditor)

    get_response = client.get(reverse("organization-update", kwargs={"pk": org.pk}))
    assert get_response.status_code == 403

    post_response = client.post(
        reverse("organization-update", kwargs={"pk": org.pk}),
        {"name": "Hacked Name (Demo)", "org_type": org.org_type, "is_active": True, "notes": ""},
    )
    assert post_response.status_code == 403
    org.refresh_from_db()
    assert org.name == "HTTP Org Auditor (Demo)"


def test_owner_can_edit_org_via_http(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Org Owner (Demo)")
    owner = UserFactory(username="http_owner")
    OrganizationMembership.objects.create(user=owner, organization=org, role=CustomerRole.OWNER)

    client.force_login(owner)
    response = client.post(
        reverse("organization-update", kwargs={"pk": org.pk}),
        {
            "name": "HTTP Org Owner Renamed (Demo)",
            "org_type": org.org_type,
            "is_active": True,
            "notes": "",
        },
    )

    assert response.status_code == 302
    org.refresh_from_db()
    assert org.name == "HTTP Org Owner Renamed (Demo)"


def test_requester_dispatcher_cannot_edit_org_via_http(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Org Dispatcher (Demo)")
    dispatcher = UserFactory(username="http_req_dispatcher")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )

    client.force_login(dispatcher)
    response = client.get(reverse("organization-update", kwargs={"pk": org.pk}))
    assert response.status_code == 403


def test_plain_member_cannot_create_new_organization(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Org Creator Check (Demo)")
    owner = UserFactory(username="http_owner_creator")
    OrganizationMembership.objects.create(user=owner, organization=org, role=CustomerRole.OWNER)

    client.force_login(owner)
    response = client.get(reverse("organization-create"))
    assert response.status_code == 403


def test_internal_ops_manager_can_create_new_organization(client: Client) -> None:
    ops_manager = UserFactory(username="http_ops_manager")
    InternalRoleAssignment.objects.create(user=ops_manager, role=InternalRole.OPERATIONS_MANAGER)

    client.force_login(ops_manager)
    response = client.post(
        reverse("organization-create"),
        {
            "name": "HTTP New Org By Ops (Demo)",
            "org_type": "clinic",
            "is_active": True,
            "notes": "",
        },
    )
    assert response.status_code == 302
    assert response.url is not None


def test_internal_customer_support_can_view_but_not_create_org(client: Client) -> None:
    """Customer support has cross-org READ access, but not cross-org MANAGE access."""
    org = OrganizationFactory(name="HTTP Org Support (Demo)")
    support = UserFactory(username="http_support")
    InternalRoleAssignment.objects.create(user=support, role=InternalRole.CUSTOMER_SUPPORT)

    client.force_login(support)

    detail_response = client.get(reverse("organization-detail", kwargs={"pk": org.pk}))
    assert detail_response.status_code == 200

    create_response = client.get(reverse("organization-create"))
    assert create_response.status_code == 403
