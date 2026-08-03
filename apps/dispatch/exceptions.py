"""Exceptions for the dispatch service layer (`apps.dispatch.services`).

Matches the existing project convention (see
`apps.deliveries.exceptions`'s own docstring): these are control-flow errors
about the *operation* being invalid, not Django `ValidationError`s about data
shape.
"""

from __future__ import annotations


class IneligibleCourierError(Exception):
    """Raised when an assignment/offer/reassignment entry point is asked to
    assign or offer a delivery to a courier who fails a Phase 3
    hard-eligibility filter (`apps.couriers.eligibility.check_courier_eligibility`).

    This is the load-bearing "hard gates cannot be overridden" guarantee: no
    dispatcher-supplied `reason`, `DispatchOverride` record, or reassignment
    justification can suppress this exception from any of
    `apps.dispatch.services.assign_delivery`/`offer_delivery`/
    `reassign_delivery` — each calls `check_courier_eligibility` unconditionally
    before writing anything. See docs/CURRENT_STATUS.md "Phase 4" section for
    the dedicated test proving this for every entry point.
    """


class AssignmentConflictError(Exception):
    """Raised when an assignment/offer/reassignment operation cannot proceed
    because of the delivery's current state: already has an active
    assignment, not in an assignable status, no active assignment exists to
    reassign, or a concurrent assignment attempt won the race first. This is
    the clean, typed "conflict, not a crash or a silent double-assignment"
    error the concurrency acceptance criterion requires — see
    `apps.dispatch.services.assign_delivery` and
    `apps.dispatch.tests.test_concurrency`.
    """
