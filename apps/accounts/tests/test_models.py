"""Tests for the custom user model and internal-role assignment."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_auth_user_model_is_custom_user() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert User.__name__ == "User"


def test_user_str_prefers_full_name() -> None:
    user = UserFactory(username="janedoe", first_name="Jane", last_name="Doe")
    assert str(user) == "Jane Doe"


def test_user_str_falls_back_to_username_when_no_name() -> None:
    user = UserFactory(username="noname", first_name="", last_name="")
    assert str(user) == "noname"


def test_user_defaults_to_not_internal_staff() -> None:
    user = UserFactory(username="plainuser")
    assert user.is_internal_staff is False


def test_creating_internal_role_assignment_sets_is_internal_staff() -> None:
    user = UserFactory(username="futuredispatcher", is_internal_staff=False)
    assert user.is_internal_staff is False

    InternalRoleAssignment.objects.create(user=user, role=InternalRole.DISPATCHER)

    user.refresh_from_db()
    assert user.is_internal_staff is True


def test_internal_role_assignment_str() -> None:
    user = UserFactory(username="opsmgr", first_name="Ops", last_name="Manager")
    assignment = InternalRoleAssignment.objects.create(
        user=user, role=InternalRole.OPERATIONS_MANAGER
    )
    assert "Ops Manager" in str(assignment)
    assert "Operations Manager" in str(assignment)


def test_internal_role_assignment_is_one_per_user() -> None:
    user = UserFactory(username="onerole")
    InternalRoleAssignment.objects.create(user=user, role=InternalRole.FINANCE)

    with pytest.raises(IntegrityError), transaction.atomic():
        InternalRoleAssignment.objects.create(user=user, role=InternalRole.DISPATCHER)
