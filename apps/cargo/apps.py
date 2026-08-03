"""AppConfig for the cargo app.

Cargo classes, policies, temperature profiles, packages, package
identifiers, and packaging attestations — see apps/cargo/models.py
(Phase 2, docs/IMPLEMENTATION_ROADMAP.md).
"""

from django.apps import AppConfig


class CargoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cargo"
    label = "cargo"
    verbose_name = "Cargo"
