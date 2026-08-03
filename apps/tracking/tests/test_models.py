"""Tests for apps.tracking.models.CourierLocationPing."""

from __future__ import annotations

import pytest

from apps.tracking.models import CourierLocationPing
from apps.tracking.tests.factories import CourierLocationPingFactory

pytestmark = pytest.mark.django_db


def test_courier_location_ping_str_includes_courier_and_coordinates() -> None:
    ping = CourierLocationPingFactory()
    text = str(ping)
    assert str(ping.courier) in text
    assert str(ping.latitude) in text


def test_courier_location_ping_courier_matches_assignment_courier() -> None:
    ping = CourierLocationPingFactory()
    assert ping.courier_id == ping.assignment.courier_id


def test_courier_location_ping_ordering_is_most_recent_first() -> None:
    first = CourierLocationPingFactory()
    second = CourierLocationPingFactory(
        assignment=first.assignment, courier=first.assignment.courier
    )

    pings = list(CourierLocationPing.objects.filter(assignment=first.assignment))

    assert pings == [second, first]
