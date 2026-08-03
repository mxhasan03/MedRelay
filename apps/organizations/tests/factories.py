"""factory_boy factories for organizations and memberships."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.organizations.models import (
    CustomerRole,
    Organization,
    OrganizationMembership,
    OrganizationType,
)


class OrganizationFactory(DjangoModelFactory):
    class Meta:
        model = Organization
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Test Org {n} (Demo)")
    org_type = OrganizationType.CLINIC


class OrganizationMembershipFactory(DjangoModelFactory):
    class Meta:
        model = OrganizationMembership

    organization = factory.SubFactory(OrganizationFactory)
    role = CustomerRole.OWNER
