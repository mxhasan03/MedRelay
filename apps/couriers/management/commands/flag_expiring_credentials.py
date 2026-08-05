"""List courier credentials that are already expired or expiring soon.

Per docs/IMPLEMENTATION_ROADMAP.md Phase 3 ("credential expiration
warnings") and docs/PRODUCT_REQUIREMENTS.md section 6 ("credential
expirations"): this is query/flagging logic only. It prints a report to
stdout. It does **not** send an email, SMS, or in-app notification — real
notifications are Phase 7 work (`apps.notifications`), not built here.

Usage:
    python manage.py flag_expiring_credentials [--within-days 30]

The actual "expired"/"expiring soon" query logic lives on
`CourierCredentialQuerySet.expired`/`.expiring_within`
(`apps.couriers.models`); this command calls
`apps.couriers.services.credential_expiration_summary` (courier=None, i.e.
across every courier) — the same shared function the courier-facing
profile screen (`apps.couriers.views.CourierProfileView`) calls scoped to
one courier — so the definition of "expiring" is never duplicated between
the two call sites.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.couriers.services import credential_expiration_summary


class Command(BaseCommand):
    help = (
        "Report courier credentials that are already expired or will expire within N days. "
        "Reporting/flagging only — sends no real notification (that is Phase 7 work)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--within-days",
            type=int,
            default=30,
            help="Flag approved credentials expiring within this many days (default: 30).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        within_days: int = options["within_days"]
        today = timezone.localdate()

        summary = credential_expiration_summary(within_days=within_days, as_of=today)
        expired = summary.expired
        expiring_soon = summary.expiring_soon

        self.stdout.write(f"Credential expiration report — as of {today.isoformat()}")
        self.stdout.write("")

        self.stdout.write(self.style.ERROR(f"Already expired ({len(expired)}):"))
        for credential in expired:
            self.stdout.write(
                f"  - {credential.courier} — {credential.get_credential_type_display()} "
                f"expired {credential.expires_on}"
            )
        if not expired:
            self.stdout.write("  (none)")

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(f"Expiring within {within_days} day(s) ({len(expiring_soon)}):")
        )
        for credential in expiring_soon:
            self.stdout.write(
                f"  - {credential.courier} — {credential.get_credential_type_display()} "
                f"expires {credential.expires_on}"
            )
        if not expiring_soon:
            self.stdout.write("  (none)")
