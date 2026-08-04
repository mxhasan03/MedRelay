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


class DeliveryRequestQuotaExceededError(Exception):
    """Raised by `apps.deliveries.services.create_delivery_request` when an
    organization has reached its `settings.DEMO_MAX_DELIVERY_REQUESTS_PER_ORG`
    cap (Phase 9 — docs/IMPLEMENTATION_ROADMAP.md "Quota/abuse safeguards").
    Not a data-validity error (that would be `ValidationError`) and not a
    concurrency error — a plain control-flow exception, matching this
    module's own convention above."""
