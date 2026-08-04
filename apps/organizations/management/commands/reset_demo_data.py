"""Phase 9 quota/abuse safeguard: wipe all demo-created data and reseed a
fresh, deterministic dataset.

docs/IMPLEMENTATION_ROADMAP.md's Phase 9 "Quota/abuse safeguards" calls for
"automatic cleanup/reset of demo-created data (a management command a cron
could call, even if you don't wire up the actual cron here)". This is that
command — no cron/scheduled-task infrastructure is wired up in this
repository (Celery has no periodic-task schedule configured anywhere; see
`config/celery.py`), by design, per the task's own "even if you don't wire
up the actual cron here." A real public demo deployment's operator would
point an external cron (or their hosting platform's scheduled-job feature)
at `python manage.py reset_demo_data --yes` on whatever cadence they choose
— that decision is part of the hosting choice documented in
`docs/HOSTING_OPTIONS.md`, not something this repository should assume.

## What this deletes

Every `Invoice` and `DeliveryRequest` row, every `Organization` row, and
every `User` whose email ends in `@medrelay.demo` (the exact, consistent
domain `seed_demo_data`/`seed_full_demo` give every seeded user — internal
staff, customer-org members, and couriers alike) — in that order.
`Invoice.organization`/`Invoice.delivery_request` and
`DeliveryRequest.organization` are all `on_delete=models.PROTECT` (a
deliberate financial/audit-trail safeguard elsewhere in this codebase, not
something this command should weaken), so `Invoice`/`DeliveryRequest` rows
must be deleted *before* their `Organization` can be; deleting them first
also cascades (Django's default `on_delete=models.CASCADE` used by
everything else that hangs off a `DeliveryRequest`) through custody events,
dispatch assignments, incidents, temperature readings, packages, and so on.
Deleting `Organization` afterward cascades to `Facility`,
`OrganizationMembership`, and everything else FK'd to it; deleting the
`@medrelay.demo` users last cascades to `CourierProfile`/
`InternalRoleAssignment` (any `OrganizationMembership` row is already gone
by then via the `Organization` cascade).

This deliberately does **not** touch:

- Fixed reference/lookup data seeded by migrations, not this command:
  `CargoClass`/`CargoPolicy`/`TemperatureProfile` (`apps.cargo`),
  `PricingRule` (`apps.deliveries`), `SLAProfile` (`apps.dispatch`). These
  are not "demo-created data" — they are the same fixed taxonomy every
  environment (including a real pilot, should one ever be authorized) would
  ship with.
- Any real superuser/admin account created via `createsuperuser` for an
  operator's own access — such an account would not have an
  `@medrelay.demo` email address, so it is never touched by this command.

## Why a destructive reset is acceptable here

Every row this command can possibly delete is, by this project's own
data-minimization policy (`docs/SECURITY_COMPLIANCE_BOUNDARIES.md`),
synthetic — there is no real patient, customer, or courier data anywhere in
this codebase's `DEMO_MODE`-only operating mode (`CLAUDE.md` "Operating
mode: DEMO_MODE only"). This command must never be pointed at anything
other than a demo/test database; it does not check `settings.APP_MODE`
before running (this prototype has exactly one supported mode), but the
`--yes` confirmation gate below is still required unless explicitly
skipped, as a plain "did you mean to do this" guard against an accidental
interactive run against the wrong database.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.billing.models import Invoice
from apps.deliveries.models import DeliveryRequest
from apps.organizations.models import Organization

User = get_user_model()

DEMO_USER_EMAIL_SUFFIX = "@medrelay.demo"


class Command(BaseCommand):
    help = (
        "Delete all demo-created data (every @medrelay.demo user and every organization, "
        "cascading to everything that hangs off them) and reseed a fresh, deterministic demo "
        "dataset via seed_demo_data + seed_full_demo. Intended to be invoked periodically by an "
        "external cron/scheduled job in a real public demo deployment (not wired up in this "
        "repository — see module docstring and docs/HOSTING_OPTIONS.md)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help=(
                "Skip the interactive confirmation prompt (required for non-interactive/cron use)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["yes"]:
            confirmation = input(
                "This will permanently delete ALL demo-created data (every @medrelay.demo user "
                "and every organization) from this database and reseed a fresh demo dataset. "
                "Type 'yes' to continue: "
            )
            if confirmation.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted — no data was changed."))
                return

        deleted_users, deleted_orgs = self._wipe()
        self.stdout.write(
            f"Deleted {deleted_users} demo user(s) and {deleted_orgs} organization(s) "
            "(cascading to everything that referenced them)."
        )
        call_command("seed_demo_data")
        call_command("seed_full_demo")
        self.stdout.write(self.style.SUCCESS("Demo data reset complete — fresh dataset reseeded."))

    @transaction.atomic
    def _wipe(self) -> tuple[int, int]:
        # Invoice/DeliveryRequest first — both PROTECT their Organization FK
        # (and Invoice PROTECTs its DeliveryRequest FK too), so Organization
        # cannot be deleted while either still references it. See module
        # docstring "What this deletes".
        Invoice.objects.all().delete()
        DeliveryRequest.objects.all().delete()

        organizations = Organization.objects.all()
        deleted_orgs = organizations.count()
        organizations.delete()

        demo_users = User.objects.filter(email__iendswith=DEMO_USER_EMAIL_SUFFIX)
        deleted_users = demo_users.count()
        demo_users.delete()

        return deleted_users, deleted_orgs
