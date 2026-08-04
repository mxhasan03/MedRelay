"""Signal handlers that write `apps.audit.models.AuditEvent` rows.

Connected from `AuditConfig.ready()` (see `apps/audit/apps.py`) rather than
called explicitly from view/service code — these are cross-cutting capture
points (any login, anywhere; any membership change, from any view, the
admin, or a management command) that are easy to miss with explicit call
sites and cheap to get right once with a signal, matching the "new generic
log for auth/membership" scope decided in `apps/audit/models.py`'s
docstring.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.http import HttpRequest

from apps.accounts.models import InternalRoleAssignment
from apps.audit.models import AuditEvent, AuditEventType
from apps.organizations.models import OrganizationMembership

# Attribute name used to stash the pre-save DB state on an instance so the
# post_save handler can tell what actually changed. Prefixed/underscored and
# deleted after use so it never leaks into anything else.
_SNAPSHOT_ATTR = "_medrelay_audit_pre_save_snapshot"


@receiver(user_logged_in)
def _on_login_succeeded(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED,
        actor=user,
        actor_label=str(user),
        summary=f"{user} logged in",
    )


@receiver(user_logged_out)
def _on_logout(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    # `user` can be None if the session had no authenticated user at all
    # (e.g. logging out an already-anonymous session) — still worth a row
    # for completeness, with a generic label rather than skipping silently.
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGOUT,
        actor=user if user is not None and getattr(user, "pk", None) else None,
        actor_label=str(user) if user is not None else "(anonymous)",
        summary=f"{user} logged out" if user is not None else "Anonymous session logged out",
    )


@receiver(user_login_failed)
def _on_login_failed(sender: Any, credentials: dict[str, Any], **kwargs: Any) -> None:
    # Never store the attempted password (it is not in `credentials` by
    # Django's own design — `user_login_failed` deliberately excludes it —
    # but guard defensively anyway rather than trusting that upstream).
    username = str(credentials.get("username", "")) if credentials else ""
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_FAILED,
        actor=None,
        actor_label=username[:150],
        summary=f"Failed login attempt for username '{username}'",
    )


@receiver(pre_save, sender=OrganizationMembership)
@receiver(pre_save, sender=InternalRoleAssignment)
def _snapshot_before_save(sender: Any, instance: Any, **kwargs: Any) -> None:
    if not instance.pk:
        return
    try:
        previous = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    setattr(instance, _SNAPSHOT_ATTR, previous)


@receiver(post_save, sender=OrganizationMembership)
def _on_membership_saved(
    sender: Any, instance: OrganizationMembership, created: bool, **kwargs: Any
) -> None:
    previous = getattr(instance, _SNAPSHOT_ATTR, None)
    if hasattr(instance, _SNAPSHOT_ATTR):
        delattr(instance, _SNAPSHOT_ATTR)

    if created:
        AuditEvent.objects.create(
            event_type=AuditEventType.MEMBERSHIP_CREATED,
            actor=instance.user,
            actor_label=str(instance.user),
            organization=instance.organization,
            summary=(
                f"{instance.user} added to {instance.organization} as "
                f"{instance.get_role_display()}"
            ),
            metadata={"role": instance.role, "is_active": instance.is_active},
        )
        return

    if previous is None:
        return
    if previous.role == instance.role and previous.is_active == instance.is_active:
        return
    AuditEvent.objects.create(
        event_type=AuditEventType.MEMBERSHIP_CHANGED,
        actor=instance.user,
        actor_label=str(instance.user),
        organization=instance.organization,
        summary=f"{instance.user}'s membership in {instance.organization} changed",
        metadata={
            "old_role": previous.role,
            "new_role": instance.role,
            "old_is_active": previous.is_active,
            "new_is_active": instance.is_active,
        },
    )


@receiver(post_save, sender=InternalRoleAssignment)
def _on_internal_role_saved(
    sender: Any, instance: InternalRoleAssignment, created: bool, **kwargs: Any
) -> None:
    previous = getattr(instance, _SNAPSHOT_ATTR, None)
    if hasattr(instance, _SNAPSHOT_ATTR):
        delattr(instance, _SNAPSHOT_ATTR)

    if created:
        AuditEvent.objects.create(
            event_type=AuditEventType.INTERNAL_ROLE_ASSIGNED,
            actor=instance.user,
            actor_label=str(instance.user),
            summary=f"{instance.user} assigned internal role {instance.get_role_display()}",
            metadata={"role": instance.role},
        )
        return

    if previous is None or previous.role == instance.role:
        return
    AuditEvent.objects.create(
        event_type=AuditEventType.INTERNAL_ROLE_CHANGED,
        actor=instance.user,
        actor_label=str(instance.user),
        summary=f"{instance.user}'s internal role changed",
        metadata={"old_role": previous.role, "new_role": instance.role},
    )
