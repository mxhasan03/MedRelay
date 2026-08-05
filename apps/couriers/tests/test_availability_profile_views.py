"""HTTP-level tests for the two new courier PWA screens: availability
(GET current state / POST update) and profile (read-only onboarding
display) — including the hard "a courier only ever sees/edits their own
data" cross-courier ownership tests and the idempotency
duplicate-submission behavior established by every other courier
state-mutating endpoint (apps.couriers.tests.test_views).
"""

from __future__ import annotations

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.couriers.models import (
    CourierAvailability,
    CourierCredentialStatus,
    CourierCredentialType,
)
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    EquipmentFactory,
    TrainingRecordFactory,
    VehicleFactory,
)
from apps.facilities.tests.factories import ServiceZoneFactory

pytestmark = pytest.mark.django_db


# --- availability GET --------------------------------------------------


def test_availability_view_requires_login(client: Client) -> None:
    response = client.get(reverse("courier-availability"))
    assert response.status_code == 302


def test_availability_view_shows_current_courier_availability(client: Client) -> None:
    zone = ServiceZoneFactory()
    availability = CourierAvailabilityFactory(
        is_online=True, current_service_zone=zone, max_concurrent_deliveries=2
    )
    client.force_login(availability.courier.user)

    response = client.get(reverse("courier-availability"))

    assert response.status_code == 200
    assert response.context["availability"].pk == availability.pk
    assert zone in response.context["service_zones"]


def test_availability_view_creates_row_on_first_visit_if_missing() -> None:
    """A brand-new courier with no CourierAvailability row yet still gets a
    working screen (get_or_create), not a 500/404."""
    courier = CourierProfileFactory()
    assert not CourierAvailability.objects.filter(courier=courier).exists()
    client = Client()
    client.force_login(courier.user)

    response = client.get(reverse("courier-availability"))

    assert response.status_code == 200
    assert CourierAvailability.objects.filter(courier=courier).exists()


# --- availability POST update -------------------------------------------


def test_availability_update_success(client: Client) -> None:
    zone = ServiceZoneFactory()
    availability = CourierAvailabilityFactory(is_online=False)
    client.force_login(availability.courier.user)

    response = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps(
            {
                "is_online": True,
                "current_service_zone_id": zone.pk,
                "shift_start": "08:00",
                "shift_end": "16:00",
                "max_concurrent_deliveries": 4,
            }
        ),
        HTTP_IDEMPOTENCY_KEY="avail-key-1",
    )

    assert response.status_code == 200
    availability.refresh_from_db()
    assert availability.is_online is True
    assert availability.current_service_zone_id == zone.pk
    assert availability.shift_start == datetime.time(8, 0)
    assert availability.shift_end == datetime.time(16, 0)
    assert availability.max_concurrent_deliveries == 4


def test_availability_update_requires_idempotency_key(client: Client) -> None:
    availability = CourierAvailabilityFactory(is_online=False)
    client.force_login(availability.courier.user)

    response = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps({"is_online": True}),
    )

    assert response.status_code == 400
    availability.refresh_from_db()
    assert availability.is_online is False


def test_availability_update_only_affects_the_logged_in_couriers_own_row(client: Client) -> None:
    """Cross-courier ownership: courier A's update must never touch courier
    B's CourierAvailability row — the courier is always derived from
    request.user.courier_profile, never a client-supplied id."""
    availability_a = CourierAvailabilityFactory(is_online=False)
    availability_b = CourierAvailabilityFactory(is_online=False)
    client.force_login(availability_a.courier.user)

    response = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps({"is_online": True}),
        HTTP_IDEMPOTENCY_KEY="avail-key-cross",
    )

    assert response.status_code == 200
    availability_a.refresh_from_db()
    availability_b.refresh_from_db()
    assert availability_a.is_online is True
    assert availability_b.is_online is False


def test_availability_update_rejects_invalid_zone(client: Client) -> None:
    availability = CourierAvailabilityFactory(is_online=False)
    client.force_login(availability.courier.user)

    response = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps({"is_online": True, "current_service_zone_id": 999999}),
        HTTP_IDEMPOTENCY_KEY="avail-key-bad-zone",
    )

    assert response.status_code == 400
    assert "error" in response.json()


def test_availability_update_same_idempotency_key_twice_does_not_reapply(
    client: Client,
) -> None:
    """Hard acceptance criterion (matches every other courier action
    endpoint): reruns/retries do not duplicate/re-apply events. The second
    call with the same key must return the FIRST call's stored result, even
    though its own body asked for a different value."""
    availability = CourierAvailabilityFactory(is_online=False, max_concurrent_deliveries=1)
    client.force_login(availability.courier.user)

    first = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps({"is_online": True, "max_concurrent_deliveries": 2}),
        HTTP_IDEMPOTENCY_KEY="dup-avail-key",
    )
    second = client.post(
        reverse("courier-availability-update"),
        content_type="application/json",
        data=json.dumps({"is_online": False, "max_concurrent_deliveries": 99}),
        HTTP_IDEMPOTENCY_KEY="dup-avail-key",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    availability.refresh_from_db()
    assert availability.is_online is True
    assert availability.max_concurrent_deliveries == 2


# --- profile view --------------------------------------------------------


def test_profile_view_requires_login(client: Client) -> None:
    response = client.get(reverse("courier-profile"))
    assert response.status_code == 302


def test_profile_view_shows_only_the_logged_in_couriers_own_data(client: Client) -> None:
    """Cross-courier ownership: courier A must never see courier B's
    vehicles/equipment/cargo authorizations/training/credentials."""
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()

    vehicle_a = VehicleFactory(courier=courier_a)
    vehicle_b = VehicleFactory(courier=courier_b)
    equipment_a = EquipmentFactory(courier=courier_a)
    equipment_b = EquipmentFactory(courier=courier_b)
    training_a = TrainingRecordFactory(courier=courier_a)
    training_b = TrainingRecordFactory(courier=courier_b)
    credential_a = CourierCredentialFactory(courier=courier_a)
    credential_b = CourierCredentialFactory(courier=courier_b)
    cargo_auth_a = CargoAuthorizationFactory(courier=courier_a)
    cargo_auth_b = CargoAuthorizationFactory(courier=courier_b)

    client = Client()
    client.force_login(courier_a.user)

    response = client.get(reverse("courier-profile"))

    assert response.status_code == 200
    assert response.context["courier"].pk == courier_a.pk

    vehicle_ids = {v.pk for v in response.context["vehicles"]}
    assert vehicle_ids == {vehicle_a.pk}
    assert vehicle_b.pk not in vehicle_ids

    equipment_ids = {e.pk for e in response.context["equipment"]}
    assert equipment_ids == {equipment_a.pk}
    assert equipment_b.pk not in equipment_ids

    training_ids = {t.pk for t in response.context["training_records"]}
    assert training_ids == {training_a.pk}
    assert training_b.pk not in training_ids

    credential_ids = {c.pk for c in response.context["credentials"]}
    assert credential_ids == {credential_a.pk}
    assert credential_b.pk not in credential_ids

    cargo_auth_ids = {ca.pk for ca in response.context["cargo_authorizations"]}
    assert cargo_auth_ids == {cargo_auth_a.pk}
    assert cargo_auth_b.pk not in cargo_auth_ids


def test_profile_view_shows_credential_expiration_summary_for_this_courier_only(
    client: Client,
) -> None:
    today = datetime.date.today()
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()
    expired_a = CourierCredentialFactory(
        courier=courier_a,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=3),
    )
    CourierCredentialFactory(
        courier=courier_b,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=3),
    )
    client.force_login(courier_a.user)

    response = client.get(reverse("courier-profile"))

    assert response.status_code == 200
    summary = response.context["credential_summary"]
    assert [c.pk for c in summary.expired] == [expired_a.pk]


def test_profile_view_forbidden_for_non_courier(client: Client) -> None:
    client.force_login(UserFactory())
    response = client.get(reverse("courier-profile"))
    assert response.status_code == 403
