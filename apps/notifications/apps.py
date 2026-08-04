"""AppConfig for the notifications app.

Phase 7: in-app notifications (`Notification`), local email delivery to
Mailpit (`EmailLogEntry` + `apps.notifications.providers.
EmailNotificationProvider`), a simulated SMS adapter (`SmsLogEntry` +
`SimulatedSmsProvider` — no real SMS API ever called), and a demo-only,
no-network-call `WebhookDelivery` log. See
`apps.notifications.payload.build_notification_payload` for the
data-minimization boundary every notification/SMS/email/webhook log record
must pass through, and docs/CURRENT_STATUS.md "Phase 7" for the full design
write-up.
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = "Notifications"
