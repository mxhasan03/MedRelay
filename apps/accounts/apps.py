"""AppConfig for the accounts app.

Custom `User` model (`AUTH_USER_MODEL = "accounts.User"`) and internal
operations role assignments (Phase 1). See apps/accounts/models.py.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"
