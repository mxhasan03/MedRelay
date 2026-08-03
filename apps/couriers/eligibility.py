"""The hard-eligibility engine (docs/PRODUCT_REQUIREMENTS.md section 11
"Hard eligibility filters", docs/ARCHITECTURE_AND_DATA_MODEL.md "Couriers"/
"Delivery and dispatch" entity groups — `DispatchCandidate`'s eligibility
half).

Phase 3 implements every hard filter its data model can honestly support:

- account not active (`CourierStatus != APPROVED`)
- credential expired (or missing) for a required credential type
- cargo authorization missing
- temperature capability missing
- vehicle/equipment incompatible
- outside service zone
- unavailable (offline, or outside shift window)
- current capacity exceeded
- facility restriction not met

One filter — **SLA mathematically infeasible** — is explicitly *not*
evaluated in Phase 3: it needs a real ETA/routing estimate, which does not
exist until Phase 4's dispatch scoring (`docs/IMPLEMENTATION_ROADMAP.md`
Phase 4). Rather than silently omitting it, `EligibilityResult.sla_feasibility`
always carries the literal string `"not_evaluated"` — a documented "not yet
evaluated," not a hidden gap — so Phase 4 can add a real feasibility check by
changing that one field's value, without changing this function's signature
or breaking any caller.

Honest workload/capacity proxy: there is no `DeliveryAssignment` model yet
(Phase 4), so there is nothing to count a courier's live in-flight
deliveries against. `_current_workload` below always returns `0` — this is
stated as fact, not estimated, and is exactly what
`CourierAvailability.max_concurrent_deliveries` is checked against. Once
Phase 4 introduces `DeliveryAssignment`, `_current_workload` is the one
function that needs to change to count real active assignments.

Service-zone matching is a simple zone-equality check (courier's current, or
failing that home, `ServiceZone` vs. the delivery's pickup facility's
`ServiceZone`) — not real geofencing/distance math, per
docs/ARCHITECTURE_AND_DATA_MODEL.md's Phase 1 decision to keep facility
coordinates as plain decimals until Phase 4. If either side has no zone set,
this filter does not fail the courier (a documented permissive default,
since we cannot honestly claim a mismatch we have no data for).

Facility-restriction matching uses `Facility.verification_requirements`
(the only "restriction" data Phase 1 actually modeled — free text, not a
structured rule): a non-blank requirement on either the pickup or
destination facility is treated as "requires an identity-verified courier,"
checked against `CourierProfile.identity_review_status`. This is a
deliberately simple, documented heuristic over free text, not a real rules
engine.

`eligible_couriers_for`/`eligible_deliveries_for` are plain Python-level
filters over `check_courier_eligibility` (O(n*m) — every candidate courier
or delivery request is checked individually), which is fine at this
prototype's demo data volumes. This is not a scalable database query;
optimizing it (e.g. pre-filtering with real SQL `WHERE`/`JOIN` conditions)
is reasonable future work once Phase 4 needs to run this over meaningfully
large candidate sets.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from django.db.models import Q
from django.utils import timezone

from apps.cargo.models import TemperatureProfileCode
from apps.couriers.models import (
    CargoAuthorization,
    CourierCredential,
    CourierCredentialStatus,
    CourierCredentialType,
    CourierStatus,
    IdentityReviewStatus,
)
from apps.deliveries.models import DeliveryStatus

if TYPE_CHECKING:
    from apps.couriers.models import CourierProfile
    from apps.deliveries.models import DeliveryRequest

# Required credential types the "credential expired" hard filter checks.
# Per docs/PRODUCT_REQUIREMENTS.md section 6, driver-license and insurance
# are the two onboarding statuses explicitly named; other credential types
# (identity verification, the background-check placeholder) are tracked on
# `CourierCredential` for onboarding-record completeness but are not (yet)
# required for dispatch eligibility in this prototype.
REQUIRED_CREDENTIAL_TYPES: frozenset[str] = frozenset(
    {CourierCredentialType.DRIVER_LICENSE, CourierCredentialType.INSURANCE}
)

# Stable failure-reason codes. Plain string constants (not a `models.TextChoices`)
# because these are never stored on a model field — they only ever appear in an
# in-memory `EligibilityResult`.
ACCOUNT_NOT_ACTIVE = "account_not_active"
CREDENTIAL_EXPIRED = "credential_expired"
CARGO_AUTHORIZATION_MISSING = "cargo_authorization_missing"
TEMPERATURE_CAPABILITY_MISSING = "temperature_capability_missing"
VEHICLE_EQUIPMENT_INCOMPATIBLE = "vehicle_equipment_incompatible"
OUTSIDE_SERVICE_ZONE = "outside_service_zone"
UNAVAILABLE = "unavailable"
CAPACITY_EXCEEDED = "capacity_exceeded"
FACILITY_RESTRICTION_NOT_MET = "facility_restriction_not_met"

SLA_FEASIBILITY_NOT_EVALUATED = "not_evaluated"


class EligibilityFailureReason(NamedTuple):
    """One specific hard-failure reason: a stable `code` plus a human-readable
    `message` (docs/PRODUCT_REQUIREMENTS.md section 11's "human-readable
    reasons" — Phase 4's scoring explanations reuse the same shape)."""

    code: str
    message: str


@dataclass(frozen=True)
class EligibilityResult:
    """The result of checking one courier against one delivery request.

    `sla_feasibility` is always `"not_evaluated"` in Phase 3 — see module
    docstring. It is intentionally a separate field from
    `hard_failure_reasons`: an unevaluated SLA check is not itself a hard
    failure, and must never silently make `eligible` false.
    """

    eligible: bool
    hard_failure_reasons: tuple[EligibilityFailureReason, ...] = field(default_factory=tuple)
    sla_feasibility: str = SLA_FEASIBILITY_NOT_EVALUATED


def _current_workload(courier: CourierProfile) -> int:
    """Honest Phase 3 proxy: always 0. See module docstring — there is no
    `DeliveryAssignment` model yet (Phase 4) to count real active/in-flight
    deliveries against, so this is not an estimate, it is a documented fact
    about what this phase can and cannot measure.
    """
    del courier  # Unused in Phase 3; kept as a parameter for a stable Phase 4 signature.
    return 0


def _check_account_active(courier: CourierProfile) -> EligibilityFailureReason | None:
    if courier.status != CourierStatus.APPROVED:
        return EligibilityFailureReason(
            ACCOUNT_NOT_ACTIVE,
            f"Courier account status is {courier.get_status_display()!r}, not Approved Courier.",
        )
    return None


def _check_credentials(
    courier: CourierProfile, *, as_of: datetime.date
) -> EligibilityFailureReason | None:
    for credential_type in REQUIRED_CREDENTIAL_TYPES:
        # A required credential with no recorded expiry is treated as
        # not-expired (Phase 3 does not require every credential type to
        # carry an expiry date); one with a recorded expiry must not have
        # already passed `as_of`.
        has_valid = CourierCredential.objects.filter(
            courier=courier,
            credential_type=credential_type,
            status=CourierCredentialStatus.APPROVED,
        ).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=as_of))
        if not has_valid.exists():
            return EligibilityFailureReason(
                CREDENTIAL_EXPIRED,
                f"No approved, unexpired {CourierCredentialType(credential_type).label} "
                "credential on file.",
            )
    return None


def _check_cargo_and_temperature(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> list[EligibilityFailureReason]:
    reasons: list[EligibilityFailureReason] = []
    if delivery_request.cargo_class_id is None:
        # Defensive only: a READY_FOR_DISPATCH request always has a cargo_class
        # (apps.deliveries.state_machine.validate_ready_for_dispatch enforces
        # this), so this branch should not be reachable via
        # eligible_deliveries_for's READY_FOR_DISPATCH-only candidate set.
        return reasons

    authorization = CargoAuthorization.objects.filter(
        courier=courier, cargo_class_id=delivery_request.cargo_class_id, is_active=True
    ).first()
    if authorization is None:
        reasons.append(
            EligibilityFailureReason(
                CARGO_AUTHORIZATION_MISSING,
                f"Courier has no active cargo authorization for {delivery_request.cargo_class}.",
            )
        )
        return reasons  # Nothing further to check without an authorization row.

    temperature_profile = delivery_request.temperature_profile
    if (
        temperature_profile is not None
        and temperature_profile.code == TemperatureProfileCode.REFRIGERATED
        and not authorization.supports_refrigeration
    ):
        reasons.append(
            EligibilityFailureReason(
                TEMPERATURE_CAPABILITY_MISSING,
                f"Courier is not authorized for refrigerated handling of "
                f"{delivery_request.cargo_class}.",
            )
        )
    return reasons


def _check_vehicle_and_equipment(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> EligibilityFailureReason | None:
    active_vehicles = courier.vehicles.filter(is_active=True)
    if not active_vehicles.exists():
        return EligibilityFailureReason(
            VEHICLE_EQUIPMENT_INCOMPATIBLE, "Courier has no active vehicle on file."
        )

    temperature_profile = delivery_request.temperature_profile
    requires_refrigeration = (
        temperature_profile is not None
        and temperature_profile.code == TemperatureProfileCode.REFRIGERATED
    )
    if requires_refrigeration:
        vehicle_ok = active_vehicles.filter(supports_refrigeration=True).exists()
        equipment_ok = courier.equipment.filter(
            is_active=True, supports_refrigeration=True
        ).exists()
        if not (vehicle_ok or equipment_ok):
            return EligibilityFailureReason(
                VEHICLE_EQUIPMENT_INCOMPATIBLE,
                "Courier has no active refrigerated vehicle or equipment for this "
                "refrigerated delivery.",
            )
    return None


def _check_service_zone(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> EligibilityFailureReason | None:
    availability = getattr(courier, "availability", None)
    courier_zone = None
    if availability is not None and availability.current_service_zone_id is not None:
        courier_zone = availability.current_service_zone
    elif courier.home_service_zone_id is not None:
        courier_zone = courier.home_service_zone

    pickup_stop = delivery_request.pickup_stop
    facility_zone = (
        pickup_stop.facility.service_zone
        if pickup_stop is not None and pickup_stop.facility.service_zone_id is not None
        else None
    )

    if courier_zone is None or facility_zone is None:
        # Permissive default: we have no zone data on one side, so we cannot
        # honestly claim a mismatch. See module docstring.
        return None
    if courier_zone.pk != facility_zone.pk:
        return EligibilityFailureReason(
            OUTSIDE_SERVICE_ZONE,
            f"Courier's service zone ({courier_zone}) does not match the pickup "
            f"facility's service zone ({facility_zone}).",
        )
    return None


def _to_new_york_time(instant: datetime.datetime) -> datetime.time:
    import zoneinfo

    tz = zoneinfo.ZoneInfo("America/New_York")
    return instant.astimezone(tz).time()


def _check_availability(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> EligibilityFailureReason | None:
    availability = getattr(courier, "availability", None)
    if availability is None or not availability.is_online:
        return EligibilityFailureReason(UNAVAILABLE, "Courier is offline.")

    if availability.shift_start is None or availability.shift_end is None:
        return None  # Online, no shift restriction configured.

    pickup_local_time = _to_new_york_time(delivery_request.pickup_window_start)
    shift_start = availability.shift_start
    shift_end = availability.shift_end
    if shift_start <= shift_end:
        in_shift = shift_start <= pickup_local_time <= shift_end
    else:
        # Overnight shift (e.g. 22:00-06:00).
        in_shift = pickup_local_time >= shift_start or pickup_local_time <= shift_end
    if not in_shift:
        return EligibilityFailureReason(
            UNAVAILABLE,
            f"Courier's shift ({shift_start}-{shift_end}) does not cover the pickup "
            f"window start ({pickup_local_time}, America/New_York).",
        )
    return None


def _check_capacity(courier: CourierProfile) -> EligibilityFailureReason | None:
    availability = getattr(courier, "availability", None)
    max_concurrent = availability.max_concurrent_deliveries if availability is not None else 0
    if _current_workload(courier) >= max_concurrent:
        return EligibilityFailureReason(
            CAPACITY_EXCEEDED,
            f"Courier's configured capacity ({max_concurrent} concurrent) is exhausted "
            "(Phase 3 has no live-assignment count yet, so current workload is always "
            "treated as 0 — see apps.couriers.eligibility module docstring).",
        )
    return None


def _check_facility_restriction(
    courier: CourierProfile, delivery_request: DeliveryRequest
) -> EligibilityFailureReason | None:
    restricted_facilities = [
        stop.facility
        for stop in delivery_request.stops.all()
        if stop.facility.verification_requirements.strip()
    ]
    if not restricted_facilities:
        return None
    if courier.identity_review_status != IdentityReviewStatus.APPROVED:
        names = ", ".join(f.name for f in restricted_facilities)
        status_display = courier.get_identity_review_status_display()
        return EligibilityFailureReason(
            FACILITY_RESTRICTION_NOT_MET,
            f"{names} requires an identity-verified courier "
            f"(courier identity review status is {status_display!r}).",
        )
    return None


def check_courier_eligibility(
    courier: CourierProfile,
    delivery_request: DeliveryRequest,
    *,
    as_of: datetime.date | None = None,
) -> EligibilityResult:
    """Check every Phase-3-supported hard eligibility filter for `courier`
    against `delivery_request`. Returns an `EligibilityResult` with the full
    list of hard-failure reasons (not just the first one found), so a caller
    (or a future UI) can show a courier or dispatcher everything wrong at
    once rather than one error per retry.
    """
    reference_date = as_of or timezone.localdate()
    reasons: list[EligibilityFailureReason] = []

    account_reason = _check_account_active(courier)
    if account_reason is not None:
        reasons.append(account_reason)

    credential_reason = _check_credentials(courier, as_of=reference_date)
    if credential_reason is not None:
        reasons.append(credential_reason)

    reasons.extend(_check_cargo_and_temperature(courier, delivery_request))

    vehicle_reason = _check_vehicle_and_equipment(courier, delivery_request)
    if vehicle_reason is not None:
        reasons.append(vehicle_reason)

    zone_reason = _check_service_zone(courier, delivery_request)
    if zone_reason is not None:
        reasons.append(zone_reason)

    availability_reason = _check_availability(courier, delivery_request)
    if availability_reason is not None:
        reasons.append(availability_reason)

    capacity_reason = _check_capacity(courier)
    if capacity_reason is not None:
        reasons.append(capacity_reason)

    facility_reason = _check_facility_restriction(courier, delivery_request)
    if facility_reason is not None:
        reasons.append(facility_reason)

    return EligibilityResult(eligible=not reasons, hard_failure_reasons=tuple(reasons))


def eligible_couriers_for(
    delivery_request: DeliveryRequest, *, as_of: datetime.date | None = None
) -> list[CourierProfile]:
    """Every `CourierProfile` that is currently eligible for `delivery_request`.

    See module docstring: this is a plain Python-level filter, not an
    optimized database query.
    """
    from apps.couriers.models import CourierProfile

    candidates = CourierProfile.objects.select_related(
        "user", "availability", "home_service_zone"
    ).all()
    return [
        courier
        for courier in candidates
        if check_courier_eligibility(courier, delivery_request, as_of=as_of).eligible
    ]


def eligible_deliveries_for(
    courier: CourierProfile, *, as_of: datetime.date | None = None
) -> list[DeliveryRequest]:
    """Every open (`READY_FOR_DISPATCH`) `DeliveryRequest` that `courier` is
    currently eligible to be shown as a job offer for.

    Restricting the candidate set to `READY_FOR_DISPATCH` is what makes the
    "no unauthorized job appears to courier" acceptance criterion meaningful:
    only requests that have already passed
    `apps.deliveries.state_machine.validate_ready_for_dispatch` (so
    `cargo_class`/`temperature_profile`/stops are guaranteed present) are
    considered at all — a `DRAFT`/`VALIDATION_REQUIRED` request is never
    "eligible" or "ineligible", it simply is not a candidate job yet.
    """
    from apps.deliveries.models import DeliveryRequest

    candidates = DeliveryRequest.objects.filter(status=DeliveryStatus.READY_FOR_DISPATCH)
    return [
        delivery_request
        for delivery_request in candidates
        if check_courier_eligibility(courier, delivery_request, as_of=as_of).eligible
    ]
