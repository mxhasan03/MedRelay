"""The single call site for appending `CustodyEvent` rows, plus the PIN/
signature proof-capture prototype (`ProofOfPickup`/`ProofOfDelivery`/
`RecipientVerification`).

`record_event` is the *only* correct way to create a `CustodyEvent` — see
`apps.custody.models.CustodyEvent`'s module docstring for why the model
itself cannot safely compute its own `sequence`/`previous_hash`/
`current_hash` in `save()`.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.custody.hashing import compute_event_hash
from apps.custody.models import (
    CustodyActorType,
    CustodyEvent,
    CustodyEventType,
    ProofOfDelivery,
    ProofOfPickup,
    RecipientVerification,
)

if TYPE_CHECKING:
    import datetime

    from apps.accounts.models import User
    from apps.cargo.models import Package
    from apps.deliveries.models import DeliveryRequest


class PinVerificationError(Exception):
    """Raised by `verify_recipient_pin` when no PIN was generated, or the
    submitted PIN does not match the stored hash."""


class ProofAlreadyCapturedError(Exception):
    """Raised when a proof-of-pickup/delivery row already exists for a delivery
    request — proof capture is a one-time event per delivery, per side."""


@transaction.atomic
def record_event(
    delivery_request: DeliveryRequest,
    event_type: str,
    *,
    actor_type: str,
    actor_user: User | None = None,
    actor_label: str = "",
    package: Package | None = None,
    occurred_at: datetime.datetime | None = None,
    location_lat: Any = None,
    location_lng: Any = None,
    location_description: str = "",
    device_metadata: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    correction_of: CustodyEvent | None = None,
) -> CustodyEvent:
    """Append one hash-chained `CustodyEvent` to `delivery_request`'s chain.

    Locks the parent `DeliveryRequest` row (`select_for_update`, the same
    concurrency-safety convention `apps.dispatch.services` established) for
    the duration of the sequence/previous-hash lookup and the insert, so two
    concurrent events for the same delivery can never be assigned the same
    `sequence` or computed against a stale `previous_hash`.
    """
    from apps.deliveries.models import DeliveryRequest as DeliveryRequestModel

    locked_delivery_request = DeliveryRequestModel.objects.select_for_update().get(
        pk=delivery_request.pk
    )
    last_event = (
        CustodyEvent.objects.filter(delivery_request=locked_delivery_request)
        .order_by("-sequence")
        .first()
    )
    sequence = (last_event.sequence + 1) if last_event else 1
    previous_hash = last_event.current_hash if last_event else ""
    now = timezone.now()

    event = CustodyEvent(
        delivery_request=locked_delivery_request,
        package=package,
        sequence=sequence,
        event_type=event_type,
        actor_type=actor_type,
        actor_user=actor_user,
        actor_label=actor_label,
        occurred_at=occurred_at or now,
        recorded_at=now,
        location_lat=location_lat,
        location_lng=location_lng,
        location_description=location_description,
        device_metadata=device_metadata or {},
        payload=payload or {},
        previous_hash=previous_hash,
        correction_of=correction_of,
    )
    event.current_hash = compute_event_hash(event, previous_hash)
    event.save()
    return event


def append_correction(
    original_event: CustodyEvent,
    *,
    actor_type: str,
    actor_user: User | None = None,
    actor_label: str = "",
    reason: str,
    payload: dict[str, Any] | None = None,
) -> CustodyEvent:
    """Append a `CORRECTION_APPENDED` event referencing `original_event`.

    Never mutates `original_event` — per docs/ARCHITECTURE_AND_DATA_MODEL.md
    section 5 ("user correction never deletes prior custody history") and
    docs/PRODUCT_REQUIREMENTS.md section 10 ("Corrections append new events
    and never overwrite originals"). See
    `apps/custody/tests/test_verification.py` for the explicit test.
    """
    correction_payload = {"reason": reason, "corrected_event_id": str(original_event.pk)}
    correction_payload.update(payload or {})
    return record_event(
        original_event.delivery_request,
        CustodyEventType.CORRECTION_APPENDED,
        actor_type=actor_type,
        actor_user=actor_user,
        actor_label=actor_label,
        package=original_event.package,
        payload=correction_payload,
        correction_of=original_event,
    )


def _generate_pin(length: int = 4) -> str:
    """A synthetic numeric PIN. `secrets` (stdlib) is used instead of `random`
    even though this is a low-stakes demo PIN, since it costs nothing and is
    the honest default for anything resembling a shared secret."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def generate_recipient_pin(
    delivery_request: DeliveryRequest, *, recipient_name: str = ""
) -> tuple[RecipientVerification, str]:
    """Generate (or regenerate) a recipient PIN for `delivery_request`.

    Returns `(RecipientVerification, plaintext_pin)` — the plaintext PIN is
    returned **exactly once**, here, and is never persisted; only its salted
    hash (`pin_hash`, via `django.contrib.auth.hashers.make_password`) is
    stored. Honest limitation: this prototype has no real recipient
    portal/SMS/email channel (Phase 7) to deliver that PIN to the recipient
    automatically — the intended demo flow is that an authorized customer
    org user reads it once (e.g. from a flash message on the delivery detail
    page) and relays it to the recipient out of band (phone call, in
    person), exactly the way many real last-mile couriers' PIN hand-off
    flows work today. Regenerating overwrites any prior hash — an old PIN,
    once regenerated, stops working immediately.
    """
    from apps.deliveries.models import RecipientVerificationMethod

    plaintext_pin = _generate_pin()
    verification, _ = RecipientVerification.objects.update_or_create(
        delivery_request=delivery_request,
        defaults={
            "method": RecipientVerificationMethod.PIN,
            "recipient_name": recipient_name or delivery_request.recipient_contact_name,
            "pin_hash": make_password(plaintext_pin),
            "pin_generated_at": timezone.now(),
            "pin_verified_at": None,
            "verified_by": None,
        },
    )
    return verification, plaintext_pin


def verify_recipient_pin(
    delivery_request: DeliveryRequest, submitted_pin: str, *, actor: User | None
) -> RecipientVerification:
    """Check `submitted_pin` against the stored hash and, on success, mark the
    `RecipientVerification` as verified (and record a `RECIPIENT_VERIFIED`
    custody event).

    Raises `PinVerificationError` if no PIN was ever generated for this
    delivery, or if the submitted PIN does not match.
    """
    try:
        verification = delivery_request.recipient_verification
    except RecipientVerification.DoesNotExist as exc:
        raise PinVerificationError(
            f"No recipient PIN has been generated for delivery {delivery_request.pk}."
        ) from exc

    if not verification.pin_hash or not check_password(submitted_pin or "", verification.pin_hash):
        raise PinVerificationError("The submitted PIN does not match.")

    verification.pin_verified_at = timezone.now()
    verification.verified_by = actor
    verification.save(update_fields=["pin_verified_at", "verified_by", "updated_at"])

    record_event(
        delivery_request,
        CustodyEventType.RECIPIENT_VERIFIED,
        actor_type=CustodyActorType.COURIER if actor is not None else CustodyActorType.SYSTEM,
        actor_user=actor,
        payload={"method": "pin"},
    )
    return verification


def capture_proof_of_pickup(
    delivery_request: DeliveryRequest,
    *,
    actor: User | None,
    sender_name: str = "",
    sender_role: str = "",
    signature_data_url: str = "",
    typed_signature_name: str = "",
) -> ProofOfPickup:
    """Record sender hand-off proof at pickup and append a `CUSTODY_ACCEPTED`
    custody event. Raises `ProofAlreadyCapturedError` if already captured for
    this delivery (a one-time event per delivery)."""
    if hasattr(delivery_request, "proof_of_pickup"):
        raise ProofAlreadyCapturedError(
            f"Proof of pickup was already captured for delivery {delivery_request.pk}."
        )

    with transaction.atomic():
        proof = ProofOfPickup.objects.create(
            delivery_request=delivery_request,
            sender_name=sender_name,
            sender_role=sender_role,
            signature_data_url=signature_data_url,
            typed_signature_name=typed_signature_name,
            captured_by=actor,
        )
        event = record_event(
            delivery_request,
            CustodyEventType.CUSTODY_ACCEPTED,
            actor_type=CustodyActorType.COURIER if actor is not None else CustodyActorType.SYSTEM,
            actor_user=actor,
            payload={"sender_name": sender_name, "has_signature": proof.has_signature},
        )
        proof.custody_event = event
        proof.save(update_fields=["custody_event"])
    return proof


def capture_proof_of_delivery(
    delivery_request: DeliveryRequest,
    *,
    actor: User | None,
    delivered_to_name: str = "",
    signature_data_url: str = "",
    typed_signature_name: str = "",
) -> ProofOfDelivery:
    """Record recipient hand-off proof at delivery and append a
    `DELIVERY_COMPLETED` custody event. Raises `ProofAlreadyCapturedError` if
    already captured. Does **not** itself transition the delivery's status —
    callers (e.g. `apps.couriers.views.CompleteDeliveryView`) call
    `apps.deliveries.state_machine.transition_delivery_request` separately,
    which is what actually enforces the `validate_delivered` gate (this
    row's existence is one of that gate's requirements)."""
    if hasattr(delivery_request, "proof_of_delivery"):
        raise ProofAlreadyCapturedError(
            f"Proof of delivery was already captured for delivery {delivery_request.pk}."
        )

    recipient_verification = getattr(delivery_request, "recipient_verification", None)

    with transaction.atomic():
        proof = ProofOfDelivery.objects.create(
            delivery_request=delivery_request,
            recipient_verification=recipient_verification,
            delivered_to_name=delivered_to_name,
            signature_data_url=signature_data_url,
            typed_signature_name=typed_signature_name,
            captured_by=actor,
        )
        event = record_event(
            delivery_request,
            CustodyEventType.DELIVERY_COMPLETED,
            actor_type=CustodyActorType.COURIER if actor is not None else CustodyActorType.SYSTEM,
            actor_user=actor,
            payload={"delivered_to_name": delivered_to_name, "has_signature": proof.has_signature},
        )
        proof.custody_event = event
        proof.save(update_fields=["custody_event"])
    return proof


__all__ = [
    "PinVerificationError",
    "ProofAlreadyCapturedError",
    "append_correction",
    "capture_proof_of_delivery",
    "capture_proof_of_pickup",
    "generate_recipient_pin",
    "record_event",
    "verify_recipient_pin",
]
