"""Generic append-only `AuditEvent` log — Phase 8.

Architecture decision (docs/CURRENT_STATUS.md "Phase 8" section has the full
write-up): `docs/ARCHITECTURE_AND_DATA_MODEL.md` lists a single `AuditEvent`
entity, and `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 6 lists a much
longer "record these" list that includes several event families this
codebase *already* logs in their own dedicated, purpose-built tables:

- delivery state transitions -> `apps.deliveries.models.DeliveryStatusTransition`
- assignment overrides -> `apps.dispatch.models.DispatchOverride`
- custody events -> `apps.custody.models.CustodyEvent` (a real hash chain —
  strictly stronger tamper evidence than this generic log provides)
- incident actions -> `apps.incidents.models.IncidentAction`
- export creation -> `apps.reporting.models.ExportJob`

Duplicating all of that into one wide `AuditEvent` table would either lose
information (a lowest-common-denominator schema) or become a second,
competing source of truth for data that already has one. Instead, this model
is scoped to exactly the two event families `docs/SECURITY_COMPLIANCE_BOUNDARIES.md`
section 6 asks for that have **no existing home** anywhere in the codebase:

1. **Authentication events** (login succeeded, login failed, logout) — new
   capture points wired via `django.contrib.auth.signals` in
   `apps/audit/signals.py`.
2. **Role/membership changes** — `apps.organizations.models.OrganizationMembership`
   creation/role-change/deactivation and
   `apps.accounts.models.InternalRoleAssignment` creation/role-change, also
   wired via Django signals in `apps/audit/signals.py`.

`apps/audit/views.py`'s audit viewer UI surfaces this generic log *and*
cross-links to the five existing per-domain logs above, rather than
re-rendering their rows here — see that module's docstring.

Data minimization: `summary`/`metadata` carry only operational identifiers
(usernames, organization/role names, delivery-request IDs) — never a
diagnosis, lab result, clinical note, or other field
docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 2 prohibits. No IP address or
user-agent is captured, matching the same minimization choice already made
for `apps.recipient.models.RecipientLinkAccessLog`.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AuditEventType(models.TextChoices):
    LOGIN_SUCCEEDED = "login_succeeded", "Login Succeeded"
    LOGIN_FAILED = "login_failed", "Login Failed"
    LOGOUT = "logout", "Logout"
    MEMBERSHIP_CREATED = "membership_created", "Organization Membership Created"
    MEMBERSHIP_CHANGED = "membership_changed", "Organization Membership Changed"
    INTERNAL_ROLE_ASSIGNED = "internal_role_assigned", "Internal Role Assigned"
    INTERNAL_ROLE_CHANGED = "internal_role_changed", "Internal Role Changed"


class AuditEventQuerySet(models.QuerySet["AuditEvent"]):
    """Blocks queryset-level bulk mutation, complementing the instance-level
    guard below — the same append-only pattern already established by
    `apps.deliveries.models.DeliveryStatusTransition` and
    `apps.custody.models.CustodyEvent`."""

    def update(self, *args: Any, **kwargs: Any) -> int:
        raise ValidationError(
            "AuditEvent rows are append-only; bulk queryset.update() is not allowed."
        )

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "AuditEvent rows are append-only; bulk queryset.delete() is not allowed."
        )


class AuditEvent(models.Model):
    """One authentication or role/membership-change event.

    Same honest limitation already documented on
    `DeliveryStatusTransition`: append-only is enforced at the ORM level
    (`save()`/`delete()` below plus the queryset guard), not by a database
    trigger or REVOKE grant — a raw SQL statement issued outside the ORM
    could still bypass it.
    """

    event_type = models.CharField(max_length=32, choices=AuditEventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        help_text="The user who performed or was subject to this event, if resolvable.",
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        help_text=(
            "Fallback identifier when no User row is resolvable (e.g. a failed login attempt "
            "against a username that does not exist)."
        ),
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        help_text="Set for organization-membership events; blank for internal-role/auth events.",
    )
    summary = models.CharField(
        max_length=255,
        help_text="Short, human-readable description. Operational identifiers only, never PHI.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Small structured detail (e.g. {'role': 'owner'}). Never PHI.",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["-occurred_at"]),
            models.Index(fields=["event_type", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()}: {self.summary}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ValidationError(
                "AuditEvent rows are append-only; an existing row cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("AuditEvent rows are append-only; existing rows cannot be deleted.")
