"""Tests for the `seed_full_demo` management command (Phase 9 comprehensive demo seed)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.billing.models import Invoice
from apps.couriers.models import CourierProfile, CourierStatus
from apps.deliveries.models import DeliveryRequest, DeliveryStatus
from apps.incidents.models import Incident, IncidentCategory, IncidentSeverity, IncidentStatus

pytestmark = pytest.mark.django_db


def _run() -> None:
    call_command("seed_full_demo", stdout=StringIO())


def test_seed_full_demo_creates_five_delivery_scenarios_in_expected_states() -> None:
    _run()

    assert DeliveryRequest.objects.count() == 5
    statuses = set(DeliveryRequest.objects.values_list("status", flat=True))
    assert statuses == {
        DeliveryStatus.READY_FOR_DISPATCH,
        DeliveryStatus.ASSIGNED,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.INCIDENT_HOLD,
        DeliveryStatus.RETURNED,
    }


def test_seed_full_demo_creates_a_real_temperature_excursion_incident() -> None:
    _run()

    excursion_incidents = Incident.objects.filter(category=IncidentCategory.TEMPERATURE_EXCURSION)
    assert excursion_incidents.count() == 1
    incident = excursion_incidents.get()
    assert incident.severity == IncidentSeverity.SEVERE
    assert incident.status == IncidentStatus.OPEN
    assert incident.placed_delivery_on_hold is True
    assert incident.delivery_request.status == DeliveryStatus.INCIDENT_HOLD


def test_seed_full_demo_creates_a_resolved_recipient_unavailable_return() -> None:
    _run()

    return_incidents = Incident.objects.filter(category=IncidentCategory.RECIPIENT_UNAVAILABLE)
    assert return_incidents.count() == 1
    incident = return_incidents.get()
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.delivery_request.status == DeliveryStatus.RETURNED
    assert hasattr(incident.delivery_request, "return_resolution")


def test_seed_full_demo_generates_at_least_one_invoice() -> None:
    _run()

    assert Invoice.objects.count() == 1
    invoice = Invoice.objects.get()
    assert invoice.delivery_request.status == DeliveryStatus.DELIVERED
    assert invoice.total > 0


def test_seed_full_demo_creates_couriers_with_varied_states() -> None:
    _run()

    profiles = {p.user.username: p for p in CourierProfile.objects.select_related("user")}
    assert profiles["demo_courier_ana"].status == CourierStatus.APPROVED
    assert profiles["demo_courier_dee"].status == CourierStatus.APPLICANT
    assert profiles["demo_courier_eli"].status == CourierStatus.SUSPENDED

    cara = profiles["demo_courier_cara"]
    soon_expiring = cara.credentials.filter(expires_on__isnull=False).order_by("expires_on").first()
    assert soon_expiring is not None
    # Expiring soon (within flag_expiring_credentials' default 30-day window) but not yet expired.
    from django.utils import timezone

    assert soon_expiring.expires_on >= timezone.localdate()
    assert (soon_expiring.expires_on - timezone.localdate()).days <= 30


def test_seed_full_demo_is_safe_to_run_twice() -> None:
    _run()
    _run()

    assert DeliveryRequest.objects.count() == 5
    assert Invoice.objects.count() == 1
    assert CourierProfile.objects.count() == 5
