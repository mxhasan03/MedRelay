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
