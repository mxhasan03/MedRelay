"""Tests for the PIN/signature proof-capture prototype
(`generate_recipient_pin`/`verify_recipient_pin`/`capture_proof_of_pickup`/
`capture_proof_of_delivery`)."""

from __future__ import annotations

import pytest
from django.contrib.auth.hashers import check_password

from apps.custody.models import ProofOfDelivery, ProofOfPickup, RecipientVerification
from apps.custody.services import (
    PinVerificationError,
    ProofAlreadyCapturedError,
    capture_proof_of_delivery,
    capture_proof_of_pickup,
    generate_recipient_pin,
    verify_recipient_pin,
)
from apps.deliveries.models import RecipientVerificationMethod
from apps.deliveries.tests.factories import DeliveryRequestFactory

pytestmark = pytest.mark.django_db


# --- Recipient PIN -----------------------------------------------------------


def test_generate_recipient_pin_stores_only_a_hash_never_plaintext() -> None:
    delivery_request = DeliveryRequestFactory(recipient_contact_name="Jordan Recipient")
    verification, plaintext_pin = generate_recipient_pin(delivery_request)

    assert len(plaintext_pin) == 4
    assert plaintext_pin.isdigit()
    assert verification.pin_hash != plaintext_pin
    assert check_password(plaintext_pin, verification.pin_hash) is True
    assert verification.method == RecipientVerificationMethod.PIN
    assert verification.recipient_name == "Jordan Recipient"
    assert verification.is_verified is False


def test_generate_recipient_pin_regeneration_invalidates_old_pin() -> None:
    delivery_request = DeliveryRequestFactory()
    _verification, first_pin = generate_recipient_pin(delivery_request)
    verification2, second_pin = generate_recipient_pin(delivery_request)

    assert RecipientVerification.objects.filter(delivery_request=delivery_request).count() == 1
    assert check_password(first_pin, verification2.pin_hash) is False
    assert check_password(second_pin, verification2.pin_hash) is True


def test_verify_recipient_pin_succeeds_with_correct_pin_and_records_custody_event() -> None:
    from apps.custody.models import CustodyEventType

    delivery_request = DeliveryRequestFactory()
    _verification, plaintext_pin = generate_recipient_pin(delivery_request)

    result = verify_recipient_pin(delivery_request, plaintext_pin, actor=None)

    assert result.is_verified is True
    assert result.verified_by is None
    last_event = delivery_request.custody_events.order_by("-sequence").first()
    assert last_event.event_type == CustodyEventType.RECIPIENT_VERIFIED


def test_verify_recipient_pin_rejects_wrong_pin() -> None:
    delivery_request = DeliveryRequestFactory()
    generate_recipient_pin(delivery_request)

    with pytest.raises(PinVerificationError):
        verify_recipient_pin(delivery_request, "0000", actor=None)

    delivery_request.recipient_verification.refresh_from_db()
    assert delivery_request.recipient_verification.is_verified is False


def test_verify_recipient_pin_raises_when_no_pin_was_ever_generated() -> None:
    delivery_request = DeliveryRequestFactory()
    with pytest.raises(PinVerificationError):
        verify_recipient_pin(delivery_request, "1234", actor=None)


# --- Proof of pickup ----------------------------------------------------------


def test_capture_proof_of_pickup_with_typed_signature_records_custody_event() -> None:
    from apps.custody.models import CustodyEventType

    delivery_request = DeliveryRequestFactory()
    proof = capture_proof_of_pickup(
        delivery_request,
        actor=None,
        sender_name="Alex Sender",
        sender_role="Front desk",
        typed_signature_name="Alex Sender",
    )

    assert isinstance(proof, ProofOfPickup)
    assert proof.has_signature is True
    assert proof.custody_event is not None
    assert proof.custody_event.event_type == CustodyEventType.CUSTODY_ACCEPTED


def test_capture_proof_of_pickup_twice_raises() -> None:
    delivery_request = DeliveryRequestFactory()
    capture_proof_of_pickup(delivery_request, actor=None, typed_signature_name="A")
    with pytest.raises(ProofAlreadyCapturedError):
        capture_proof_of_pickup(delivery_request, actor=None, typed_signature_name="B")


def test_proof_of_pickup_without_any_signature_has_signature_is_false() -> None:
    delivery_request = DeliveryRequestFactory()
    proof = capture_proof_of_pickup(delivery_request, actor=None, sender_name="No Sig")
    assert proof.has_signature is False


# --- Proof of delivery ---------------------------------------------------------


def test_capture_proof_of_delivery_links_existing_recipient_verification() -> None:
    delivery_request = DeliveryRequestFactory()
    verification, plaintext_pin = generate_recipient_pin(delivery_request)
    verify_recipient_pin(delivery_request, plaintext_pin, actor=None)

    proof = capture_proof_of_delivery(
        delivery_request, actor=None, delivered_to_name="Jordan Recipient"
    )

    assert isinstance(proof, ProofOfDelivery)
    assert proof.recipient_verification_id == verification.pk
    assert proof.custody_event is not None


def test_capture_proof_of_delivery_twice_raises() -> None:
    delivery_request = DeliveryRequestFactory()
    capture_proof_of_delivery(delivery_request, actor=None, typed_signature_name="R")
    with pytest.raises(ProofAlreadyCapturedError):
        capture_proof_of_delivery(delivery_request, actor=None, typed_signature_name="R2")
