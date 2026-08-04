"""Tests for apps.deliveries.services: creation ("wizard" backend), submission,
cancellation, optimistic concurrency, and the recurring-route generation stub."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    TemperatureProfileFactory,
)
from apps.deliveries.exceptions import DeliveryRequestQuotaExceededError, StaleDeliveryRequestError
from apps.deliveries.models import DeliveryStatus, RecipientVerificationMethod, StopType
from apps.deliveries.services import (
    cancel_delivery_request,
    create_delivery_request,
    generate_delivery_requests_for_recurring_route,
    submit_delivery_request,
    update_delivery_request_with_version_check,
)
from apps.deliveries.tests.factories import RecurringRouteFactory
from apps.facilities.tests.factories import FacilityFactory
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def _create_full_delivery_request(*, attest_packaging: bool, cargo_code=CargoClassCode.CLASS_2):
    organization = OrganizationFactory()
    pickup_facility = FacilityFactory(
        organization=organization, latitude="40.75", longitude="-73.98"
    )
    destination_facility = FacilityFactory(latitude="40.76", longitude="-73.99")
    cargo_class = CargoClassFactory(code=cargo_code)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    start = datetime.datetime(2026, 1, 5, 14, 0, tzinfo=datetime.UTC)

    return create_delivery_request(
        organization=organization,
        created_by=None,
        service_level="scheduled",
        pickup_facility=pickup_facility,
        destination_facility=destination_facility,
        pickup_window_start=start,
        pickup_window_end=start + datetime.timedelta(hours=2),
        required_delivery_by=start + datetime.timedelta(hours=4),
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
        package_count=2,
        sender_contact_name="Front Desk",
        recipient_contact_name="Lab Intake",
        recipient_verification_method=RecipientVerificationMethod.NONE,
        attest_packaging=attest_packaging,
    )


def test_create_delivery_request_builds_stops_and_packages() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)

    assert delivery_request.status == DeliveryStatus.DRAFT
    assert delivery_request.stops.count() == 2
    assert {s.stop_type for s in delivery_request.stops.all()} == {
        StopType.PICKUP,
        StopType.DESTINATION,
    }
    assert delivery_request.packages.count() == 2
    for package in delivery_request.packages.all():
        assert package.identifier is not None
    assert delivery_request.has_packaging_attestation is True


def test_create_delivery_request_without_attestation_has_none() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=False)
    assert delivery_request.has_packaging_attestation is False


def test_submit_delivery_request_reaches_ready_for_dispatch_when_complete() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)
    submit_delivery_request(delivery_request, actor=None)
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    assert delivery_request.estimated_price is not None
    assert hasattr(delivery_request, "quote")


def _create_delivery_request_for_org(organization, *, attest_packaging: bool = True):
    """Like `_create_full_delivery_request`, but for a *caller-supplied*
    organization (needed to test the Phase 9 per-organization quota, which
    `_create_full_delivery_request` can't exercise itself since it builds a
    brand-new `Organization` on every call)."""
    pickup_facility = FacilityFactory(
        organization=organization, latitude="40.75", longitude="-73.98"
    )
    destination_facility = FacilityFactory(latitude="40.76", longitude="-73.99")
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    start = datetime.datetime(2026, 1, 5, 14, 0, tzinfo=datetime.UTC)

    return create_delivery_request(
        organization=organization,
        created_by=None,
        service_level="scheduled",
        pickup_facility=pickup_facility,
        destination_facility=destination_facility,
        pickup_window_start=start,
        pickup_window_end=start + datetime.timedelta(hours=2),
        required_delivery_by=start + datetime.timedelta(hours=4),
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
        package_count=1,
        sender_contact_name="Front Desk",
        recipient_contact_name="Lab Intake",
        recipient_verification_method=RecipientVerificationMethod.NONE,
        attest_packaging=attest_packaging,
    )


@override_settings(DEMO_MAX_DELIVERY_REQUESTS_PER_ORG=2)
def test_create_delivery_request_raises_once_org_quota_is_reached() -> None:
    """Phase 9 abuse safeguard (docs/CURRENT_STATUS.md 'Phase 9' — quota/abuse
    safeguards): the third delivery request for the same organization is
    rejected with a clear error once the (here, deliberately lowered) cap is
    reached, and no row is created for the rejected attempt."""
    organization = OrganizationFactory()
    _create_delivery_request_for_org(organization)
    _create_delivery_request_for_org(organization)

    with pytest.raises(DeliveryRequestQuotaExceededError, match="demo delivery-request cap"):
        _create_delivery_request_for_org(organization)

    assert organization.delivery_requests.count() == 2


@override_settings(DEMO_MAX_DELIVERY_REQUESTS_PER_ORG=1)
def test_create_delivery_request_quota_is_per_organization_not_global() -> None:
    """A different organization's own requests must not count against this
    one's cap — the quota is scoped per-tenant, matching every other
    tenant-scoping rule in this codebase (CLAUDE.md 'Multi-tenancy')."""
    first_org = OrganizationFactory()
    second_org = OrganizationFactory()
    _create_delivery_request_for_org(first_org)

    # second_org has made zero requests yet, so it should still succeed even
    # though the global cap (1) has already been reached by first_org.
    _create_delivery_request_for_org(second_org)

    with pytest.raises(DeliveryRequestQuotaExceededError):
        _create_delivery_request_for_org(first_org)


@override_settings(DEMO_MAX_DELIVERY_REQUESTS_PER_ORG=None)
def test_create_delivery_request_quota_check_is_a_no_op_when_setting_is_none() -> None:
    """A `None` cap (not set, or explicitly disabled) must never block
    creation — see `_enforce_delivery_request_quota`'s defensive default."""
    organization = OrganizationFactory()
    for _ in range(3):
        _create_delivery_request_for_org(organization)


def test_submit_delivery_request_stays_at_validation_required_without_attestation() -> None:
    """This is the end-to-end version of the Phase 2 acceptance criterion: a
    delivery request missing its required packaging attestation is blocked
    from reaching READY_FOR_DISPATCH, all the way through the real
    create+submit service path (not just the lower-level state machine
    function)."""
    delivery_request = _create_full_delivery_request(attest_packaging=False)
    submit_delivery_request(delivery_request, actor=None)
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.VALIDATION_REQUIRED
    assert delivery_request.estimated_price is None

    # Submission that stopped short of dispatch still recorded its earlier
    # transitions (append-only), it did not roll everything back.
    statuses = list(delivery_request.status_transitions.values_list("to_status", flat=True))
    assert statuses == [DeliveryStatus.SUBMITTED, DeliveryStatus.VALIDATION_REQUIRED]


def test_cancel_delivery_request_transitions_to_cancelled() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)
    cancel_delivery_request(delivery_request, actor=None, reason="test cancellation")
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.CANCELLED
    last_transition = delivery_request.status_transitions.last()
    assert last_transition.reason == "test cancellation"


def test_update_with_version_check_succeeds_with_matching_version() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)
    updated = update_delivery_request_with_version_check(
        delivery_request,
        expected_version=delivery_request.version,
        facility_instructions="Ring the bell.",
    )
    assert updated.facility_instructions == "Ring the bell."
    assert updated.version == 2


def test_update_with_version_check_raises_on_stale_version() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)
    stale_version = delivery_request.version

    # Someone else updates the row first (version bumps behind our back).
    update_delivery_request_with_version_check(
        delivery_request, expected_version=stale_version, facility_instructions="First writer."
    )

    with pytest.raises(StaleDeliveryRequestError):
        update_delivery_request_with_version_check(
            delivery_request,
            expected_version=stale_version,
            facility_instructions="Second writer, stale.",
        )


def test_update_with_version_check_rejects_prohibited_keywords() -> None:
    delivery_request = _create_full_delivery_request(attest_packaging=True)
    with pytest.raises(ValidationError):
        update_delivery_request_with_version_check(
            delivery_request,
            expected_version=delivery_request.version,
            facility_instructions="Please treat this as a controlled substance shipment.",
        )


def test_generate_delivery_requests_for_recurring_route_is_a_documented_stub() -> None:
    route = RecurringRouteFactory()
    with pytest.raises(NotImplementedError):
        generate_delivery_requests_for_recurring_route(route, datetime.date(2026, 1, 5))
