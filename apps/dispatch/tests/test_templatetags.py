"""Unit tests for `apps.dispatch.templatetags.dispatch_badges` — the plain
Python filter/tag logic behind the dispatch-board badges, independent of
any template rendering or database access."""

from __future__ import annotations

import datetime

from django.http import QueryDict
from django.utils import timezone

from apps.deliveries.models import DeliveryStatus
from apps.dispatch.sla import AT_RISK, FEASIBLE, INFEASIBLE, NOT_EVALUATED
from apps.dispatch.templatetags.dispatch_badges import (
    dict_get,
    eligibility_badge_class,
    eligibility_label,
    relative_time,
    sla_risk_badge_class,
    sla_risk_label,
    status_badge_class,
    toggle_sort_url,
)


def test_status_badge_class_covers_every_delivery_status() -> None:
    # Every DeliveryStatus value must map to a real badge class, not the
    # unrecognized-value fallback — this test fails loudly the moment a
    # future DeliveryStatus is added without updating the badge palette.
    for status in DeliveryStatus.values:
        assert status_badge_class(status).startswith("badge-")


def test_status_badge_class_groups_match_the_documented_palette() -> None:
    assert status_badge_class(DeliveryStatus.DRAFT) == "badge-neutral"
    assert status_badge_class(DeliveryStatus.READY_FOR_DISPATCH) == "badge-blue"
    assert status_badge_class(DeliveryStatus.ASSIGNED) == "badge-amber"
    assert status_badge_class(DeliveryStatus.DELIVERED) == "badge-green"
    assert status_badge_class(DeliveryStatus.INCIDENT_HOLD) == "badge-red"
    assert status_badge_class("some_future_status_not_yet_added") == "badge-neutral"


def test_eligibility_badge_helpers() -> None:
    assert eligibility_badge_class(True) == "badge-green"
    assert eligibility_badge_class(False) == "badge-red"
    assert eligibility_label(True) == "Eligible"
    assert eligibility_label(False) == "Ineligible"


def test_sla_risk_badge_helpers_distinguish_at_risk_from_infeasible() -> None:
    assert sla_risk_badge_class(AT_RISK) == "badge-amber"
    assert sla_risk_badge_class(INFEASIBLE) == "badge-red"
    assert sla_risk_label(AT_RISK) == "AT RISK"
    assert sla_risk_label(INFEASIBLE) == "INFEASIBLE"
    # Feasible/not-evaluated/None render no badge at all — an unremarkable
    # delivery should not visually compete with genuinely at-risk ones.
    assert sla_risk_badge_class(FEASIBLE) == ""
    assert sla_risk_badge_class(NOT_EVALUATED) == ""
    assert sla_risk_badge_class(None) == ""


def test_dict_get_returns_value_or_none() -> None:
    mapping = {"a": 1}
    assert dict_get(mapping, "a") == 1
    assert dict_get(mapping, "missing") is None
    assert dict_get(None, "a") is None


def test_relative_time_formats_minutes_hours_days() -> None:
    now = timezone.now()
    assert relative_time(None) == ""
    assert relative_time(now) == "just now"
    assert relative_time(now - datetime.timedelta(minutes=4)) == "4 min ago"
    assert relative_time(now - datetime.timedelta(hours=2)) == "2 hr ago"
    assert relative_time(now - datetime.timedelta(days=3)) == "3 days ago"
    assert relative_time(now - datetime.timedelta(days=1)) == "1 day ago"


def test_toggle_sort_url_sets_ascending_first_then_flips_to_descending() -> None:
    empty = QueryDict(mutable=True)
    first_click = toggle_sort_url(empty, "unassigned_sort", "service_level")
    assert first_click == "?unassigned_sort=service_level"

    already_ascending = QueryDict("unassigned_sort=service_level")
    second_click = toggle_sort_url(already_ascending, "unassigned_sort", "service_level")
    assert second_click == "?unassigned_sort=-service_level"


def test_toggle_sort_url_preserves_other_query_params() -> None:
    query = QueryDict("unassigned_org=3&assigned_sort=required_delivery_by")
    url = toggle_sort_url(query, "unassigned_sort", "service_level")
    result = QueryDict(url.lstrip("?"))
    assert result["unassigned_org"] == "3"
    assert result["assigned_sort"] == "required_delivery_by"
    assert result["unassigned_sort"] == "service_level"
