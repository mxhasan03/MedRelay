"""AppConfig for the accounts app.

Users, authentication, and privileged-role support (MFA-ready). No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"
