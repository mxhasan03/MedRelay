"""Delivery-request creation, submission, cancellation, and optimistic concurrency.

This module is the "wizard" backend (docs/PRODUCT_REQUIREMENTS.md section 5
"Delivery request wizard") — see `apps.deliveries.forms.DeliveryRequestForm`
and `apps.deliveries.views.DeliveryRequestCreateView` for the single-form
(not literal multi-step-UI) implementation. Design decision write-up lives
in docs/CURRENT_STATUS.md "Phase 2" section ("wizard vs. single form").
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.cargo.models import PackagingAttestation
from apps.cargo.services import create_packages_for_delivery_request
from apps.deliveries.exceptions import StaleDeliveryRequestError
from apps.deliveries.models import (
    DeliveryRequest,
    DeliveryStatus,
    DeliveryStop,
    RecipientVerificationMethod,
    StopType,
)
from apps.deliveries.pricing import quote_delivery_request
from apps.deliveries.state_machine import transition_delivery_request

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.cargo.models import CargoClass, TemperatureProfile
    from apps.deliveries.models import RecurringRoute
    from apps.facilities.models import Facility
    from apps.organizations.models import Organization


@transaction.atomic
def create_delivery_request(
    *,
    organization: Organization,
    created_by: User,
    service_level: str,
    pickup_facility: Facility,
    destination_facility: Facility,
    pickup_window_start: datetime.datetime,
    pickup_window_end: datetime.datetime,
    required_delivery_by: datetime.datetime,
    cargo_class: CargoClass,
    temperature_profile: TemperatureProfile,
    package_count: int = 1,
    approximate_weight_kg: Decimal | None = None,
    approximate_length_cm: Decimal | None = None,
    approximate_width_cm: Decimal | None = None,
    approximate_height_cm: Decimal | None = None,
    sender_contact_name: str = "",
    sender_contact_phone: str = "",
    sender_contact_role: str = "",
    recipient_contact_name: str = "",
    recipient_contact_phone: str = "",
    recipient_contact_role: str = "",
    recipient_verification_method: str = RecipientVerificationMethod.NONE,
    facility_instructions: str = "",
    attest_packaging: bool = False,
    attestation_notes: str = "",
) -> DeliveryRequest:
    """Create a `DRAFT` delivery request with its pickup/destination stops,
    per-package `Package`/`PackageIdentifier` rows, and (optionally) its
    packaging attestation — the full wizard field set from
    docs/PRODUCT_REQUIREMENTS.md section 5, minus recurring-route fields.
    """
    delivery_request = DeliveryRequest(
        organization=organization,
        created_by=created_by,
        service_level=service_level,
        status=DeliveryStatus.DRAFT,
        pickup_window_start=pickup_window_start,
        pickup_window_end=pickup_window_end,
        required_delivery_by=required_delivery_by,
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
        package_count=package_count,
        approximate_weight_kg=approximate_weight_kg,
        approximate_length_cm=approximate_length_cm,
        approximate_width_cm=approximate_width_cm,
        approximate_height_cm=approximate_height_cm,
        sender_contact_name=sender_contact_name,
        sender_contact_phone=sender_contact_phone,
        sender_contact_role=sender_contact_role,
        recipient_contact_name=recipient_contact_name,
        recipient_contact_phone=recipient_contact_phone,
        recipient_contact_role=recipient_contact_role,
        recipient_verification_method=recipient_verification_method,
        facility_instructions=facility_instructions,
    )
    delivery_request.full_clean()
    delivery_request.save()

    # Phase 6: the genesis event of this delivery's custody hash chain. Lazy
    # import — apps.custody does not need apps.deliveries at module scope,
    # but this keeps the dependency direction explicit (see
    # apps.deliveries.state_machine's module docstring for the same
    # "one real import direction, one lazy" convention used across this
    # codebase).
    from apps.custody.models import CustodyActorType, CustodyEventType
    from apps.custody.services import record_event

    record_event(
        delivery_request,
        CustodyEventType.REQUEST_CREATED,
        actor_type=CustodyActorType.CUSTOMER,
        actor_user=created_by,
        payload={"service_level": service_level, "package_count": package_count},
    )

    DeliveryStop.objects.create(
        delivery_request=delivery_request,
        stop_type=StopType.PICKUP,
        sequence=1,
        facility=pickup_facility,
        scheduled_window_start=pickup_window_start,
        scheduled_window_end=pickup_window_end,
    )
    DeliveryStop.objects.create(
        delivery_request=delivery_request,
        stop_type=StopType.DESTINATION,
        sequence=2,
        facility=destination_facility,
        scheduled_window_end=required_delivery_by,
    )

    create_packages_for_delivery_request(
        delivery_request,
        count=package_count,
        cargo_class=cargo_class,
        temperature_profile=temperature_profile,
        approximate_weight_kg=approximate_weight_kg,
        approximate_length_cm=approximate_length_cm,
        approximate_width_cm=approximate_width_cm,
        approximate_height_cm=approximate_height_cm,
    )

    if attest_packaging:
        PackagingAttestation.objects.create(
            delivery_request=delivery_request,
            attested_by=created_by,
            notes=attestation_notes,
        )

    return delivery_request


def submit_delivery_request(delivery_request: DeliveryRequest, *, actor: User) -> DeliveryRequest:
    """`DRAFT -> SUBMITTED -> VALIDATION_REQUIRED`, then attempt `-> READY_FOR_DISPATCH`.

    If the `READY_FOR_DISPATCH` validation gate fails, the request is left
    at `VALIDATION_REQUIRED` — the `SUBMITTED`/`VALIDATION_REQUIRED`
    transitions still commit (each runs in its own outer-transaction step,
    and only the `READY_FOR_DISPATCH` attempt runs inside a savepoint that
    rolls back on failure). Callers that want the specific validation error
    messages should call `apps.deliveries.state_machine.validate_ready_for_dispatch`
    themselves — this function intentionally does not swallow-and-hide them,
    it just doesn't propagate them as an exception from *this* function,
    since "validation gate failed, request now sits in VALIDATION_REQUIRED"
    is an expected, non-exceptional outcome of submission, not an error in
    the submission operation itself.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction as db_transaction

    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=actor)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=actor)
    try:
        with db_transaction.atomic():
            transition_delivery_request(
                delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=actor
            )
    except ValidationError:
        pass
    else:
        quote_delivery_request(delivery_request)
    return delivery_request


def cancel_delivery_request(
    delivery_request: DeliveryRequest, *, actor: User, reason: str = ""
) -> DeliveryRequest:
    return transition_delivery_request(
        delivery_request, DeliveryStatus.CANCELLED, actor=actor, reason=reason
    )


def update_delivery_request_with_version_check(
    delivery_request: DeliveryRequest, *, expected_version: int, **field_updates: Any
) -> DeliveryRequest:
    """Optimistic-concurrency edit: apply `field_updates` only if `expected_version`
    still matches the row's current `version` (docs/ARCHITECTURE_AND_DATA_MODEL.md
    section 9). Raises `StaleDeliveryRequestError` on a mismatch, leaving the row
    untouched.
    """
    delivery_request.refresh_from_db(fields=["version"])
    if delivery_request.version != expected_version:
        raise StaleDeliveryRequestError(
            f"Delivery request {delivery_request.pk} was modified concurrently "
            f"(expected version {expected_version}, found {delivery_request.version})."
        )
    for field_name, value in field_updates.items():
        setattr(delivery_request, field_name, value)
    delivery_request.version += 1
    delivery_request.full_clean()
    delivery_request.save()
    return delivery_request


def generate_delivery_requests_for_recurring_route(
    route: RecurringRoute, target_date: datetime.date
) -> list[DeliveryRequest]:
    """STUB — not implemented in Phase 2.

    `RecurringRoute`'s data model and basic admin/service CRUD are complete
    (docs/IMPLEMENTATION_ROADMAP.md Phase 2 scope), but the actual scheduled
    job that turns an approved, unpaused `RecurringRoute` into concrete
    `DeliveryRequest` rows for a given date is deliberately not built here —
    see docs/CURRENT_STATUS.md "Phase 2" section, "Known gaps", for the full
    disclosure. Raising `NotImplementedError` (rather than silently doing
    nothing or returning an empty list) makes this gap loud instead of easy
    to miss.
    """
    raise NotImplementedError(
        "Recurring-route delivery-request generation is deferred to a later phase. "
        "See docs/CURRENT_STATUS.md Phase 2 'Known gaps'."
    )
