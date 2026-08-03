"""The genuine multi-threaded concurrent-assignment race test
(docs/CURRENT_STATUS.md "Phase 4" acceptance criterion #2).

## What this test actually does

Two real OS threads, each with its **own** database connection (Django
opens a new connection per thread automatically — the only thing this test
does explicitly is call `django.db.connections.close_all()` at the end of
each thread, which is the documented, recommended cleanup for threads that
open their own connections), call `apps.dispatch.services.assign_delivery`
**concurrently** for the *same* `DeliveryRequest`, each with a *different*
eligible courier. A `threading.Barrier` synchronizes both threads to start
as close to simultaneously as possible, to maximize (not guarantee — see
below) the chance that both threads' reads/writes genuinely interleave
rather than happen to run fully sequentially.

`@pytest.mark.django_db(transaction=True)` is pytest-django's equivalent of
subclassing `django.test.TransactionTestCase` directly — real commits happen
against the actual configured test database and the test database is reset
between tests by truncation, *not* by wrapping the test body in one
uncommitted outer transaction the way plain `@pytest.mark.django_db()`
(`TestCase`-style) does. This matters a great deal here: under a wrapped
transaction, two "concurrent" threads would both be operating inside the
*same* uncommitted transaction from the outer test transaction's point of
view, and SQLite/PostgreSQL row/table locks would never actually contend —
the race literally could not manifest. `transaction=True` is what makes this
a real test of real concurrent commits.

## The assertion this proves, regardless of backend or interleaving

Exactly one of the two `assign_delivery` calls succeeds (a real,
committed, `ACTIVE` `DeliveryAssignment` row for one courier) and the other
raises a clean `apps.dispatch.exceptions.AssignmentConflictError` — never a
silent double-assignment, and never an unhandled/opaque crash. This holds no
matter which of the two possible code paths actually catches the loser:

1. If both threads' reads happen to overlap before either commits, both
   pass the initial status/eligibility checks, and the race is decided by
   `apps.dispatch.models.DeliveryAssignment`'s partial
   `UniqueConstraint` (`unique_active_assignment_per_delivery_request`) at
   INSERT time — a real database-level constraint violation
   (`IntegrityError`), which `assign_delivery` catches and converts to
   `AssignmentConflictError`.
2. If one thread's whole operation happens to complete before the other
   even starts reading, the second thread's very first check (the
   delivery's current status is no longer in `ASSIGNABLE_STATUSES`) catches
   it instead, equally cleanly.
3. **Actually observed while developing this test, on SQLite specifically**:
   the losing thread's write attempt can also fail to acquire SQLite's
   coarse whole-database write lock in time at all, surfacing as
   `django.db.OperationalError` ("database is locked") rather than
   `IntegrityError`. `assign_delivery` catches both exception types and
   converts either to `AssignmentConflictError` — see
   `apps.dispatch.services`'s module docstring for the full write-up of this
   real, empirically-found (not merely theorized) SQLite behavior.

Both are exercised by this test depending on real OS thread scheduling —
that is exactly why the test asserts the *outcome invariant* (exactly one
success, one clean failure, exactly one `ACTIVE` assignment row survives)
rather than asserting *which* code path fired.

## Honest confidence: SQLite (this test's actual run) vs. PostgreSQL

This test runs against **SQLite** (`config.settings.test`, this project's
only CI/local test database — see CLAUDE.md). Two things are true and
important to say plainly:

- `apps.dispatch.services.assign_delivery`'s `select_for_update()` call on
  the `DeliveryRequest` row is a **documented no-op under SQLite** — Django's
  own SQL compiler (confirmed by reading
  `django/db/models/sql/compiler.py` in this environment) silently omits the
  `FOR UPDATE` clause whenever the backend's `has_select_for_update` feature
  flag is `False`, which SQLite's backend never sets `True`. So this test
  does **not** exercise a real row lock at all.
- What actually makes this test pass under SQLite is (a) the partial
  `UniqueConstraint`, a real, backend-independent database constraint that
  SQLite enforces exactly as strictly as PostgreSQL would, and (b) SQLite's
  own coarser, whole-database write-transaction serialization (only one
  writer transaction can be mid-flight at a time), which is real
  concurrency control, just far coarser-grained than PostgreSQL's MVCC row
  locks.

**What this test does NOT prove**: that `select_for_update()` provides
correct, efficient, minimally-contended row-level locking under real
PostgreSQL concurrent load. On PostgreSQL, `select_for_update()` would take
a real row lock (the second transaction's `SELECT ... FOR UPDATE` would
itself block until the first transaction commits or rolls back, rather than
both transactions reading `READY_FOR_DISPATCH` simultaneously and racing to
INSERT) — a materially different, and additionally-protected, interleaving
that this SQLite-backed test cannot exercise or verify. The partial unique
constraint would still be the final backstop either way, so the *outcome*
guarantee this test proves ("exactly one wins, one gets a clean conflict")
should hold under PostgreSQL too, on the strength of that constraint alone
— but the row-lock code path itself, and any subtler timing/deadlock
behavior specific to PostgreSQL's locking, is **not** verified here. This
project's `compose.yaml` `db` service runs real PostgreSQL/PostGIS; this
test was not additionally run against it in this session (see
docs/CURRENT_STATUS.md "Phase 4" section for the honest reasoning about
time/complexity trade-off) — a real `docker compose`-backed Postgres run of
this exact test would be the natural next step to raise confidence further,
and is flagged there as a reasonable follow-up rather than something this
report claims was done.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connections

from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    VehicleFactory,
)
from apps.deliveries.models import DeliveryStatus, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.exceptions import AssignmentConflictError, IneligibleCourierError
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment
from apps.dispatch.services import assign_delivery
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory


def _ready_for_dispatch_delivery(*, pickup_zone=None):
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    pickup_facility = FacilityFactory(service_zone=pickup_zone)
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
    return delivery_request, cargo_class


def _eligible_courier(cargo_class, zone):
    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True, current_service_zone=zone)
    return courier


@pytest.mark.django_db(transaction=True)
def test_concurrent_assign_delivery_exactly_one_wins() -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier_a = _eligible_courier(cargo_class, zone)
    courier_b = _eligible_courier(cargo_class, zone)

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def worker(label: str, courier_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            # A reason is supplied unconditionally so neither thread can be
            # short-circuited by the *unrelated* "non-top-ranked candidate
            # needs a reason" soft-override check — this test is exercising
            # the assignment-conflict race, not the override-reason gate.
            assign_delivery(
                delivery_request.pk,
                courier_id,
                None,
                reason="Concurrency test — either candidate is acceptable.",
            )
        except AssignmentConflictError as exc:
            results[label] = f"conflict: {exc}"
        except IneligibleCourierError as exc:  # pragma: no cover - should not happen here
            results[label] = f"ineligible (unexpected): {exc}"
        except Exception as exc:  # pragma: no cover - would be a genuine crash/bug
            results[label] = f"CRASH: {exc!r}"
        else:
            results[label] = "success"
        finally:
            # Recommended Django cleanup for threads that open their own
            # connections — see module docstring.
            connections.close_all()

    thread_a = threading.Thread(target=worker, args=("a", courier_a.pk))
    thread_b = threading.Thread(target=worker, args=("b", courier_b.pk))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not thread_a.is_alive(), "worker thread 'a' did not finish — deadlock?"
    assert not thread_b.is_alive(), "worker thread 'b' did not finish — deadlock?"
    assert set(results) == {"a", "b"}, "both worker threads must report a result"

    successes = [label for label, outcome in results.items() if outcome == "success"]
    conflicts = [label for label, outcome in results.items() if outcome.startswith("conflict:")]
    crashes = [label for label, outcome in results.items() if outcome.startswith("CRASH")]

    assert crashes == [], f"no worker should crash with an unexpected exception: {results}"
    assert len(successes) == 1, f"exactly one assignment attempt must succeed: {results}"
    assert len(conflicts) == 1, f"exactly one attempt must get a clean conflict error: {results}"

    # No silent double-assignment: exactly one ACTIVE assignment row exists,
    # and it belongs to whichever courier actually won.
    active_assignments = DeliveryAssignment.objects.filter(
        delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
    )
    assert active_assignments.count() == 1
    winner_courier_id = courier_a.pk if successes == ["a"] else courier_b.pk
    assert active_assignments.get().courier_id == winner_courier_id

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED
