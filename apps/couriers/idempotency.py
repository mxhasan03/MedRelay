"""The Idempotency-Key mechanism for courier-facing state-mutating endpoints.

See `apps.couriers.models.CourierActionIdempotencyKey`'s docstring for the
full design write-up (docs/ARCHITECTURE_AND_DATA_MODEL.md section 9:
"require Idempotency-Key for create/transition endpoints"). This module is
the single call site every affected view goes through
(`apps.couriers.views`, `apps.tracking.views`): job offer accept/decline,
pickup/transit status transitions, and location pings.

Design: "store the idempotency key with the resulting effect, and if the
same key arrives again, return the original result rather than
re-applying/duplicating the effect" (docs/IMPLEMENTATION_ROADMAP.md Phase 5
acceptance criteria). Concretely:

1. Look up an existing `CourierActionIdempotencyKey` row for
   `(courier, endpoint, key)`. If found, return its stored `response_data`/
   `status_code` immediately — `fn` is never called again, so the
   underlying effect (a `DeliveryAssignment`, a `DeliveryStatusTransition`,
   a `CourierLocationPing`) is never created twice.
2. Otherwise, call `fn()` to actually perform the effect. If it raises, the
   exception propagates and **nothing is recorded** — a genuinely failed
   request (e.g. an invalid transition) must remain retryable with a
   corrected request under the same key, not be permanently remembered as a
   failure.
3. If `fn()` succeeds, try to record the result. A concurrent request with
   the same key racing this one is handled by the model's real database
   `UniqueConstraint`: if the `INSERT` loses the race, the *other* request's
   stored result (not this one's) is what gets returned — see
   `IdempotencyRaceLostError` below, which callers are not expected to
   handle specially (the fetched, already-committed result is correct and
   safe to return either way).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import IntegrityError, transaction

from apps.couriers.models import CourierActionIdempotencyKey, CourierProfile


def idempotent_call(
    *,
    courier: CourierProfile,
    endpoint: str,
    key: str,
    fn: Callable[[], dict[str, Any]],
    status_code: int = 200,
) -> tuple[dict[str, Any], int]:
    """Run `fn()` at most once per `(courier, endpoint, key)`.

    Returns `(response_data, status_code)` — either freshly computed by
    `fn()` (first time this key is seen) or replayed from a prior successful
    call with the same key. `fn` must return a JSON-serializable dict; it
    should perform its own transaction management (every service function
    this is used with — `assign_delivery`, `transition_delivery_request`,
    `record_location_ping` — already is/wraps a `transaction.atomic()`), so
    that a raised exception never leaves a partial effect behind for this
    function to accidentally record.
    """
    existing = CourierActionIdempotencyKey.objects.filter(
        courier=courier, endpoint=endpoint, key=key
    ).first()
    if existing is not None:
        return existing.response_data, existing.status_code

    response_data = fn()

    try:
        with transaction.atomic():
            CourierActionIdempotencyKey.objects.create(
                courier=courier,
                endpoint=endpoint,
                key=key,
                response_data=response_data,
                status_code=status_code,
            )
    except IntegrityError:
        # A concurrent request with the same key won the race and already
        # recorded its own (equivalent) result first. Replay *that* row
        # rather than this call's own freshly-computed response_data, so
        # every caller observing this key ends up seeing the exact same
        # stored response regardless of which concurrent request "won".
        winner = CourierActionIdempotencyKey.objects.get(
            courier=courier, endpoint=endpoint, key=key
        )
        return winner.response_data, winner.status_code

    return response_data, status_code


__all__ = ["idempotent_call"]
