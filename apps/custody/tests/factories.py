"""factory_boy factories for custody-app tests. Kept minimal — most tests go
through `apps.custody.services.record_event`/`append_correction` directly
rather than a bare `CustodyEventFactory`, since `CustodyEvent` should never
be constructed outside that service layer (see its model docstring)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.custody.models import ProofOfDelivery, ProofOfPickup, RecipientVerification
from apps.deliveries.models import RecipientVerificationMethod


class ProofOfPickupFactory(DjangoModelFactory):
    class Meta:
        model = ProofOfPickup

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    sender_name = "Front Desk (Test)"
    typed_signature_name = "F. Desk"


class RecipientVerificationFactory(DjangoModelFactory):
    class Meta:
        model = RecipientVerification

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    method = RecipientVerificationMethod.PIN


class ProofOfDeliveryFactory(DjangoModelFactory):
    class Meta:
        model = ProofOfDelivery

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    typed_signature_name = "R. Recipient"
