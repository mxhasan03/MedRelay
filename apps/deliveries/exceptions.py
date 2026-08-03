"""Exceptions for the delivery state machine and optimistic-concurrency checks.

Validation failures (missing cargo classification, missing packaging
attestation, prohibited-cargo keyword hits) use Django's own
`django.core.exceptions.ValidationError` instead — that is the idiomatic
choice for "the data is not acceptable" in Django, and it composes with
`full_clean()`/form validation for free. The exceptions below are for
control-flow errors that are not about the data's validity but about the
*operation* being invalid (an illegal state transition, a stale version).
"""

from __future__ import annotations


class InvalidTransitionError(Exception):
    """Raised when a requested delivery-status transition is not allowed from the
    current status (docs/CURRENT_STATUS.md "Phase 2" — only the early-lifecycle
    transitions are load-bearing; see apps.deliveries.state_machine)."""


class StaleDeliveryRequestError(Exception):
    """Raised by optimistic-concurrency checks when the caller's expected
    `version` does not match the current row's `version`."""
