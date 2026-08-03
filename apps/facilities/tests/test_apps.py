"""Smoke tests for the facilities app.

Phase 0 has no domain models yet; this only proves the app is installed,
importable, and wired into INSTALLED_APPS correctly.
"""

from django.apps import apps as django_apps


def test_facilities_app_is_installed() -> None:
    assert django_apps.is_installed("apps.facilities")


def test_facilities_app_config_importable() -> None:
    config = django_apps.get_app_config("facilities")
    assert config.name == "apps.facilities"
