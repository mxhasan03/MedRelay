"""Model-level tests for DeliveryRequest, DeliveryStop, PricingRule seed data,
and RecurringRoute."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.deliveries.models import DeliveryStop, PricingRule, PricingRuleKey, StopType
from apps.deliveries.tests.factories import (
    DeliveryRequestFactory,
    DeliveryStopFactory,
    RecurringRouteFactory,
)
from apps.facilities.tests.factories import FacilityFactory

pytestmark = pytest.mark.django_db


def test_delivery_request_id_is_a_uuid() -> None:
    import uuid

    delivery_request = DeliveryRequestFactory()
    assert isinstance(delivery_request.pk, uuid.UUID)


def test_delivery_request_version_defaults_to_one() -> None:
    delivery_request = DeliveryRequestFactory()
    assert delivery_request.version == 1


def test_delivery_request_clean_rejects_prohibited_keywords_in_instructions() -> None:
    delivery_request = DeliveryRequestFactory(
        facility_instructions="This package contains a human organ for transplant."
    )
    with pytest.raises(ValidationError):
        delivery_request.full_clean()


def test_delivery_request_clean_allows_ordinary_instructions() -> None:
    delivery_request = DeliveryRequestFactory(
        facility_instructions="Ring the bell at the loading dock."
    )
    delivery_request.full_clean()  # should not raise


def test_delivery_stop_unique_per_type_per_delivery_request() -> None:
    delivery_request = DeliveryRequestFactory()
    DeliveryStopFactory(delivery_request=delivery_request, stop_type=StopType.PICKUP, sequence=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryStop.objects.create(
            delivery_request=delivery_request,
            stop_type=StopType.PICKUP,
            sequence=2,
            facility=FacilityFactory(),
        )


def test_pricing_rules_seeded_by_migration_cover_every_key() -> None:
    seeded_keys = set(PricingRule.objects.values_list("key", flat=True))
    assert seeded_keys == set(PricingRuleKey.values)


def test_recurring_route_basic_fields() -> None:
    route = RecurringRouteFactory(
        frequency="weekly", weekly_days_of_week=[0, 2, 4], is_approved=False, is_paused=False
    )
    assert route.frequency == "weekly"
    assert route.weekly_days_of_week == [0, 2, 4]
    assert route.is_approved is False
    assert route.is_paused is False


def test_recurring_route_holiday_exceptions_is_a_simple_date_list() -> None:
    route = RecurringRouteFactory(holiday_exceptions=["2026-12-25", "2026-01-01"])
    route.refresh_from_db()
    assert route.holiday_exceptions == ["2026-12-25", "2026-01-01"]


def test_recurring_route_pause_resume_flags_are_independently_toggleable() -> None:
    route = RecurringRouteFactory(is_paused=False)
    route.is_paused = True
    route.save(update_fields=["is_paused"])
    route.refresh_from_db()
    assert route.is_paused is True


def test_recurring_route_end_date_may_be_open_ended() -> None:
    route = RecurringRouteFactory(end_date=None)
    assert route.end_date is None


def test_recurring_route_stop_sequence_unique_per_route() -> None:
    from apps.deliveries.models import RecurringRouteStop

    route = RecurringRouteFactory()
    RecurringRouteStop.objects.create(
        recurring_route=route, sequence=1, stop_type=StopType.PICKUP, facility=FacilityFactory()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RecurringRouteStop.objects.create(
            recurring_route=route,
            sequence=1,
            stop_type=StopType.DESTINATION,
            facility=FacilityFactory(),
        )


def test_recurring_route_id_is_a_uuid() -> None:
    import uuid

    route = RecurringRouteFactory()
    assert isinstance(route.pk, uuid.UUID)
