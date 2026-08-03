"""Tests for apps.couriers.idempotency.idempotent_call — the mechanism
backing every new Phase 5 courier state-mutating endpoint's
Idempotency-Key handling.
"""

from __future__ import annotations

import pytest

from apps.couriers.idempotency import idempotent_call
from apps.couriers.models import CourierActionIdempotencyKey
from apps.couriers.tests.factories import CourierProfileFactory

pytestmark = pytest.mark.django_db


def test_idempotent_call_runs_fn_once_and_returns_its_result() -> None:
    courier = CourierProfileFactory()
    calls = []

    def fn():
        calls.append(1)
        return {"value": 42}

    data, status_code = idempotent_call(
        courier=courier, endpoint="test_endpoint", key="key-1", fn=fn
    )

    assert data == {"value": 42}
    assert status_code == 200
    assert len(calls) == 1
    assert CourierActionIdempotencyKey.objects.filter(
        courier=courier, endpoint="test_endpoint", key="key-1"
    ).exists()


def test_idempotent_call_replays_stored_result_without_calling_fn_again() -> None:
    """Hard acceptance criterion: reruns/retries do not duplicate events."""
    courier = CourierProfileFactory()
    calls = []

    def fn():
        calls.append(1)
        return {"count": len(calls)}

    first_data, _ = idempotent_call(courier=courier, endpoint="ep", key="dup-key", fn=fn)
    second_data, _ = idempotent_call(courier=courier, endpoint="ep", key="dup-key", fn=fn)

    assert len(calls) == 1  # fn was only ever actually invoked once
    assert first_data == second_data == {"count": 1}
    assert CourierActionIdempotencyKey.objects.filter(courier=courier, endpoint="ep").count() == 1


def test_idempotent_call_scopes_by_courier() -> None:
    """The same key from two different couriers must never collide."""
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()

    data_a, _ = idempotent_call(
        courier=courier_a, endpoint="ep", key="shared-key", fn=lambda: {"who": "a"}
    )
    data_b, _ = idempotent_call(
        courier=courier_b, endpoint="ep", key="shared-key", fn=lambda: {"who": "b"}
    )

    assert data_a == {"who": "a"}
    assert data_b == {"who": "b"}
    assert CourierActionIdempotencyKey.objects.filter(key="shared-key").count() == 2


def test_idempotent_call_scopes_by_endpoint() -> None:
    """The same client-generated key reused across two different endpoints
    must not falsely dedupe them against each other."""
    courier = CourierProfileFactory()

    data_1, _ = idempotent_call(
        courier=courier, endpoint="endpoint_one", key="k", fn=lambda: {"n": 1}
    )
    data_2, _ = idempotent_call(
        courier=courier, endpoint="endpoint_two", key="k", fn=lambda: {"n": 2}
    )

    assert data_1 == {"n": 1}
    assert data_2 == {"n": 2}


def test_idempotent_call_does_not_record_a_failed_attempt() -> None:
    """A genuinely failed request (fn raises) must remain retryable under the
    same key with a corrected request, not be permanently remembered as a
    failure."""
    courier = CourierProfileFactory()

    def failing_fn():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        idempotent_call(courier=courier, endpoint="ep", key="retry-key", fn=failing_fn)

    assert not CourierActionIdempotencyKey.objects.filter(
        courier=courier, endpoint="ep", key="retry-key"
    ).exists()

    # A corrected retry under the same key now succeeds normally.
    data, _ = idempotent_call(
        courier=courier, endpoint="ep", key="retry-key", fn=lambda: {"ok": True}
    )
    assert data == {"ok": True}


def test_idempotent_call_finds_a_pre_existing_row_and_never_calls_fn() -> None:
    """A row already recorded for this (courier, endpoint, key) — exactly the
    state left behind by a concurrent request that already won the race and
    committed its own result — is replayed as-is; fn is never invoked. This
    covers the "existing row found" half of the concurrency story described
    in apps.couriers.idempotency's module docstring; the IntegrityError/
    "lost the race mid-INSERT" half is a genuine, narrow race window that
    would need real concurrent threads (like
    apps.dispatch.tests.test_concurrency) to exercise directly rather than a
    single-threaded unit test — not attempted here, called out honestly
    rather than faked."""
    courier = CourierProfileFactory()
    CourierActionIdempotencyKey.objects.create(
        courier=courier, endpoint="ep", key="race-key", response_data={"winner": True}
    )

    data, status_code = idempotent_call(
        courier=courier, endpoint="ep", key="race-key", fn=lambda: {"winner": False}
    )

    assert data == {"winner": True}
