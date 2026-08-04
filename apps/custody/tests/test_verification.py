"""The tamper-evident hash-chain tests — the single most important test file
in Phase 6.

`test_verify_custody_chain_detects_genuine_raw_sql_tampering` is the load-
bearing test: it builds a real chain through `record_event`, verifies it
passes, then genuinely tampers with a historical event's stored field via a
raw SQL `UPDATE` — bypassing both the instance-level and queryset-level
ORM append-only guards entirely (simulating someone with raw database
access, not just someone misusing the ORM) — and confirms
`verify_custody_chain` actually detects the break, and at the correct
event.
"""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.custody.models import CustodyActorType, CustodyEvent, CustodyEventType
from apps.custody.services import append_correction, record_event
from apps.custody.verification import verify_custody_chain
from apps.deliveries.tests.factories import DeliveryRequestFactory

pytestmark = pytest.mark.django_db


def _raw_pk(instance: CustodyEvent):
    """The PK value exactly as it is physically stored in this backend's
    table (e.g. a 32-char hex string with no dashes on SQLite/MySQL, vs. a
    native UUID/dashed string on PostgreSQL) — `CustodyEvent._meta.pk.
    get_db_prep_value` is what the ORM itself uses to convert a `uuid.UUID`
    Python value into the on-disk representation, so a raw-SQL `WHERE id =`
    clause built from `str(instance.pk)` alone would silently match zero
    rows on a backend that doesn't store UUIDs as dashed strings (SQLite,
    the backend this project's tests run against, is exactly such a
    backend) — this is what makes the raw-SQL tamper genuinely land on the
    right row on any backend, not just happen to work on Postgres."""
    return CustodyEvent._meta.pk.get_db_prep_value(instance.pk, connection)


def _build_chain(delivery_request, count: int = 4) -> list[CustodyEvent]:
    events = []
    event_types = [
        CustodyEventType.REQUEST_CREATED,
        CustodyEventType.COURIER_ASSIGNED,
        CustodyEventType.CUSTODY_ACCEPTED,
        CustodyEventType.DELIVERY_COMPLETED,
    ]
    for i in range(count):
        event = record_event(
            delivery_request,
            event_types[i % len(event_types)],
            actor_type=CustodyActorType.SYSTEM,
            payload={"step": i},
        )
        events.append(event)
    return events


# --- Chain construction / hash correctness ----------------------------------


def test_record_event_builds_a_correctly_linked_chain() -> None:
    delivery_request = DeliveryRequestFactory()
    events = _build_chain(delivery_request)

    assert [e.sequence for e in events] == [1, 2, 3, 4]
    assert events[0].previous_hash == ""
    for earlier, later in zip(events, events[1:], strict=False):
        assert later.previous_hash == earlier.current_hash
        assert later.current_hash != earlier.current_hash

    for event in events:
        assert len(event.current_hash) == 64  # SHA-256 hex digest length
        int(event.current_hash, 16)  # is valid hex


def test_verify_custody_chain_passes_for_a_valid_chain() -> None:
    delivery_request = DeliveryRequestFactory()
    events = _build_chain(delivery_request)

    result = verify_custody_chain(delivery_request.pk)

    assert result.is_valid is True
    assert result.checked_count == len(events)
    assert result.broken_at_sequence is None
    assert result.broken_event_id is None


def test_verify_custody_chain_is_valid_and_trivial_for_a_delivery_with_no_events() -> None:
    delivery_request = DeliveryRequestFactory()
    result = verify_custody_chain(delivery_request.pk)
    assert result.is_valid is True
    assert result.checked_count == 0


# --- The load-bearing tamper-detection test ---------------------------------


def test_verify_custody_chain_detects_genuine_raw_sql_tampering() -> None:
    """Build a valid chain, confirm it verifies, then genuinely tamper with a
    *historical* (not the latest) event's stored payload via a raw SQL
    UPDATE — bypassing the ORM entirely, including both the instance-level
    `save()` guard and the queryset-level `.update()` guard — and confirm
    the verifier detects the break at exactly that event."""
    delivery_request = DeliveryRequestFactory()
    events = _build_chain(delivery_request, count=4)

    # Sanity: the ORM-level append-only guards really do block a normal
    # mutation attempt (proving the tamper below genuinely needs to go
    # around the ORM, not just call an API that would have worked anyway).
    tampered_target = events[1]  # a historical (not the first, not the last) event
    with pytest.raises(ValidationError):
        tampered_target.payload = {"already": "blocked by save() guard"}
        tampered_target.save()
    with pytest.raises(ValidationError):
        CustodyEvent.objects.filter(pk=tampered_target.pk).update(payload={"x": 1})

    # Confirm the chain is genuinely valid before tampering.
    assert verify_custody_chain(delivery_request.pk).is_valid is True

    # Now genuinely bypass the ORM: a raw SQL UPDATE against the real table,
    # exactly simulating direct database access that ignores the
    # application-level append-only convention entirely.
    table = CustodyEvent._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET payload = %s WHERE id = %s",  # noqa: S608 - test-only, fixed table name
            [json.dumps({"step": "TAMPERED"}), _raw_pk(tampered_target)],
        )

    tampered_target.refresh_from_db()
    assert tampered_target.payload == {"step": "TAMPERED"}  # the raw SQL really did change it

    result = verify_custody_chain(delivery_request.pk)

    assert result.is_valid is False
    assert result.checked_count == 2  # walk stops at the first broken event (sequence 2)
    assert result.broken_at_sequence == tampered_target.sequence
    assert result.broken_event_id == tampered_target.pk
    assert "current_hash" in result.reason or "hash" in result.reason.lower()


def test_verify_custody_chain_detects_a_tampered_previous_hash_link() -> None:
    """Tampering with `previous_hash` itself (not just the payload) is also
    detected — either as a direct previous_hash-link mismatch against the
    prior event, or (since previous_hash also feeds this event's own hash)
    as a current_hash mismatch. Either way, `is_valid` must be False and the
    correct event must be identified."""
    delivery_request = DeliveryRequestFactory()
    events = _build_chain(delivery_request, count=3)
    target = events[2]

    table = CustodyEvent._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET previous_hash = %s WHERE id = %s",  # noqa: S608
            ["0" * 64, _raw_pk(target)],
        )

    result = verify_custody_chain(delivery_request.pk)
    assert result.is_valid is False
    assert result.broken_at_sequence == target.sequence


def test_verify_custody_chain_only_checks_the_requested_deliverys_events() -> None:
    """Tampering with one delivery's chain must not report a break for an
    unrelated delivery."""
    delivery_a = DeliveryRequestFactory()
    delivery_b = DeliveryRequestFactory()
    _build_chain(delivery_a, count=2)
    _build_chain(delivery_b, count=2)

    table = CustodyEvent._meta.db_table
    tampered = CustodyEvent.objects.filter(delivery_request=delivery_a).order_by("sequence").first()
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET payload = %s WHERE id = %s",  # noqa: S608
            [json.dumps({"tampered": True}), _raw_pk(tampered)],
        )

    assert verify_custody_chain(delivery_a.pk).is_valid is False
    assert verify_custody_chain(delivery_b.pk).is_valid is True


# --- Corrections append, never overwrite ------------------------------------


def test_correction_appends_a_new_event_and_never_mutates_the_original() -> None:
    delivery_request = DeliveryRequestFactory()
    events = _build_chain(delivery_request, count=2)
    original = events[0]
    original_payload_snapshot = dict(original.payload)
    original_hash_snapshot = original.current_hash

    correction = append_correction(
        original,
        actor_type=CustodyActorType.INTERNAL_OPS,
        reason="Package count was recorded incorrectly.",
        payload={"corrected_field": "package_count", "new_value": 5},
    )

    original.refresh_from_db()
    assert original.payload == original_payload_snapshot
    assert original.current_hash == original_hash_snapshot

    assert correction.event_type == CustodyEventType.CORRECTION_APPENDED
    assert correction.correction_of_id == original.pk
    assert correction.sequence == 3  # appended after the existing 2 events
    assert correction.payload["reason"] == "Package count was recorded incorrectly."
    assert correction.payload["corrected_event_id"] == str(original.pk)
    assert correction.payload["corrected_field"] == "package_count"

    # The correction is itself a normal, verifiable link in the chain.
    assert verify_custody_chain(delivery_request.pk).is_valid is True

    # And attempting to directly edit the original event is still blocked.
    with pytest.raises(ValidationError):
        original.payload = {"nope": True}
        original.save()


def test_correction_of_relationship_is_queryable_from_the_original_event() -> None:
    delivery_request = DeliveryRequestFactory()
    (original,) = _build_chain(delivery_request, count=1)
    correction = append_correction(
        original, actor_type=CustodyActorType.INTERNAL_OPS, reason="Fixing a typo."
    )
    assert list(original.corrections.all()) == [correction]


# --- Append-only guard (ORM-level; the weaker mechanism the hash chain
# strengthens — see apps.custody.models.CustodyEvent's module docstring) ----


def test_custody_event_delete_is_blocked() -> None:
    delivery_request = DeliveryRequestFactory()
    (event,) = _build_chain(delivery_request, count=1)
    with pytest.raises(ValidationError):
        event.delete()


def test_custody_event_queryset_bulk_delete_is_blocked() -> None:
    delivery_request = DeliveryRequestFactory()
    _build_chain(delivery_request, count=1)
    with pytest.raises(ValidationError):
        CustodyEvent.objects.filter(delivery_request=delivery_request).delete()
