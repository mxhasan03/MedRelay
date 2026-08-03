"""Tests for the hard-eligibility engine (`apps.couriers.eligibility`).

Covers the Phase 3 acceptance criteria:

- for every implemented hard filter, a test proving a courier failing that
  specific condition is excluded, and a positive test proving an otherwise-
  fully-qualified courier passes.
- a Hypothesis property test: for many random combinations of the nine
  implemented pass/fail conditions, `eligible` is true iff every individual
  hard filter passes, and the specific set of reported failure-reason codes
  matches exactly (including the one documented interaction: the
  "temperature capability" check is only meaningful when a cargo
  authorization exists at all).
- "no unauthorized job appears to courier": `eligible_deliveries_for`
  excludes a `READY_FOR_DISPATCH` delivery the courier lacks an
  authorization/temperature-capability for, while still including one the
  courier is fully eligible for.
"""

from __future__ import annotations

import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.eligibility import (
    ACCOUNT_NOT_ACTIVE,
    CAPACITY_EXCEEDED,
    CARGO_AUTHORIZATION_MISSING,
    CREDENTIAL_EXPIRED,
    FACILITY_RESTRICTION_NOT_MET,
    OUTSIDE_SERVICE_ZONE,
    SLA_FEASIBILITY_NOT_EVALUATED,
    TEMPERATURE_CAPABILITY_MISSING,
    UNAVAILABLE,
    VEHICLE_EQUIPMENT_INCOMPATIBLE,
    check_courier_eligibility,
    eligible_couriers_for,
    eligible_deliveries_for,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    EquipmentFactory,
    VehicleFactory,
)
from apps.deliveries.models import DeliveryStatus, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory

pytestmark = pytest.mark.django_db

FUTURE_EXPIRY = datetime.date(2027, 1, 1)
PAST_EXPIRY = datetime.date(2020, 1, 1)


def _make_ready_for_dispatch_delivery(
    *,
    temperature_code: str = TemperatureProfileCode.REFRIGERATED,
    pickup_service_zone=None,
    pickup_verification_requirements: str = "",
):
    """Build a real `READY_FOR_DISPATCH` delivery request: a Class 2 cargo
    classification with a policy that allows both ambient and refrigerated,
    a pickup/destination stop, and a packaging attestation — everything
    `apps.deliveries.state_machine.validate_ready_for_dispatch` requires.
    """
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=temperature_code)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    pickup_facility = FacilityFactory(
        service_zone=pickup_service_zone,
        verification_requirements=pickup_verification_requirements,
    )
    destination_facility = FacilityFactory()
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.PICKUP,
        sequence=1,
        facility=pickup_facility,
    )
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.DESTINATION,
        sequence=2,
        facility=destination_facility,
    )
    PackagingAttestationFactory(delivery_request=delivery_request)

    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    return delivery_request, cargo_class


def _make_fully_eligible_courier(cargo_class, zone):
    """A courier passing every Phase 3 hard filter for a REFRIGERATED delivery
    against `cargo_class`, with `zone` as both its current service zone and
    the delivery's pickup facility's zone.
    """
    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        expires_on=FUTURE_EXPIRY,
    )
    CourierCredentialFactory(
        courier=courier, credential_type=CourierCredentialType.INSURANCE, expires_on=FUTURE_EXPIRY
    )
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class, supports_refrigeration=True)
    VehicleFactory(courier=courier, supports_refrigeration=True)
    CourierAvailabilityFactory(
        courier=courier, is_online=True, current_service_zone=zone, max_concurrent_deliveries=1
    )
    return courier


# --- Positive baseline --------------------------------------------------------


def test_fully_qualified_courier_is_eligible_with_no_reasons() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is True
    assert result.hard_failure_reasons == ()
    assert result.sla_feasibility == SLA_FEASIBILITY_NOT_EVALUATED


# --- Per-filter negative tests -------------------------------------------------


def test_account_not_active_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.status = CourierStatus.SUSPENDED
    courier.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert ACCOUNT_NOT_ACTIVE in {r.code for r in result.hard_failure_reasons}


def test_expired_credential_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.credentials.filter(credential_type=CourierCredentialType.DRIVER_LICENSE).update(
        expires_on=PAST_EXPIRY
    )

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert CREDENTIAL_EXPIRED in {r.code for r in result.hard_failure_reasons}


def test_missing_required_credential_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.credentials.filter(credential_type=CourierCredentialType.INSURANCE).delete()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert CREDENTIAL_EXPIRED in {r.code for r in result.hard_failure_reasons}


def test_missing_cargo_authorization_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.cargo_authorizations.all().delete()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert CARGO_AUTHORIZATION_MISSING in {r.code for r in result.hard_failure_reasons}


def test_missing_temperature_capability_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.cargo_authorizations.update(supports_refrigeration=False)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert TEMPERATURE_CAPABILITY_MISSING in {r.code for r in result.hard_failure_reasons}


def test_ambient_delivery_does_not_require_refrigeration_capability() -> None:
    """Sanity check on the temperature-capability filter: an AMBIENT delivery
    never triggers TEMPERATURE_CAPABILITY_MISSING even if the courier's
    authorization doesn't support refrigeration."""
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(
        temperature_code=TemperatureProfileCode.AMBIENT, pickup_service_zone=zone
    )
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.cargo_authorizations.update(supports_refrigeration=False)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is True


def test_incompatible_vehicle_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.vehicles.update(supports_refrigeration=False)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert VEHICLE_EQUIPMENT_INCOMPATIBLE in {r.code for r in result.hard_failure_reasons}


def test_no_active_vehicle_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(
        temperature_code=TemperatureProfileCode.AMBIENT, pickup_service_zone=zone
    )
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.vehicles.all().delete()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert VEHICLE_EQUIPMENT_INCOMPATIBLE in {r.code for r in result.hard_failure_reasons}


def test_refrigerated_equipment_substitutes_for_refrigerated_vehicle() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.vehicles.update(supports_refrigeration=False)
    EquipmentFactory(courier=courier, supports_refrigeration=True)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is True


def test_outside_service_zone_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    other_zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.current_service_zone = other_zone
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert OUTSIDE_SERVICE_ZONE in {r.code for r in result.hard_failure_reasons}


def test_missing_zone_data_does_not_fail_the_zone_filter() -> None:
    """Documented permissive default: no zone data on either side is not
    treated as a mismatch."""
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=None)
    courier = _make_fully_eligible_courier(cargo_class, zone=None)
    courier.availability.current_service_zone = None
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert OUTSIDE_SERVICE_ZONE not in {r.code for r in result.hard_failure_reasons}


def test_offline_courier_is_unavailable() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.is_online = False
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert UNAVAILABLE in {r.code for r in result.hard_failure_reasons}


def test_courier_with_no_availability_row_is_unavailable() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.delete()
    # `refresh_from_db()` clears the cached reverse one-to-one relation so the
    # eligibility check sees the real (now-absent) availability row, not the
    # stale in-memory instance we just called .delete() on.
    courier.refresh_from_db()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert UNAVAILABLE in {r.code for r in result.hard_failure_reasons}


def test_outside_shift_window_is_unavailable() -> None:
    """DeliveryRequestFactory's default pickup window start is 9am America/New_York
    (see apps.deliveries.tests.factories.DEFAULT_PICKUP_START)."""
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.shift_start = datetime.time(12, 0)
    courier.availability.shift_end = datetime.time(20, 0)
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert UNAVAILABLE in {r.code for r in result.hard_failure_reasons}


def test_inside_shift_window_is_available() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.shift_start = datetime.time(7, 0)
    courier.availability.shift_end = datetime.time(20, 0)
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is True


def test_capacity_exceeded_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.availability.max_concurrent_deliveries = 0
    courier.availability.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert CAPACITY_EXCEEDED in {r.code for r in result.hard_failure_reasons}


def test_facility_restriction_not_met_excludes_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(
        pickup_service_zone=zone,
        pickup_verification_requirements="Photo ID check-in required at front desk",
    )
    courier = _make_fully_eligible_courier(cargo_class, zone)
    courier.identity_review_status = IdentityReviewStatus.NOT_STARTED
    courier.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is False
    assert FACILITY_RESTRICTION_NOT_MET in {r.code for r in result.hard_failure_reasons}


def test_facility_restriction_met_by_identity_verified_courier() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(
        pickup_service_zone=zone,
        pickup_verification_requirements="Photo ID check-in required at front desk",
    )
    courier = _make_fully_eligible_courier(cargo_class, zone)

    result = check_courier_eligibility(courier, delivery_request)

    assert result.eligible is True


# --- SLA feasibility is documented as "not evaluated", never a hard failure ---


def test_sla_feasibility_is_never_a_hard_failure_reason() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)
    # Even a courier failing every other filter should never get an SLA-related
    # hard-failure code — Phase 3 does not evaluate it at all.
    courier.status = CourierStatus.SUSPENDED
    courier.save()

    result = check_courier_eligibility(courier, delivery_request)

    assert result.sla_feasibility == SLA_FEASIBILITY_NOT_EVALUATED
    assert all("sla" not in r.code for r in result.hard_failure_reasons)


# --- "No unauthorized job appears to courier" ---------------------------------


def test_eligible_deliveries_for_excludes_unauthorized_ready_for_dispatch_job() -> None:
    zone = ServiceZoneFactory()
    eligible_delivery, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    courier = _make_fully_eligible_courier(cargo_class, zone)

    # A second, *different* cargo class the courier is never authorized for.
    other_cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=other_cargo_class, allows_ambient=True, allows_refrigerated=True)
    unauthorized_delivery, _ = _make_ready_for_dispatch_delivery(
        temperature_code=TemperatureProfileCode.AMBIENT, pickup_service_zone=zone
    )
    unauthorized_delivery.cargo_class = other_cargo_class
    unauthorized_delivery.save(update_fields=["cargo_class"])

    offered = eligible_deliveries_for(courier)
    offered_ids = {d.id for d in offered}

    assert eligible_delivery.id in offered_ids
    assert unauthorized_delivery.id not in offered_ids


def test_eligible_couriers_for_excludes_courier_missing_temperature_capability() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(pickup_service_zone=zone)
    eligible_courier = _make_fully_eligible_courier(cargo_class, zone)
    ineligible_courier = _make_fully_eligible_courier(cargo_class, zone)
    ineligible_courier.cargo_authorizations.update(supports_refrigeration=False)

    candidates = eligible_couriers_for(delivery_request)
    candidate_ids = {c.pk for c in candidates}

    assert eligible_courier.pk in candidate_ids
    assert ineligible_courier.pk not in candidate_ids


# --- Property test: eligible iff every implemented hard filter passes --------

_FLAG_NAMES = [
    "account_not_active",
    "credential_expired",
    "cargo_authorization_missing",
    "temperature_capability_missing",
    "vehicle_equipment_incompatible",
    "outside_service_zone",
    "unavailable",
    "capacity_exceeded",
    "facility_restriction_not_met",
]


def _expected_codes(flags: dict[str, bool]) -> set[str]:
    """Independently-written reference calculation of which failure codes
    `check_courier_eligibility` should report for a given flag combination.

    One documented interaction, matching `apps.couriers.eligibility`'s own
    behavior: the temperature-capability check only applies when a cargo
    authorization exists at all (there is nothing to check refrigeration
    support on if the authorization itself is missing).
    """
    expected: set[str] = set()
    if flags["account_not_active"]:
        expected.add(ACCOUNT_NOT_ACTIVE)
    if flags["credential_expired"]:
        expected.add(CREDENTIAL_EXPIRED)
    if flags["cargo_authorization_missing"]:
        expected.add(CARGO_AUTHORIZATION_MISSING)
    elif flags["temperature_capability_missing"]:
        expected.add(TEMPERATURE_CAPABILITY_MISSING)
    if flags["vehicle_equipment_incompatible"]:
        expected.add(VEHICLE_EQUIPMENT_INCOMPATIBLE)
    if flags["outside_service_zone"]:
        expected.add(OUTSIDE_SERVICE_ZONE)
    if flags["unavailable"]:
        expected.add(UNAVAILABLE)
    if flags["capacity_exceeded"]:
        expected.add(CAPACITY_EXCEEDED)
    if flags["facility_restriction_not_met"]:
        expected.add(FACILITY_RESTRICTION_NOT_MET)
    return expected


def _build_scenario(flags: dict[str, bool]):
    zone = ServiceZoneFactory()
    other_zone = ServiceZoneFactory()

    delivery_request, cargo_class = _make_ready_for_dispatch_delivery(
        pickup_service_zone=zone,
        pickup_verification_requirements=(
            "Photo ID check-in required" if flags["facility_restriction_not_met"] else ""
        ),
    )

    courier = CourierProfileFactory(
        status=CourierStatus.SUSPENDED if flags["account_not_active"] else CourierStatus.APPROVED,
        identity_review_status=(
            IdentityReviewStatus.NOT_STARTED
            if flags["facility_restriction_not_met"]
            else IdentityReviewStatus.APPROVED
        ),
    )
    CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        expires_on=PAST_EXPIRY if flags["credential_expired"] else FUTURE_EXPIRY,
    )
    CourierCredentialFactory(
        courier=courier, credential_type=CourierCredentialType.INSURANCE, expires_on=FUTURE_EXPIRY
    )
    if not flags["cargo_authorization_missing"]:
        CargoAuthorizationFactory(
            courier=courier,
            cargo_class=cargo_class,
            supports_refrigeration=not flags["temperature_capability_missing"],
        )
    VehicleFactory(
        courier=courier, supports_refrigeration=not flags["vehicle_equipment_incompatible"]
    )
    CourierAvailabilityFactory(
        courier=courier,
        is_online=not flags["unavailable"],
        current_service_zone=other_zone if flags["outside_service_zone"] else zone,
        max_concurrent_deliveries=0 if flags["capacity_exceeded"] else 1,
    )
    return courier, delivery_request


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    flags=st.fixed_dictionaries({name: st.booleans() for name in _FLAG_NAMES}),
)
def test_eligible_iff_every_hard_filter_passes(flags: dict[str, bool]) -> None:
    courier, delivery_request = _build_scenario(flags)

    result = check_courier_eligibility(courier, delivery_request)

    expected = _expected_codes(flags)
    actual_codes = {r.code for r in result.hard_failure_reasons}

    assert actual_codes == expected
    assert result.eligible == (len(expected) == 0)
    assert result.sla_feasibility == SLA_FEASIBILITY_NOT_EVALUATED
