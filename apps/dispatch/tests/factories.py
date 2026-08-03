"""factory_boy factories for dispatch assignments, job offers, overrides,
recommendations, route plans, and SLA profiles."""

from __future__ import annotations

import datetime

import factory
from factory.django import DjangoModelFactory

from apps.couriers.tests.factories import CourierProfileFactory
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.dispatch.models import (
    AssignmentStatus,
    DeliveryAssignment,
    DispatchOverride,
    DispatchOverrideType,
    DispatchRecommendation,
    JobOffer,
    JobOfferStatus,
    RoutePlan,
    SLAProfile,
)


class SLAProfileFactory(DjangoModelFactory):
    class Meta:
        model = SLAProfile
        django_get_or_create = ("service_level",)

    service_level = "scheduled"
    min_slack_minutes = 60


class DeliveryAssignmentFactory(DjangoModelFactory):
    class Meta:
        model = DeliveryAssignment

    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    courier = factory.SubFactory(CourierProfileFactory)
    status = AssignmentStatus.ACTIVE


class JobOfferFactory(DjangoModelFactory):
    class Meta:
        model = JobOffer

    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    courier = factory.SubFactory(CourierProfileFactory)
    status = JobOfferStatus.OFFERED
    expires_at = factory.LazyFunction(
        lambda: datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30)
    )


class DispatchRecommendationFactory(DjangoModelFactory):
    class Meta:
        model = DispatchRecommendation

    delivery_request = factory.SubFactory(DeliveryRequestFactory)


class DispatchOverrideFactory(DjangoModelFactory):
    class Meta:
        model = DispatchOverride

    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    override_type = DispatchOverrideType.NOTE
    reason = "Test override reason."
    chosen_courier = factory.SubFactory(CourierProfileFactory)


class RoutePlanFactory(DjangoModelFactory):
    class Meta:
        model = RoutePlan

    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    total_distance_km = 5
    total_duration_minutes = 40
