"""factory_boy factories for incident-app tests."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.incidents.models import Incident, IncidentCategory, IncidentSeverity, IncidentStatus


class IncidentFactory(DjangoModelFactory):
    class Meta:
        model = Incident

    delivery_request = factory.SubFactory("apps.deliveries.tests.factories.DeliveryRequestFactory")
    category = IncidentCategory.PACKAGE_DAMAGE
    severity = IncidentSeverity.MODERATE
    status = IncidentStatus.OPEN
    summary = "Test incident summary."
