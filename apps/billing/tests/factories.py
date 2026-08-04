"""factory_boy factories for billing-app tests."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.billing.models import Invoice
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.organizations.tests.factories import OrganizationFactory


class InvoiceFactory(DjangoModelFactory):
    class Meta:
        model = Invoice

    organization = factory.SubFactory(OrganizationFactory)
    delivery_request = factory.SubFactory(DeliveryRequestFactory)
    invoice_number = factory.Sequence(lambda n: f"INV-TEST-{n:06d}")
    subtotal = 10
    total = 10
