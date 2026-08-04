"""Template context processors shared across the project."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest

DEMO_DISCLAIMER = (
    "This is a software prototype using synthetic data. It is not certified or approved for "
    "real medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, "
    "or other legal compliance."
)


def app_mode(request: HttpRequest) -> dict[str, Any]:
    """Expose APP_MODE and the required disclaimer text to every template."""
    return {
        "APP_MODE": getattr(settings, "APP_MODE", "DEMO_MODE"),
        "DEMO_DISCLAIMER": DEMO_DISCLAIMER,
    }


def nav(request: HttpRequest) -> dict[str, Any]:
    """Role-aware navigation flags for `templates/partials/nav.html` (Phase 8
    unified design system). Computed once here rather than in every view's
    `get_context_data`, since the top nav is shared by every non-courier,
    non-recipient page. Uses the same permission functions
    (`apps.organizations.services`) every view already uses — this
    processor decides nothing new about access, it only decides what to
    *show a link to*; every linked page still enforces its own permission
    check independently.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "nav_is_internal_staff": False,
            "nav_is_courier": False,
            "nav_can_dispatch": False,
            "nav_can_view_audit": False,
        }

    from apps.organizations.services import can_dispatch, can_view_audit_log

    return {
        "nav_is_internal_staff": bool(getattr(user, "is_internal_staff", False)),
        "nav_is_courier": bool(getattr(user, "is_courier", False)),
        "nav_can_dispatch": can_dispatch(user),
        "nav_can_view_audit": can_view_audit_log(user),
    }
