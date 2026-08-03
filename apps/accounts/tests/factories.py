"""factory_boy factories for the custom user model and internal roles."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import InternalRole, InternalRoleAssignment, User


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"testuser{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    first_name = "Test"
    last_name = "User"


class InternalRoleAssignmentFactory(DjangoModelFactory):
    class Meta:
        model = InternalRoleAssignment

    user = factory.SubFactory(UserFactory)
    role = InternalRole.DISPATCHER
