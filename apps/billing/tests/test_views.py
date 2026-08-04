"""HTTP-level tests for invoice views: billing-role gating and tenant
scoping of the invoice list."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.billing.services import generate_invoice_for_delivery
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.organizations.models import CustomerRole
from apps.organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory

pytestmark = pytest.mark.django_db


def _member(organization, role):
    user = UserFactory()
    OrganizationMembershipFactory(user=user, organization=organization, role=role)
    return user


def test_invoice_list_only_shows_the_users_own_organization() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    invoice_a = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_a))
    generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_b))

    user = _member(org_a, CustomerRole.BILLING_MANAGER)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("invoice-list"))

    invoices = list(response.context["invoices"])
    assert [i.pk for i in invoices] == [invoice_a.pk]


def test_read_only_auditor_cannot_view_another_orgs_invoice() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    invoice_b = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_b))

    user = _member(org_a, CustomerRole.READ_ONLY_AUDITOR)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("invoice-detail", kwargs={"pk": invoice_b.pk}))

    assert response.status_code == 403


def test_billing_manager_can_view_their_own_invoice_detail() -> None:
    org = OrganizationFactory()
    invoice = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org))

    user = _member(org, CustomerRole.BILLING_MANAGER)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("invoice-detail", kwargs={"pk": invoice.pk}))

    assert response.status_code == 200


def test_csv_export_returns_csv_content_type_and_is_tenant_scoped() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    invoice_b = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_b))

    user = _member(org_a, CustomerRole.OWNER)
    client = Client()
    client.force_login(user)

    response = client.get(reverse("invoice-export-csv", kwargs={"pk": invoice_b.pk}))
    assert response.status_code == 403


def test_generate_invoice_view_requires_billing_manage_access() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    non_billing_user = _member(org, CustomerRole.READ_ONLY_AUDITOR)

    client = Client()
    client.force_login(non_billing_user)
    response = client.post(reverse("invoice-generate", kwargs={"delivery_id": delivery_request.pk}))

    assert response.status_code == 403


def test_generate_invoice_view_succeeds_for_org_owner() -> None:
    org = OrganizationFactory()
    delivery_request = DeliveryRequestFactory(organization=org)
    owner = _member(org, CustomerRole.OWNER)

    client = Client()
    client.force_login(owner)
    response = client.post(reverse("invoice-generate", kwargs={"delivery_id": delivery_request.pk}))

    assert response.status_code == 302


def test_html_export_is_tenant_scoped_and_renders_html() -> None:
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    invoice_a = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_a))
    invoice_b = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org_b))

    user = _member(org_a, CustomerRole.OWNER)
    client = Client()
    client.force_login(user)

    own_response = client.get(reverse("invoice-export-html", kwargs={"pk": invoice_a.pk}))
    assert own_response.status_code == 200
    assert own_response["Content-Type"].startswith("text/html")

    other_response = client.get(reverse("invoice-export-html", kwargs={"pk": invoice_b.pk}))
    assert other_response.status_code == 403


def test_mark_paid_and_unpaid_round_trip_through_the_view() -> None:
    org = OrganizationFactory()
    invoice = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org))
    owner = _member(org, CustomerRole.OWNER)

    client = Client()
    client.force_login(owner)

    response = client.post(
        reverse("invoice-mark-paid", kwargs={"pk": invoice.pk}), {"action": "paid"}
    )
    assert response.status_code == 302
    invoice.refresh_from_db()
    assert invoice.payment_status == "paid"

    response = client.post(
        reverse("invoice-mark-paid", kwargs={"pk": invoice.pk}), {"action": "unpaid"}
    )
    assert response.status_code == 302
    invoice.refresh_from_db()
    assert invoice.payment_status == "unpaid"


def test_mark_paid_requires_billing_manage_access() -> None:
    org = OrganizationFactory()
    invoice = generate_invoice_for_delivery(DeliveryRequestFactory(organization=org))
    non_billing_user = _member(org, CustomerRole.READ_ONLY_AUDITOR)

    client = Client()
    client.force_login(non_billing_user)
    response = client.post(
        reverse("invoice-mark-paid", kwargs={"pk": invoice.pk}), {"action": "paid"}
    )

    assert response.status_code == 403
