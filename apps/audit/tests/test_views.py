"""HTTP-level permission tests for the audit viewer — scoped to
compliance-reviewer/operations-manager/system-administrator internal roles
only, per `apps.organizations.services.can_view_audit_log`."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory
from apps.audit.models import AuditEvent, AuditEventType
from apps.organizations.tests.factories import OrganizationMembershipFactory

pytestmark = pytest.mark.django_db

DEMO_PASSWORD = "AuditViewTest!2026"  # pragma: allowlist secret


def _login_as(client: Client, user) -> None:
    user.set_password(DEMO_PASSWORD)
    user.save()
    client.force_login(user)


def test_compliance_reviewer_can_view_the_audit_log() -> None:
    user = UserFactory()
    InternalRoleAssignment.objects.create(user=user, role=InternalRole.COMPLIANCE_REVIEWER)
    AuditEvent.objects.create(
        event_type=AuditEventType.LOGIN_SUCCEEDED, actor_label="x", summary="x logged in"
    )

    client = Client()
    _login_as(client, user)
    response = client.get(reverse("audit-event-list"))

    assert response.status_code == 200
    assert b"x logged in" in response.content


def test_ordinary_customer_org_member_is_denied() -> None:
    membership = OrganizationMembershipFactory(user=UserFactory())

    client = Client()
    _login_as(client, membership.user)
    response = client.get(reverse("audit-event-list"))

    assert response.status_code == 403


def test_internal_staff_without_an_allowlisted_role_is_denied() -> None:
    user = UserFactory()
    InternalRoleAssignment.objects.create(user=user, role=InternalRole.CUSTOMER_SUPPORT)

    client = Client()
    _login_as(client, user)
    response = client.get(reverse("audit-event-list"))

    assert response.status_code == 403


def test_anonymous_user_is_redirected_to_login() -> None:
    client = Client()
    response = client.get(reverse("audit-event-list"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]
