"""AppConfig for the facilities app.

Customer facilities, contacts, receiving rules, and service zones. No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class FacilitiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.facilities"
    label = "facilities"
    verbose_name = "Facilities"
