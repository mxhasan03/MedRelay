"""Tests for apps.dispatch models: the partial unique constraint on active
assignments, DispatchOverride's required-reason guard, and JobOffer.is_expired.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.couriers.tests.factories import CourierProfileFactory
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.dispatch.models import (
    AssignmentStatus,
    DeliveryAssignment,
    DispatchOverride,
    JobOfferStatus,
)
from apps.dispatch.tests.factories import JobOfferFactory

pytestmark = pytest.mark.django_db


def test_only_one_active_assignment_per_delivery_request_at_db_level() -> None:
    delivery_request = DeliveryRequestFactory()
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()
    DeliveryAssignment.objects.create(
        delivery_request=delivery_request, courier=courier_a, status=AssignmentStatus.ACTIVE
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAssignment.objects.create(
            delivery_request=delivery_request, courier=courier_b, status=AssignmentStatus.ACTIVE
        )

    assert (
        DeliveryAssignment.objects.filter(
            delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
        ).count()
        == 1
    )


def test_reassigned_status_does_not_conflict_with_a_new_active_assignment() -> None:
    """A REASSIGNED row (a past assignment) coexists fine with a new ACTIVE
    one for the same delivery — the constraint only guards ACTIVE rows."""
    delivery_request = DeliveryRequestFactory()
    courier_a = CourierProfileFactory()
    courier_b = CourierProfileFactory()
    DeliveryAssignment.objects.create(
        delivery_request=delivery_request, courier=courier_a, status=AssignmentStatus.REASSIGNED
    )
    DeliveryAssignment.objects.create(
        delivery_request=delivery_request, courier=courier_b, status=AssignmentStatus.ACTIVE
    )

    assert DeliveryAssignment.objects.filter(delivery_request=delivery_request).count() == 2


def test_dispatch_override_rejects_blank_reason() -> None:
    delivery_request = DeliveryRequestFactory()
    courier = CourierProfileFactory()
    override = DispatchOverride(
        delivery_request=delivery_request,
        override_type="note",
        reason="   ",
        chosen_courier=courier,
    )
    with pytest.raises(ValidationError):
        override.save()


def test_dispatch_override_accepts_a_real_reason() -> None:
    delivery_request = DeliveryRequestFactory()
    courier = CourierProfileFactory()
    override = DispatchOverride(
        delivery_request=delivery_request,
        override_type="note",
        reason="Courier requested by facility.",
        chosen_courier=courier,
    )
    override.save()
    assert override.pk is not None


def test_job_offer_is_expired_reflects_expires_at() -> None:
    expired_offer = JobOfferFactory(
        expires_at=timezone.now() - datetime.timedelta(minutes=1), status=JobOfferStatus.OFFERED
    )
    live_offer = JobOfferFactory(
        expires_at=timezone.now() + datetime.timedelta(minutes=30), status=JobOfferStatus.OFFERED
    )

    assert expired_offer.is_expired is True
    assert live_offer.is_expired is False


def test_job_offer_is_expired_false_once_no_longer_offered() -> None:
    """An offer that already moved to a terminal status is not "expired" even
    if its expires_at has passed — it's just no longer relevant."""
    offer = JobOfferFactory(
        expires_at=timezone.now() - datetime.timedelta(minutes=1),
        status=JobOfferStatus.CANCELLED,
    )
    assert offer.is_expired is False
