"""AppConfig for the notifications app.

In-app notifications, local email (Mailpit), and simulated SMS adapters.
No domain models yet in Phase 0.
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = "Notifications"
