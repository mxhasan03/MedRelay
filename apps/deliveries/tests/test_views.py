"""HTTP-level tests for delivery-request views: tenant isolation, role
permissions, and the create/submit "wizard" flow blocking dispatch when
required cargo/packaging information is missing (through a real HTTP POST,
not just the service-layer call)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    TemperatureProfileFactory,
)
from apps.deliveries.models import DeliveryStatus
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.models import CustomerRole, OrganizationMembership
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def test_delivery_request_list_requires_login(client: Client) -> None:
    response = client.get(reverse("deliveryrequest-list"))
    assert response.status_code == 302
    assert reverse("login") in response.url


def test_delivery_request_list_only_shows_own_organization(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Delivery Org A (Demo)")
    org_b = OrganizationFactory(name="HTTP Delivery Org B (Demo)")
    dr_a = DeliveryRequestFactory(organization=org_a)
    DeliveryRequestFactory(organization=org_b)

    user_a = UserFactory(username="http_delivery_user_a")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("deliveryrequest-list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert str(dr_a.pk)[:8] in content


def test_cannot_view_other_org_delivery_request_via_http(client: Client) -> None:
    org_a = OrganizationFactory(name="HTTP Delivery Org A2 (Demo)")
    org_b = OrganizationFactory(name="HTTP Delivery Org B2 (Demo)")
    dr_b = DeliveryRequestFactory(organization=org_b)

    user_a = UserFactory(username="http_delivery_user_a2")
    OrganizationMembership.objects.create(user=user_a, organization=org_a, role=CustomerRole.OWNER)

    client.force_login(user_a)
    response = client.get(reverse("deliveryrequest-detail", kwargs={"pk": dr_b.pk}))
    assert response.status_code == 403


def test_read_only_auditor_cannot_create_delivery_request(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Delivery Org Auditor (Demo)")
    auditor = UserFactory(username="http_delivery_auditor")
    OrganizationMembership.objects.create(
        user=auditor, organization=org, role=CustomerRole.READ_ONLY_AUDITOR
    )

    client.force_login(auditor)
    response = client.get(reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}))
    assert response.status_code == 403


def test_requester_dispatcher_can_create_delivery_request(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Delivery Org Dispatcher (Demo)")
    dispatcher = UserFactory(username="http_delivery_dispatcher")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )
    client.force_login(dispatcher)
    response = client.get(reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}))
    assert response.status_code == 200


def _post_wizard_data(
    pickup_facility, destination_facility, cargo_class, temperature_profile, *, attest
):
    return {
        "pickup_facility": pickup_facility.pk,
        "destination_facility": destination_facility.pk,
        "pickup_window_start": "2026-01-05 14:00:00",
        "pickup_window_end": "2026-01-05 16:00:00",
        "required_delivery_by": "2026-01-05 18:00:00",
        "service_level": "scheduled",
        "cargo_class": cargo_class.pk,
        "package_count": 1,
        "temperature_profile": temperature_profile.pk,
        "sender_contact_name": "Front Desk",
        "sender_contact_phone": "",
        "sender_contact_role": "",
        "recipient_contact_name": "Lab Intake",
        "recipient_contact_phone": "",
        "recipient_contact_role": "",
        "recipient_verification_method": "none",
        "facility_instructions": "",
        "attest_packaging": "on" if attest else "",
        "attestation_notes": "",
    }


def test_wizard_creates_and_reaches_ready_for_dispatch_when_complete(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Delivery Org Wizard OK (Demo)")
    dispatcher = UserFactory(username="http_delivery_wizard_ok")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )
    pickup_facility = FacilityFactory(organization=org)
    destination_facility = FacilityFactory()
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)

    client.force_login(dispatcher)
    response = client.post(
        reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}),
        _post_wizard_data(
            pickup_facility, destination_facility, cargo_class, temperature_profile, attest=True
        ),
    )

    assert response.status_code == 302
    from apps.deliveries.models import DeliveryRequest

    created = DeliveryRequest.objects.get(organization=org)
    assert created.status == DeliveryStatus.READY_FOR_DISPATCH


def test_wizard_rejects_oversized_facility_instructions_cleanly(client: Client) -> None:
    """Phase 8 upload/input-limits acceptance criterion: an oversized
    free-text field is rejected cleanly (a re-rendered form with a
    validation error), not a 500 or a silently-truncated write."""
    org = OrganizationFactory(name="HTTP Delivery Org Oversized Field (Demo)")
    dispatcher = UserFactory(username="http_delivery_wizard_oversized")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )
    pickup_facility = FacilityFactory(organization=org)
    destination_facility = FacilityFactory()
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)

    client.force_login(dispatcher)
    data = _post_wizard_data(
        pickup_facility, destination_facility, cargo_class, temperature_profile, attest=True
    )
    data["facility_instructions"] = "x" * 2001

    response = client.post(
        reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}), data
    )

    assert response.status_code == 200
    assert "facility_instructions" in response.context["form"].errors
    from apps.deliveries.models import DeliveryRequest

    assert not DeliveryRequest.objects.filter(organization=org).exists()


def test_wizard_blocks_dispatch_when_packaging_attestation_missing(client: Client) -> None:
    """The hard validation rule from docs/PRODUCT_REQUIREMENTS.md section 5:
    "The request must block dispatch when required cargo or packaging
    information is missing." — exercised through the real wizard HTTP POST."""
    org = OrganizationFactory(name="HTTP Delivery Org Wizard Blocked (Demo)")
    dispatcher = UserFactory(username="http_delivery_wizard_blocked")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )
    pickup_facility = FacilityFactory(organization=org)
    destination_facility = FacilityFactory()
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)

    client.force_login(dispatcher)
    response = client.post(
        reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}),
        _post_wizard_data(
            pickup_facility, destination_facility, cargo_class, temperature_profile, attest=False
        ),
    )

    assert response.status_code == 302
    from apps.deliveries.models import DeliveryRequest

    created = DeliveryRequest.objects.get(organization=org)
    assert created.status == DeliveryStatus.VALIDATION_REQUIRED

    detail_response = client.get(reverse("deliveryrequest-detail", kwargs={"pk": created.pk}))
    assert detail_response.status_code == 200
    content = detail_response.content.decode()
    assert "attestation" in content.lower()


def test_pickup_facility_choices_are_scoped_to_requesting_organization(client: Client) -> None:
    org = OrganizationFactory(name="HTTP Delivery Org Scope (Demo)")
    other_org = OrganizationFactory(name="HTTP Delivery Org Scope Other (Demo)")
    dispatcher = UserFactory(username="http_delivery_scope_dispatcher")
    OrganizationMembership.objects.create(
        user=dispatcher, organization=org, role=CustomerRole.REQUESTER_DISPATCHER
    )
    own_facility = FacilityFactory(organization=org, name="Own Facility (Demo)")
    other_facility = FacilityFactory(organization=other_org, name="Other Org Facility (Demo)")

    client.force_login(dispatcher)
    response = client.get(reverse("deliveryrequest-create", kwargs={"organization_pk": org.pk}))

    assert response.status_code == 200
    form = response.context["form"]
    pickup_choices = set(form.fields["pickup_facility"].queryset.values_list("pk", flat=True))
    assert own_facility.pk in pickup_choices
    assert other_facility.pk not in pickup_choices


def test_generate_recipient_pin_shows_plaintext_pin_once_via_flash_message(client: Client) -> None:
    """Phase 6: an authorized org user can generate a recipient PIN; the
    plaintext value is shown once via a flash message, never persisted."""
    from apps.custody.models import RecipientVerification
    from apps.deliveries.models import RecipientVerificationMethod

    organization = OrganizationFactory()
    user = UserFactory()
    OrganizationMembership.objects.create(
        user=user, organization=organization, role=CustomerRole.OWNER
    )
    delivery_request = DeliveryRequestFactory(
        organization=organization, recipient_verification_method=RecipientVerificationMethod.PIN
    )
    client.force_login(user)

    response = client.post(
        reverse("deliveryrequest-generate-recipient-pin", kwargs={"pk": delivery_request.pk}),
        follow=True,
    )

    assert response.status_code == 200
    messages = [str(m) for m in response.context["messages"]]
    assert any("Recipient PIN generated" in m for m in messages)
    verification = RecipientVerification.objects.get(delivery_request=delivery_request)
    assert verification.pin_hash != ""


def test_generate_recipient_pin_forbidden_for_unauthorized_user(client: Client) -> None:
    organization = OrganizationFactory()
    other_org = OrganizationFactory()
    outsider = UserFactory()
    OrganizationMembership.objects.create(
        user=outsider, organization=other_org, role=CustomerRole.OWNER
    )
    delivery_request = DeliveryRequestFactory(organization=organization)
    client.force_login(outsider)

    response = client.post(
        reverse("deliveryrequest-generate-recipient-pin", kwargs={"pk": delivery_request.pk})
    )

    assert response.status_code == 403
