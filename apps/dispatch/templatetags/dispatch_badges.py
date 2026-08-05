"""Template filters that turn raw dispatch-board values (delivery status,
eligibility booleans, SLA feasibility strings, timestamps) into the
color-coded `.badge` component classes/labels defined in
`templates/base.html`'s `@layer components` block.

Kept in `apps.dispatch` (not a project-wide `apps.core`/`common` app,
which does not exist in this codebase) since every current caller is a
dispatch-board template; `templates/incidents/*` reuses only the plain
`.badge-*` CSS classes directly, not these Python filters, so this module
does not need to be shared across apps. See docs/CURRENT_STATUS.md's
Phase 4/8 write-ups for why this repository keeps template logic close to
the app that owns the page, rather than centralizing it prematurely.
"""

from __future__ import annotations

import datetime
from typing import Any

from django import template
from django.http import QueryDict
from django.utils import timezone

from apps.deliveries.models import DeliveryStatus
from apps.dispatch.sla import AT_RISK, INFEASIBLE

register = template.Library()

# Every DeliveryStatus value is listed explicitly so a future enum addition
# fails loudly (KeyError caught below, falling back to neutral) rather than
# silently rendering an unstyled badge. Palette follows this pass's brief:
# neutral gray (not yet actionable / terminal-and-uneventful), blue
# (validated/ready or actively moving), amber (needs dispatcher
# attention/in progress), green (successfully completed), red
# (exception/failure) — kept consistent with the existing rose-800 brand
# accent used for buttons/links elsewhere (badges are a deliberately
# separate, desaturated palette so they never compete with the primary
# action color).
_STATUS_BADGE_CLASSES: dict[str, str] = {
    DeliveryStatus.DRAFT: "badge-neutral",
    DeliveryStatus.SUBMITTED: "badge-neutral",
    DeliveryStatus.VALIDATION_REQUIRED: "badge-blue",
    DeliveryStatus.READY_FOR_DISPATCH: "badge-blue",
    DeliveryStatus.OFFERED: "badge-amber",
    DeliveryStatus.ASSIGNED: "badge-amber",
    DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP: "badge-blue",
    DeliveryStatus.AT_PICKUP: "badge-blue",
    DeliveryStatus.PICKED_UP: "badge-blue",
    DeliveryStatus.IN_TRANSIT: "badge-blue",
    DeliveryStatus.AT_DESTINATION: "badge-blue",
    DeliveryStatus.DELIVERED: "badge-green",
    DeliveryStatus.REJECTED: "badge-red",
    DeliveryStatus.CANCELLED: "badge-neutral",
    DeliveryStatus.INCIDENT_HOLD: "badge-red",
    DeliveryStatus.RETURNING: "badge-amber",
    DeliveryStatus.RETURNED: "badge-neutral",
    DeliveryStatus.FAILED: "badge-red",
}


@register.filter
def status_badge_class(status: str) -> str:
    """`.badge-*` class for a raw `DeliveryStatus` value; `.badge-neutral`
    for anything unrecognized (defensive, should not happen — see the
    module-level dict's completeness note)."""
    return _STATUS_BADGE_CLASSES.get(status, "badge-neutral")


@register.filter
def eligibility_badge_class(is_eligible: bool) -> str:
    return "badge-green" if is_eligible else "badge-red"


@register.filter
def eligibility_label(is_eligible: bool) -> str:
    return "Eligible" if is_eligible else "Ineligible"


# SLA feasibility → (badge class, short label). `FEASIBLE`/`NOT_EVALUATED`
# deliberately render no badge at all in the templates that use this (see
# `sla_risk_badge_class`/`sla_risk_label` below returning ""/None for them)
# — a normal/unremarkable delivery should not compete visually with the
# genuinely at-risk ones.
_SLA_RISK_BADGE_CLASSES: dict[str, str] = {
    AT_RISK: "badge-amber",
    INFEASIBLE: "badge-red",
}
_SLA_RISK_LABELS: dict[str, str] = {
    AT_RISK: "AT RISK",
    INFEASIBLE: "INFEASIBLE",
}


@register.filter
def sla_risk_badge_class(feasibility: str | None) -> str:
    """ "" (no badge) for `FEASIBLE`/`NOT_EVALUATED`/`None`; a distinct amber
    vs. red `.badge-*` class for `AT_RISK` vs. `INFEASIBLE` — see
    `apps.dispatch.services.sla_risk_by_delivery_id`'s docstring for why
    this distinction is real, not cosmetic: `DispatchCandidate.sla_feasibility`
    already separates "slack getting thin" from "mathematically cannot make
    the deadline"."""
    return _SLA_RISK_BADGE_CLASSES.get(feasibility or "", "")


@register.filter
def sla_risk_label(feasibility: str | None) -> str:
    return _SLA_RISK_LABELS.get(feasibility or "", "")


@register.filter
def dict_get(mapping: dict[Any, Any] | None, key: Any) -> Any:
    """Dynamic-key dict lookup — Django templates have no `mapping[key]`
    syntax for a variable key (only a literal-key `mapping.key` attribute/
    item lookup), so callers that need `some_dict|dict_get:some_variable`
    (e.g. `sla_risk_by_delivery_id|dict_get:delivery_request.pk`,
    `latest_ping_by_courier_id|dict_get:candidate.courier.pk`) go through
    this filter instead."""
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def relative_time(value: datetime.datetime | None) -> str:
    """A short "N min/hr/day ago" string for `value` (assumed to be in the
    past). Deliberately not `django.contrib.humanize` (not an installed
    app in this project — see config/settings/base.py's INSTALLED_APPS —
    and pulling in a whole new app for one filter would be needless for a
    single "minutes/hours ago" label)."""
    if value is None:
        return ""
    now = timezone.now()
    delta = now - value
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "just now"
    if total_seconds < 60:
        return "just now"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


@register.simple_tag
def toggle_sort_url(query_dict: QueryDict | dict[str, Any], param: str, field: str) -> str:
    """Build the full `?...` query string for a plain-link sortable column
    header: sets `param=field`, or `param=-field` to flip direction if
    `field` is already the active sort for `param` — no JS, matching this
    page's existing "plain link, full page reload" convention (see
    `docs/CURRENT_STATUS.md`'s Phase 4 scoping note on this page). Every
    other existing GET parameter (e.g. an organization filter, or the other
    table's independent sort param) is preserved untouched.

    `query_dict` is `request.GET` (a real `QueryDict` in every template
    this is used from); a plain `dict` is also accepted so this stays
    trivially unit-testable without constructing a request.
    """
    if isinstance(query_dict, QueryDict):
        new_query = query_dict.copy()
    else:
        new_query = QueryDict(mutable=True)
        for key, value in query_dict.items():
            new_query[key] = str(value)
    current = new_query.get(param, "")
    new_query[param] = f"-{field}" if current == field else field
    return "?" + new_query.urlencode()
