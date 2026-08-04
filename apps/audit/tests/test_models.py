"""Append-only enforcement for AuditEvent, matching the same pattern already
covered for DeliveryStatusTransition/CustodyEvent."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent, AuditEventType

pytestmark = pytest.mark.django_db


def test_audit_event_can_be_created() -> None:
    event = AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED,
        actor_label="someone",
        summary="someone logged in",
    )
    assert event.pk is not None


def test_existing_audit_event_row_cannot_be_updated() -> None:
    event = AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED, actor_label="a", summary="a logged in"
    )
    event.summary = "tampered"
    with pytest.raises(ValidationError):
        event.save()


def test_audit_event_row_cannot_be_deleted() -> None:
    event = AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED, actor_label="a", summary="a logged in"
    )
    with pytest.raises(ValidationError):
        event.delete()


def test_bulk_queryset_update_is_blocked() -> None:
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED, actor_label="a", summary="a logged in"
    )
    with pytest.raises(ValidationError):
        AuditEvent.objects.all().update(summary="tampered")


def test_bulk_queryset_delete_is_blocked() -> None:
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED, actor_label="a", summary="a logged in"
    )
    with pytest.raises(ValidationError):
        AuditEvent.objects.all().delete()
