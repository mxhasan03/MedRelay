"""HTTP-level tests for the courier PWA views: job offer accept/decline,
active delivery status advancement, and package scan — including the hard
"reruns/retries do not duplicate events" acceptance criterion for every one
of these state-mutating endpoints.
"""

from __future__ import annotations

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackageFactory,
    PackageIdentifierFactory,
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
from apps.deliveries.models import DeliveryStatus, DeliveryStatusTransition, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.models import DeliveryAssignment, JobOfferStatus
from apps.dispatch.services import assign_delivery, offer_delivery

pytestmark = pytest.mark.django_db


def _ready_delivery_with_cargo_class():
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    return delivery_request, cargo_class


def _eligible_courier(cargo_class):
    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True)
    return courier


def _assigned_delivery_and_courier():
    delivery_request, cargo_class = _ready_delivery_with_cargo_class()
    courier = _eligible_courier(cargo_class)
    assign_delivery(delivery_request.pk, courier.pk, None)
    delivery_request.refresh_from_db()
    return delivery_request, courier


def _offered_delivery_and_courier():
    delivery_request, cargo_class = _ready_delivery_with_cargo_class()
    courier = _eligible_courier(cargo_class)
    expires_at = timezone.now() + datetime.timedelta(minutes=30)
    (offer,) = offer_delivery(delivery_request.pk, [courier.pk], expires_at)
    return offer, courier


# --- courier home / job offer list ------------------------------------------


def test_courier_home_requires_login(client: Client) -> None:
    response = client.get(reverse("courier-home"))
    assert response.status_code == 302


def test_courier_home_forbidden_for_non_courier(client: Client) -> None:
    client.force_login(UserFactory())
    response = client.get(reverse("courier-home"))
    assert response.status_code == 403


def test_job_offer_list_only_shows_this_couriers_own_offers(client: Client) -> None:
    offer_a, courier_a = _offered_delivery_and_courier()
    offer_b, _courier_b = _offered_delivery_and_courier()
    client.force_login(courier_a.user)

    response = client.get(reverse("courier-job-offer-list"))

    assert response.status_code == 200
    offer_ids = {offer.pk for offer in response.context["offers"]}
    assert offer_a.pk in offer_ids
    assert offer_b.pk not in offer_ids


# --- job offer accept -------------------------------------------------------


def test_job_offer_accept_success(client: Client) -> None:
    offer, courier = _offered_delivery_and_courier()
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="accept-key-1",
    )

    assert response.status_code == 201
    offer.refresh_from_db()
    assert offer.status == JobOfferStatus.ACCEPTED
    assert DeliveryAssignment.objects.filter(
        delivery_request=offer.delivery_request, courier=courier
    ).exists()


def test_job_offer_accept_requires_idempotency_key(client: Client) -> None:
    offer, courier = _offered_delivery_and_courier()
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({}),
    )

    assert response.status_code == 400
    offer.refresh_from_db()
    assert offer.status == JobOfferStatus.OFFERED


def test_job_offer_accept_rejects_wrong_courier(client: Client) -> None:
    offer, _courier = _offered_delivery_and_courier()
    other = CourierProfileFactory()
    client.force_login(other.user)

    response = client.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="accept-key-wrong",
    )

    assert response.status_code == 403


def test_job_offer_accept_same_idempotency_key_twice_does_not_duplicate(client: Client) -> None:
    """Hard acceptance criterion: reruns/retries do not duplicate events."""
    offer, courier = _offered_delivery_and_courier()
    client.force_login(courier.user)

    first = client.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="dup-accept-key",
    )
    second = client.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="dup-accept-key",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert DeliveryAssignment.objects.filter(delivery_request=offer.delivery_request).count() == 1


# --- job offer decline -------------------------------------------------------


def test_job_offer_decline_success_records_reason(client: Client) -> None:
    offer, courier = _offered_delivery_and_courier()
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-job-offer-decline", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({"reason": "Cargo seal looked compromised."}),
        HTTP_IDEMPOTENCY_KEY="decline-key-1",
    )

    assert response.status_code == 200
    offer.refresh_from_db()
    assert offer.status == JobOfferStatus.DECLINED
    assert offer.decline_reason == "Cargo seal looked compromised."


def test_job_offer_decline_does_not_block_other_couriers_offer(client: Client) -> None:
    delivery_request, cargo_class = _ready_delivery_with_cargo_class()
    courier_a = _eligible_courier(cargo_class)
    courier_b = _eligible_courier(cargo_class)
    expires_at = timezone.now() + datetime.timedelta(minutes=30)
    offers = offer_delivery(delivery_request.pk, [courier_a.pk, courier_b.pk], expires_at)
    offer_a = next(o for o in offers if o.courier_id == courier_a.pk)
    offer_b = next(o for o in offers if o.courier_id == courier_b.pk)

    client_a = Client()
    client_a.force_login(courier_a.user)
    response = client_a.post(
        reverse("courier-job-offer-decline", kwargs={"pk": offer_a.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="decline-key-a",
    )
    assert response.status_code == 200

    client_b = Client()
    client_b.force_login(courier_b.user)
    accept_response = client_b.post(
        reverse("courier-job-offer-accept", kwargs={"pk": offer_b.pk}),
        content_type="application/json",
        data=json.dumps({}),
        HTTP_IDEMPOTENCY_KEY="accept-key-b",
    )
    assert accept_response.status_code == 201


def test_job_offer_decline_same_idempotency_key_twice_does_not_duplicate(client: Client) -> None:
    offer, courier = _offered_delivery_and_courier()
    client.force_login(courier.user)

    first = client.post(
        reverse("courier-job-offer-decline", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({"reason": "first"}),
        HTTP_IDEMPOTENCY_KEY="dup-decline-key",
    )
    second = client.post(
        reverse("courier-job-offer-decline", kwargs={"pk": offer.pk}),
        content_type="application/json",
        data=json.dumps({"reason": "different-should-be-ignored"}),
        HTTP_IDEMPOTENCY_KEY="dup-decline-key",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    offer.refresh_from_db()
    assert offer.decline_reason == "first"


# --- active delivery / status advance ---------------------------------------


def test_active_delivery_view_forbidden_for_a_different_courier(client: Client) -> None:
    delivery_request, _courier = _assigned_delivery_and_courier()
    other = CourierProfileFactory()
    client.force_login(other.user)

    response = client.get(reverse("courier-active-delivery", kwargs={"pk": delivery_request.pk}))

    assert response.status_code == 403


def test_active_delivery_view_shows_next_status_for_assigned_courier(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)

    response = client.get(reverse("courier-active-delivery", kwargs={"pk": delivery_request.pk}))

    assert response.status_code == 200
    assert response.context["next_status"] == DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP


def test_delivery_status_advance_success(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-delivery-advance", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"to_status": DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP}),
        HTTP_IDEMPOTENCY_KEY="advance-key-1",
    )

    assert response.status_code == 200
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP


def test_delivery_status_advance_rejects_wrong_courier(client: Client) -> None:
    delivery_request, _courier = _assigned_delivery_and_courier()
    other = CourierProfileFactory()
    client.force_login(other.user)

    response = client.post(
        reverse("courier-delivery-advance", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"to_status": DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP}),
        HTTP_IDEMPOTENCY_KEY="advance-key-wrong",
    )

    assert response.status_code == 403
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED


def test_delivery_status_advance_same_idempotency_key_twice_does_not_duplicate(
    client: Client,
) -> None:
    """Hard acceptance criterion: reruns/retries do not duplicate events —
    only one DeliveryStatusTransition row for this transition, not two."""
    delivery_request, courier = _assigned_delivery_and_courier()
    client.force_login(courier.user)
    body = json.dumps({"to_status": DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP})

    first = client.post(
        reverse("courier-delivery-advance", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=body,
        HTTP_IDEMPOTENCY_KEY="dup-advance-key",
    )
    second = client.post(
        reverse("courier-delivery-advance", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=body,
        HTTP_IDEMPOTENCY_KEY="dup-advance-key",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    transitions = DeliveryStatusTransition.objects.filter(
        delivery_request=delivery_request,
        to_status=DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
    )
    assert transitions.count() == 1


# --- package scan ------------------------------------------------------------


def test_package_scan_correct_code_confirms_pickup(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    package = PackageFactory(delivery_request=delivery_request, sequence_number=1)
    identifier = PackageIdentifierFactory(package=package)
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-package-scan", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"code": identifier.code}),
        HTTP_IDEMPOTENCY_KEY="scan-key-1",
    )

    assert response.status_code == 200
    package.refresh_from_db()
    assert package.scanned_at is not None


def test_package_scan_wrong_code_is_rejected_with_clear_error(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    PackageFactory(delivery_request=delivery_request)
    client.force_login(courier.user)

    response = client.post(
        reverse("courier-package-scan", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=json.dumps({"code": "PKG-DOESNOTEXIST"}),
        HTTP_IDEMPOTENCY_KEY="scan-key-bad",
    )

    assert response.status_code == 422
    assert "error" in response.json()


def test_package_scan_same_idempotency_key_twice_does_not_duplicate(client: Client) -> None:
    delivery_request, courier = _assigned_delivery_and_courier()
    package = PackageFactory(delivery_request=delivery_request)
    identifier = PackageIdentifierFactory(package=package)
    client.force_login(courier.user)
    body = json.dumps({"code": identifier.code})

    first = client.post(
        reverse("courier-package-scan", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=body,
        HTTP_IDEMPOTENCY_KEY="dup-scan-key",
    )
    second = client.post(
        reverse("courier-package-scan", kwargs={"pk": delivery_request.pk}),
        content_type="application/json",
        data=body,
        HTTP_IDEMPOTENCY_KEY="dup-scan-key",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
