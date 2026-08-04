"""HTTP-level tests for the Phase 6 courier PWA endpoints: pickup proof
capture, package condition check, complete-delivery (recipient proof + the
AT_DESTINATION -> DELIVERED transition), and courier-initiated incident
reporting.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackageFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    VehicleFactory,
)
from apps.custody.services import generate_recipient_pin
from apps.deliveries.models import DeliveryStatus, RecipientVerificationMethod, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.services import assign_delivery

pytestmark = pytest.mark.django_db


def _assigned_delivery_and_courier(
    recipient_verification_method: str = RecipientVerificationMethod.NONE,
):
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
        recipient_verification_method=recipient_verification_method,
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)

    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True)

    assign_delivery(delivery_request.pk, courier.pk, None)
    delivery_request.refresh_from_db()
    return delivery_request, courier


def _advance_to_at_destination(delivery_request):
    for to_status in (
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
        DeliveryStatus.AT_DESTINATION,
    ):
        transition_delivery_request(delivery_request, to_status, actor=None)
    return delivery_request


# --- Pickup proof capture ----------------------------------------------------


def test_capture_pickup_proof_success(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-pickup-proof", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"sender_name": "Alex Sender", "typed_signature_name": "Alex Sender"}),
        HTTP_IDEMPOTENCY_KEY="pickup-proof-key-1",
    )

    assert response.status_code == 201
    assert hasattr(delivery_request, "proof_of_pickup") or delivery_request.proof_of_pickup


def test_capture_pickup_proof_twice_conflicts(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)
    url = reverse("courier-pickup-proof", kwargs={"pk": delivery_request.pk})

    client.post(
        url,
        content_type="application/json",
        data=json.dumps({"typed_signature_name": "A"}),
        HTTP_IDEMPOTENCY_KEY="key-a",
    )
    response = client.post(
        url,
        content_type="application/json",
        data=json.dumps({"typed_signature_name": "B"}),
        HTTP_IDEMPOTENCY_KEY="key-b",
    )
    assert response.status_code == 409


def test_capture_pickup_proof_rejects_wrong_courier(client: Client) -> None:
    delivery_request, _courier = _assigned_delivery_and_courier()
    other = CourierProfileFactory()
    client.force_login(other.user)

    response = client.post(
        reverse("courier-pickup-proof", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"typed_signature_name": "X"}),
        HTTP_IDEMPOTENCY_KEY="key-wrong",
    )
    assert response.status_code == 403


# --- Condition check ---------------------------------------------------------


def test_capture_condition_check_success(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    package = PackageFactory(delivery_request=delivery_request, sequence_number=1)
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-condition-check", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps(
            {
                "package_id": package.pk,
                "stage": "pickup",
                "seal_status": "intact",
                "physical_damage_observed": False,
            }
        ),
        HTTP_IDEMPOTENCY_KEY="condition-key-1",
    )

    assert response.status_code == 201
    assert response.json()["has_any_concern"] is False


# --- Complete delivery (recipient proof + DELIVERED transition) -------------


def test_complete_delivery_with_no_verification_method_succeeds(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier(RecipientVerificationMethod.NONE)
    _advance_to_at_destination(delivery_request)
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-delivery-complete", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps(
            {"delivered_to_name": "Jordan Recipient", "typed_signature_name": "Jordan"}
        ),
        HTTP_IDEMPOTENCY_KEY="complete-key-1",
    )

    assert response.status_code == 200
    assert response.json()["status"] == DeliveryStatus.DELIVERED
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.DELIVERED


def test_complete_delivery_with_pin_method_requires_correct_pin(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier(RecipientVerificationMethod.PIN)
    _advance_to_at_destination(delivery_request)
    _verification, plaintext_pin = generate_recipient_pin(delivery_request)
    client.force_login(courier.user)
    url = reverse("courier-delivery-complete", kwargs={"pk": delivery_request.pk})

    wrong = client.post(
        url,
        content_type="application/json",
        data=json.dumps({"pin": "0000", "typed_signature_name": "Jordan"}),
        HTTP_IDEMPOTENCY_KEY="complete-wrong-pin",
    )
    assert wrong.status_code == 422
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.AT_DESTINATION

    correct = client.post(
        url,
        content_type="application/json",
        data=json.dumps({"pin": plaintext_pin, "typed_signature_name": "Jordan"}),
        HTTP_IDEMPOTENCY_KEY="complete-correct-pin",
    )
    assert correct.status_code == 200
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.DELIVERED


def test_complete_delivery_same_idempotency_key_twice_does_not_duplicate(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier(RecipientVerificationMethod.NONE)
    _advance_to_at_destination(delivery_request)
    client.force_login(courier.user)
    url = reverse("courier-delivery-complete", kwargs={"pk": delivery_request.pk})
    body = json.dumps({"delivered_to_name": "Jordan", "typed_signature_name": "Jordan"})

    first = client.post(
        url, content_type="application/json", data=body, HTTP_IDEMPOTENCY_KEY="dup-complete-key"
    )
    second = client.post(
        url, content_type="application/json", data=body, HTTP_IDEMPOTENCY_KEY="dup-complete-key"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    from apps.custody.models import ProofOfDelivery

    assert ProofOfDelivery.objects.filter(delivery_request=delivery_request).count() == 1


# --- Courier-initiated incident report --------------------------------------


def test_report_incident_with_severe_severity_places_delivery_on_hold(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    _advance_to_at_destination(delivery_request)
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-report-incident", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps(
            {
                "category": "broken_seal",
                "severity": "severe",
                "summary": "Seal was broken on arrival.",
            }
        ),
        HTTP_IDEMPOTENCY_KEY="incident-key-1",
    )

    assert response.status_code == 201
    assert response.json()["placed_on_hold"] is True
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.INCIDENT_HOLD


# --- Upload/input limits (Phase 8) --------------------------------------------


def test_pickup_proof_rejects_an_oversized_signature_with_a_clean_413(client: Client) -> None:
    from apps.custody.models import ProofOfPickup
    from apps.custody.validators import MAX_SIGNATURE_DATA_URL_LENGTH

    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)
    oversized = "data:image/png;base64," + "a" * MAX_SIGNATURE_DATA_URL_LENGTH

    response = client.post(
        reverse("courier-pickup-proof", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"signature_data_url": oversized}),
        HTTP_IDEMPOTENCY_KEY="oversized-signature-key",
    )

    assert response.status_code == 413
    assert "too large" in response.json()["error"]
    assert ProofOfPickup.objects.filter(delivery_request=delivery_request).exists() is False


def test_report_incident_rejects_an_oversized_summary_with_a_clean_400(client: Client) -> None:
    from apps.incidents.models import Incident

    delivery_request, courier = _assigned_delivery_and_courier()
    _advance_to_at_destination(delivery_request)
    client.force_login(courier.user)
    oversized_summary = "x" * (Incident.SUMMARY_MAX_LENGTH + 1)

    response = client.post(
        reverse("courier-report-incident", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps(
            {"category": "broken_seal", "severity": "minor", "summary": oversized_summary}
        ),
        HTTP_IDEMPOTENCY_KEY="oversized-summary-key",
    )

    assert response.status_code == 400
    assert "too long" in response.json()["error"]
    assert Incident.objects.filter(delivery_request=delivery_request).exists() is False
