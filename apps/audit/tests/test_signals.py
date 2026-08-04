"""Real, end-to-end signal-driven audit capture: a genuine login/logout/
failed-login through the real `/accounts/login/` view, and a genuine
OrganizationMembership/InternalRoleAssignment save — not a call to the
signal handler function directly."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.audit.models import AuditEvent, AuditEventType
from apps.organizations.models import CustomerRole
from apps.organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory

pytestmark = pytest.mark.django_db

DEMO_PASSWORD = "AuditSignalTest!2026"  # pragma: allowlist secret


def test_successful_login_is_captured() -> None:
    user = UserFactory()
    user.set_password(DEMO_PASSWORD)
    user.save()

    client = Client()
    response = client.post(reverse("login"), {"username": user.username, "password": DEMO_PASSWORD})
    assert response.status_code == 302

    event = AuditEvent.objects.get(event_type=AuditEventType.LOGIN_SUCCEEDED)
    assert event.actor_id == user.pk


def test_failed_login_is_captured_without_a_resolved_actor() -> None:
    client = Client()
    client.post(
        reverse("login"),
        {"username": "nonexistent-user", "password": "wrong"},  # pragma: allowlist secret
    )

    event = AuditEvent.objects.get(event_type=AuditEventType.LOGIN_FAILED)
    assert event.actor is None
    assert event.actor_label == "nonexistent-user"


def test_logout_is_captured() -> None:
    user = UserFactory()
    user.set_password(DEMO_PASSWORD)
    user.save()
    client = Client()
    client.post(reverse("login"), {"username": user.username, "password": DEMO_PASSWORD})

    client.post(reverse("logout"))

    event = AuditEvent.objects.get(event_type=AuditEventType.LOGOUT)
    assert event.actor_id == user.pk


def test_creating_an_organization_membership_is_captured() -> None:
    organization = OrganizationFactory()
    user = UserFactory()

    membership = OrganizationMembershipFactory(
        organization=organization, user=user, role=CustomerRole.OWNER
    )

    event = AuditEvent.objects.get(event_type=AuditEventType.MEMBERSHIP_CREATED)
    assert event.actor_id == user.pk
    assert event.organization_id == organization.pk
    assert event.metadata["role"] == membership.role


def test_changing_a_membership_role_is_captured_but_unrelated_saves_are_not() -> None:
    membership = OrganizationMembershipFactory(
        user=UserFactory(), role=CustomerRole.REQUESTER_DISPATCHER
    )
    initial_count = AuditEvent.objects.filter(event_type=AuditEventType.MEMBERSHIP_CHANGED).count()

    # Re-saving with no actual field change must not create a spurious "changed" row.
    membership.save()
    assert (
        AuditEvent.objects.filter(event_type=AuditEventType.MEMBERSHIP_CHANGED).count()
        == initial_count
    )

    membership.role = CustomerRole.ADMINISTRATOR
    membership.save()
    event = AuditEvent.objects.filter(event_type=AuditEventType.MEMBERSHIP_CHANGED).latest(
        "occurred_at"
    )
    assert event.metadata["old_role"] == CustomerRole.REQUESTER_DISPATCHER
    assert event.metadata["new_role"] == CustomerRole.ADMINISTRATOR


def test_assigning_an_internal_role_is_captured() -> None:
    user = UserFactory()
    InternalRoleAssignment.objects.create(user=user, role=InternalRole.DISPATCHER)

    event = AuditEvent.objects.get(event_type=AuditEventType.INTERNAL_ROLE_ASSIGNED)
    assert event.actor_id == user.pk
    assert event.metadata["role"] == InternalRole.DISPATCHER
