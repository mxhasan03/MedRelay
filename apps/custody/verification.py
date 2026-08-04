"""The tamper-evidence verifier: walks a delivery's full `CustodyEvent` chain
and confirms every `current_hash` is correctly derived and every
`previous_hash` correctly links to the prior event.

This is what makes the hash chain a *real* tamper-evidence mechanism rather
than just an append-only convention: even if someone bypasses the ORM-level
append-only guard entirely (e.g. via raw SQL — see
`apps/custody/tests/test_verification.py`'s
`test_verify_custody_chain_detects_raw_sql_tampering`, which does exactly
that), `verify_custody_chain` recomputes each event's expected hash from its
*current* stored field values and will disagree with the stored
`current_hash` the moment any field was altered after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.custody.hashing import compute_event_hash
from apps.custody.models import CustodyEvent


@dataclass(frozen=True)
class ChainVerificationResult:
    delivery_request_id: Any
    is_valid: bool
    checked_count: int
    broken_at_sequence: int | None = None
    broken_event_id: Any | None = None
    reason: str = ""


def verify_custody_chain(delivery_id: Any) -> ChainVerificationResult:
    """Verify the full custody-event chain for `delivery_id`.

    Returns a `ChainVerificationResult` reporting whether the chain is
    intact and, if not, exactly which event (by `sequence`/`id`) broke it
    and why (a `previous_hash` link mismatch, or a `current_hash` that no
    longer matches the event's own recomputed hash — i.e. the row's data was
    altered after it was written).
    """
    events = list(CustodyEvent.objects.filter(delivery_request_id=delivery_id).order_by("sequence"))

    expected_previous_hash = ""
    checked_count = 0
    for event in events:
        checked_count += 1
        if event.previous_hash != expected_previous_hash:
            return ChainVerificationResult(
                delivery_request_id=delivery_id,
                is_valid=False,
                checked_count=checked_count,
                broken_at_sequence=event.sequence,
                broken_event_id=event.pk,
                reason=(
                    f"Event {event.sequence}'s previous_hash does not match the prior event's "
                    "current_hash — the chain link is broken (an earlier event may have been "
                    "reordered, deleted, or this event's previous_hash was itself altered)."
                ),
            )

        recomputed_hash = compute_event_hash(event, event.previous_hash)
        if recomputed_hash != event.current_hash:
            return ChainVerificationResult(
                delivery_request_id=delivery_id,
                is_valid=False,
                checked_count=checked_count,
                broken_at_sequence=event.sequence,
                broken_event_id=event.pk,
                reason=(
                    f"Event {event.sequence}'s stored current_hash does not match its recomputed "
                    "hash — this event's data was altered after it was written."
                ),
            )
        expected_previous_hash = event.current_hash

    return ChainVerificationResult(
        delivery_request_id=delivery_id, is_valid=True, checked_count=checked_count
    )


__all__ = ["ChainVerificationResult", "verify_custody_chain"]
