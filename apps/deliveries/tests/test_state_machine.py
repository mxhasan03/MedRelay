"""Tests for the delivery status state machine.

Covers the Phase 2 acceptance criteria: valid transitions succeed, invalid/
skipped transitions raise, cancellation rules are enforced, missing cargo
classification/packaging attestation block READY_FOR_DISPATCH, and
DeliveryStatusTransition rows are genuinely append-only.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.deliveries.exceptions import InvalidTransitionError
from apps.deliveries.models import DeliveryStatus, DeliveryStatusTransition, StopType
from apps.deliveries.state_machine import transition_delivery_request, validate_ready_for_dispatch
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def _fully_valid_delivery_request():
    """Build a DeliveryRequest with everything READY_FOR_DISPATCH needs: cargo
    class/temperature profile compatible with policy, both stops, and a
    packaging attestation."""
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    return delivery_request


# --- Valid transitions -------------------------------------------------------


def test_draft_to_submitted_succeeds() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    assert delivery_request.status == DeliveryStatus.SUBMITTED
    assert delivery_request.version == 2


def test_full_happy_path_reaches_ready_for_dispatch() -> None:
    delivery_request = _fully_valid_delivery_request()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
    assert delivery_request.version == 4


def test_cancellation_allowed_from_draft_submitted_validation_required_and_ready() -> None:
    for target_status in (
        DeliveryStatus.DRAFT,
        DeliveryStatus.SUBMITTED,
        DeliveryStatus.VALIDATION_REQUIRED,
        DeliveryStatus.READY_FOR_DISPATCH,
        DeliveryStatus.OFFERED,
        DeliveryStatus.ASSIGNED,
    ):
        delivery_request = _fully_valid_delivery_request()
        if target_status != DeliveryStatus.DRAFT:
            transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
        if target_status in (
            DeliveryStatus.VALIDATION_REQUIRED,
            DeliveryStatus.READY_FOR_DISPATCH,
            DeliveryStatus.OFFERED,
            DeliveryStatus.ASSIGNED,
        ):
            transition_delivery_request(
                delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None
            )
        if target_status in (
            DeliveryStatus.READY_FOR_DISPATCH,
            DeliveryStatus.OFFERED,
            DeliveryStatus.ASSIGNED,
        ):
            transition_delivery_request(
                delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None
            )
        if target_status in (DeliveryStatus.OFFERED, DeliveryStatus.ASSIGNED):
            transition_delivery_request(delivery_request, DeliveryStatus.OFFERED, actor=None)
        if target_status == DeliveryStatus.ASSIGNED:
            transition_delivery_request(delivery_request, DeliveryStatus.ASSIGNED, actor=None)
        assert delivery_request.status == target_status

        transition_delivery_request(delivery_request, DeliveryStatus.CANCELLED, actor=None)
        assert delivery_request.status == DeliveryStatus.CANCELLED


# --- Invalid / skipped transitions ------------------------------------------


def test_skipping_states_raises_invalid_transition() -> None:
    delivery_request = DeliveryRequestFactory()
    with pytest.raises(InvalidTransitionError):
        transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)


def test_transition_from_cancelled_raises_invalid_transition() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.CANCELLED, actor=None)
    with pytest.raises(InvalidTransitionError):
        transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)


def test_ready_for_dispatch_to_offered_succeeds() -> None:
    """Phase 4 extends the state machine: READY_FOR_DISPATCH -> OFFERED is now
    implemented (apps.dispatch.services.offer_delivery)."""
    delivery_request = _fully_valid_delivery_request()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.OFFERED, actor=None)
    assert delivery_request.status == DeliveryStatus.OFFERED


def test_ready_for_dispatch_and_offered_can_both_reach_assigned() -> None:
    """Phase 4: a delivery may be assigned directly from READY_FOR_DISPATCH
    (apps.dispatch.services.assign_delivery), or after an OFFERED round."""
    direct = _fully_valid_delivery_request()
    transition_delivery_request(direct, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(direct, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(direct, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    transition_delivery_request(direct, DeliveryStatus.ASSIGNED, actor=None)
    assert direct.status == DeliveryStatus.ASSIGNED

    via_offer = _fully_valid_delivery_request()
    transition_delivery_request(via_offer, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(via_offer, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(via_offer, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    transition_delivery_request(via_offer, DeliveryStatus.OFFERED, actor=None)
    transition_delivery_request(via_offer, DeliveryStatus.ASSIGNED, actor=None)
    assert via_offer.status == DeliveryStatus.ASSIGNED


def test_offered_can_revert_to_ready_for_dispatch() -> None:
    """An offer round with no acceptance reverts the delivery to the open pool."""
    delivery_request = _fully_valid_delivery_request()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.OFFERED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH


def _assigned_delivery_request():
    delivery_request = _fully_valid_delivery_request()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.ASSIGNED, actor=None)
    return delivery_request


def test_assigned_through_at_destination_succeeds_step_by_step() -> None:
    """Phase 5 extends the state machine with the courier-driven middle
    transitions (apps.couriers.services.advance_delivery_status)."""
    delivery_request = _assigned_delivery_request()
    for to_status in (
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
        DeliveryStatus.AT_DESTINATION,
    ):
        transition_delivery_request(delivery_request, to_status, actor=None)
        assert delivery_request.status == to_status


def test_skipping_a_pickup_transit_step_raises_invalid_transition() -> None:
    """Each Phase 5 transition must be taken in order — jumping straight from
    ASSIGNED to PICKED_UP (skipping COURIER_EN_ROUTE_TO_PICKUP/AT_PICKUP) is
    not allowed."""
    delivery_request = _assigned_delivery_request()
    with pytest.raises(InvalidTransitionError):
        transition_delivery_request(delivery_request, DeliveryStatus.PICKED_UP, actor=None)


def test_cancellation_allowed_from_every_pickup_transit_state() -> None:
    sequence = (
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
    )
    for index, to_status in enumerate(sequence):
        delivery_request = _assigned_delivery_request()
        for step in sequence[: index + 1]:
            transition_delivery_request(delivery_request, step, actor=None)
        assert delivery_request.status == to_status
        transition_delivery_request(delivery_request, DeliveryStatus.CANCELLED, actor=None)
        assert delivery_request.status == DeliveryStatus.CANCELLED


def test_at_destination_to_delivered_is_not_implemented_in_phase_5() -> None:
    """DELIVERED implies proof-of-delivery capture (recipient PIN/signature),
    which is Phase 6 ("custody, proof, temperature, and incidents") work —
    see apps/deliveries/state_machine.py's module docstring for the full
    boundary write-up. Phase 5 stops at AT_DESTINATION; attempting the final
    transition must raise, not silently succeed."""
    delivery_request = _assigned_delivery_request()
    for to_status in (
        DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
        DeliveryStatus.AT_PICKUP,
        DeliveryStatus.PICKED_UP,
        DeliveryStatus.IN_TRANSIT,
        DeliveryStatus.AT_DESTINATION,
    ):
        transition_delivery_request(delivery_request, to_status, actor=None)
    with pytest.raises(InvalidTransitionError):
        transition_delivery_request(delivery_request, DeliveryStatus.DELIVERED, actor=None)


# --- Validation gate: missing cargo classification / packaging attestation ---


def test_missing_cargo_classification_blocks_ready_for_dispatch() -> None:
    delivery_request = DeliveryRequestFactory(cargo_class=None, temperature_profile=None)
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)

    with pytest.raises(ValidationError) as exc_info:
        transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)

    assert any("classification" in message for message in exc_info.value.messages)
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.VALIDATION_REQUIRED


def test_missing_packaging_attestation_blocks_ready_for_dispatch_when_required() -> None:
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(
        cargo_class=cargo_class, requires_packaging_attestation=True, allows_refrigerated=True
    )
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    # Deliberately no PackagingAttestation created.

    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)

    with pytest.raises(ValidationError) as exc_info:
        transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)

    assert any("attestation" in message for message in exc_info.value.messages)


def test_temperature_profile_not_permitted_by_policy_blocks_ready_for_dispatch() -> None:
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_1)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=False)
    refrigerated = TemperatureProfileFactory(code=TemperatureProfileCode.REFRIGERATED)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=refrigerated
    )
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    DeliveryStopFactory(
        delivery_request=delivery_request, stop_type=StopType.DESTINATION, sequence=2
    )
    PackagingAttestationFactory(delivery_request=delivery_request)

    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)

    with pytest.raises(ValidationError):
        transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)


def test_prohibited_keyword_in_instructions_blocks_ready_for_dispatch() -> None:
    delivery_request = _fully_valid_delivery_request()
    delivery_request.facility_instructions = "Do not repackage; courier will repack on arrival."
    delivery_request.save(update_fields=["facility_instructions"])

    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)

    with pytest.raises(ValidationError):
        validate_ready_for_dispatch(delivery_request)


# --- Append-only DeliveryStatusTransition ------------------------------------


def test_transitions_are_recorded_append_only() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)

    rows = list(delivery_request.status_transitions.all())
    assert [(r.from_status, r.to_status) for r in rows] == [
        (DeliveryStatus.DRAFT, DeliveryStatus.SUBMITTED),
        (DeliveryStatus.SUBMITTED, DeliveryStatus.VALIDATION_REQUIRED),
    ]


def test_existing_transition_row_cannot_be_updated() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    row = delivery_request.status_transitions.first()

    row.reason = "trying to rewrite history"
    with pytest.raises(ValidationError):
        row.save()


def test_existing_transition_row_cannot_be_deleted() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    row = delivery_request.status_transitions.first()

    with pytest.raises(ValidationError):
        row.delete()

    assert DeliveryStatusTransition.objects.filter(pk=row.pk).exists()


def test_bulk_queryset_update_is_rejected() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)

    with pytest.raises(ValidationError):
        DeliveryStatusTransition.objects.filter(delivery_request=delivery_request).update(
            reason="bulk rewrite"
        )


def test_bulk_queryset_delete_is_rejected() -> None:
    delivery_request = DeliveryRequestFactory()
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)

    with pytest.raises(ValidationError):
        DeliveryStatusTransition.objects.filter(delivery_request=delivery_request).delete()

    assert DeliveryStatusTransition.objects.filter(delivery_request=delivery_request).count() == 1


def test_cross_tenant_delivery_requests_not_mixed_up_in_factories() -> None:
    """Sanity check that two delivery requests for different organizations are
    genuinely independent rows (guards against a shared-mutable-default bug)."""
    org_a = OrganizationFactory()
    org_b = OrganizationFactory()
    dr_a = DeliveryRequestFactory(organization=org_a)
    dr_b = DeliveryRequestFactory(organization=org_b)
    assert dr_a.organization_id != dr_b.organization_id
