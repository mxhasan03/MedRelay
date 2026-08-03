"""Model-level tests for apps.couriers: the User.is_courier sync, credential
expiration querysets, and basic uniqueness constraints."""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction

from apps.couriers.models import (
    CargoAuthorization,
    CourierCredentialStatus,
    CourierCredentialType,
    CourierStatus,
)
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
)

pytestmark = pytest.mark.django_db


def test_creating_courier_profile_sets_user_is_courier_true() -> None:
    courier = CourierProfileFactory()
    courier.user.refresh_from_db()
    assert courier.user.is_courier is True


def test_courier_profile_default_status_is_applicant() -> None:
    # Built directly (not via the factory, whose default overrides status to
    # APPROVED for test convenience) to check the model's own field default.
    from apps.accounts.tests.factories import UserFactory
    from apps.couriers.models import CourierProfile

    applicant = CourierProfile.objects.create(user=UserFactory())
    assert applicant.status == CourierStatus.APPLICANT


def test_cargo_authorization_is_unique_per_courier_per_cargo_class() -> None:
    # `CargoAuthorizationFactory` uses `django_get_or_create`, so calling it
    # again with the same (courier, cargo_class) would just fetch the
    # existing row rather than exercise the uniqueness constraint — create
    # the duplicate directly instead.
    authorization = CargoAuthorizationFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        CargoAuthorization.objects.create(
            courier=authorization.courier, cargo_class=authorization.cargo_class
        )


def test_credential_expiring_within_includes_soon_expiring_approved_credential() -> None:
    courier = CourierProfileFactory()
    today = datetime.date(2026, 6, 1)
    soon = CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today + datetime.timedelta(days=10),
    )

    results = list(courier.credentials.expiring_within(30, as_of=today))

    assert results == [soon]


def test_credential_expiring_within_excludes_credential_outside_horizon() -> None:
    courier = CourierProfileFactory()
    today = datetime.date(2026, 6, 1)
    CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today + datetime.timedelta(days=90),
    )

    results = list(courier.credentials.expiring_within(30, as_of=today))

    assert results == []


def test_credential_expiring_within_excludes_non_approved_credential() -> None:
    courier = CourierProfileFactory()
    today = datetime.date(2026, 6, 1)
    CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.PENDING_REVIEW,
        expires_on=today + datetime.timedelta(days=10),
    )

    results = list(courier.credentials.expiring_within(30, as_of=today))

    assert results == []


def test_credential_expired_queryset_finds_past_expiry() -> None:
    courier = CourierProfileFactory()
    today = datetime.date(2026, 6, 1)
    expired = CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.DRIVER_LICENSE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today - datetime.timedelta(days=1),
    )
    CourierCredentialFactory(
        courier=courier,
        credential_type=CourierCredentialType.INSURANCE,
        status=CourierCredentialStatus.APPROVED,
        expires_on=today + datetime.timedelta(days=1),
    )

    results = list(courier.credentials.expired(as_of=today))

    assert results == [expired]


def test_credential_is_expired_property() -> None:
    credential = CourierCredentialFactory(expires_on=datetime.date(2000, 1, 1))
    assert credential.is_expired is True

    credential.expires_on = datetime.date(2999, 1, 1)
    assert credential.is_expired is False

    credential.expires_on = None
    assert credential.is_expired is False
