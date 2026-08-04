"""HTTP-level tests for the incident console (list/detail/resolve)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import InternalRole
from apps.accounts.tests.factories import InternalRoleAssignmentFactory, UserFactory
from apps.deliveries.models import DeliveryStatus
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.incidents.models import IncidentCategory, IncidentSeverity, IncidentStatus
from apps.incidents.services import open_incident

pytestmark = pytest.mark.django_db


def _dispatcher_user():
    user = UserFactory()
    InternalRoleAssignmentFactory(user=user, role=InternalRole.DISPATCHER)
    return user


def test_incident_list_requires_login(client: Client) -> None:
    response = client.get(reverse("incident-list"))
    assert response.status_code == 302


def test_incident_list_forbidden_for_non_dispatch_user(client: Client) -> None:
    client.force_login(UserFactory())
    response = client.get(reverse("incident-list"))
    assert response.status_code == 403


def test_incident_list_shows_only_open_incidents(client: Client) -> None:
    delivery_request = DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT)
    open_incident_row = open_incident(
        delivery_request,
        category=IncidentCategory.PACKAGE_DAMAGE,
        severity=IncidentSeverity.MINOR,
        summary="Minor scuff.",
        actor=None,
    )
    resolved = open_incident(
        DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT),
        category=IncidentCategory.MISSED_SLA,
        severity=IncidentSeverity.MINOR,
        summary="Late.",
        actor=None,
    )
    resolved.status = IncidentStatus.RESOLVED
    resolved.save(update_fields=["status"])

    client.force_login(_dispatcher_user())
    response = client.get(reverse("incident-list"))

    assert response.status_code == 200
    incidents = list(response.context["incidents"])
    assert open_incident_row in incidents
    assert resolved not in incidents


def test_incident_resolve_view_requires_a_note(client: Client) -> None:
    delivery_request = DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT)
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.BROKEN_SEAL,
        severity=IncidentSeverity.SEVERE,
        summary="Seal broken.",
        actor=None,
    )
    client.force_login(_dispatcher_user())

    response = client.post(
        reverse("incident-resolve", kwargs={"pk": incident.pk}),
        data={"resolution_type": "resumed", "resolution_note": ""},
    )

    assert response.status_code == 302
    incident.refresh_from_db()
    assert incident.status == IncidentStatus.OPEN


def test_incident_resolve_view_success_resumes_delivery(client: Client) -> None:
    delivery_request = DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT)
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.BROKEN_SEAL,
        severity=IncidentSeverity.SEVERE,
        summary="Seal broken.",
        actor=None,
    )
    client.force_login(_dispatcher_user())

    response = client.post(
        reverse("incident-resolve", kwargs={"pk": incident.pk}),
        data={"resolution_type": "resumed", "resolution_note": "Confirmed contents intact."},
    )

    assert response.status_code == 302
    incident.refresh_from_db()
    assert incident.status == IncidentStatus.RESOLVED
    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.IN_TRANSIT


def test_incident_detail_view_shows_action_history(client: Client) -> None:
    delivery_request = DeliveryRequestFactory(status=DeliveryStatus.IN_TRANSIT)
    incident = open_incident(
        delivery_request,
        category=IncidentCategory.LOST_PACKAGE,
        severity=IncidentSeverity.MODERATE,
        summary="Package missing.",
        actor=None,
    )
    client.force_login(_dispatcher_user())

    response = client.get(reverse("incident-detail", kwargs={"pk": incident.pk}))

    assert response.status_code == 200
    assert response.context["incident"].pk == incident.pk
