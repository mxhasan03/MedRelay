"""Incidents, incident actions, and the return-to-sender resolution flow.

See docs/CURRENT_STATUS.md "Phase 6" section for the full design write-up.
Highlights:

- `IncidentSeverity` splits incidents into severities that place a delivery
  on `INCIDENT_HOLD` (`SEVERE`/`CRITICAL`) and ones that do not
  (`MINOR`/`MODERATE`) — per docs/PRODUCT_REQUIREMENTS.md section 13,
  "**Severe** incidents suspend normal completion", not *every* incident.
  `HOLD_SEVERITIES` is the single source of truth both `apps.incidents.
  services.open_incident` and `apps.deliveries.state_machine.
  validate_delivered` consult, so they can never silently disagree about
  which severities are hold-worthy.
- `Incident.delivery_status_before_hold` snapshots the delivery's status at
  the moment the hold was placed, so `apps.incidents.services.
  resolve_incident`'s "resume" resolution can restore it faithfully rather
  than guessing.
- `IncidentAction` is append-only (docs/PRODUCT_REQUIREMENTS.md section 7
  "Incident console... append-only event history"), the same ORM-level
  guard convention as `apps.deliveries.models.DeliveryStatusTransition`/
  `apps.custody.models.CustodyEvent`.
- `ReturnResolution` wires the return-to-sender flow
  (`RETURNING -> RETURNED`), typically reached by resolving a severe
  incident with `IncidentResolutionType.RETURN_TO_SENDER` (see
  `apps.incidents.services.resolve_incident`/`initiate_return`/
  `complete_return`), though `incident` is nullable to allow a
  directly-initiated return without a formal incident.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class IncidentCategory(models.TextChoices):
    """docs/PRODUCT_REQUIREMENTS.md section 13."""

    LEAK_SPILL = "leak_spill", "Leak/Spill"
    BROKEN_SEAL = "broken_seal", "Broken Seal"
    PACKAGE_DAMAGE = "package_damage", "Package Damage"
    TEMPERATURE_EXCURSION = "temperature_excursion", "Temperature Excursion"
    VEHICLE_ACCIDENT = "vehicle_accident", "Vehicle Accident"
    COURIER_INJURY_EXPOSURE = "courier_injury_exposure", "Courier Injury/Exposure"
    LOST_PACKAGE = "lost_package", "Lost Package"
    INCORRECT_RECIPIENT = "incorrect_recipient", "Incorrect Recipient"
    WRONG_DESTINATION = "wrong_destination", "Wrong Destination"
    MISSED_SLA = "missed_sla", "Missed SLA"
    RECIPIENT_UNAVAILABLE = "recipient_unavailable", "Recipient Unavailable"
    SUSPECTED_TAMPERING = "suspected_tampering", "Suspected Tampering"


class IncidentSeverity(models.TextChoices):
    MINOR = "minor", "Minor"
    MODERATE = "moderate", "Moderate"
    SEVERE = "severe", "Severe"
    CRITICAL = "critical", "Critical"


# The severities that place a delivery on INCIDENT_HOLD and block DELIVERED
# until resolved — see module docstring. Single source of truth consulted by
# both apps.incidents.services.open_incident and
# apps.deliveries.state_machine.validate_delivered.
HOLD_SEVERITIES = frozenset({IncidentSeverity.SEVERE, IncidentSeverity.CRITICAL})


class IncidentStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class IncidentResolutionType(models.TextChoices):
    RESUMED = "resumed", "Resumed Normal Delivery"
    RETURN_TO_SENDER = "return_to_sender", "Return to Sender"
    CANCELLED = "cancelled", "Delivery Cancelled"
    OTHER = "other", "Other"


class Incident(models.Model):
    """One incident record for a delivery (and, when applicable, a specific
    package)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="incidents"
    )
    package = models.ForeignKey(
        "cargo.Package",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
    )
    category = models.CharField(max_length=32, choices=IncidentCategory.choices)
    severity = models.CharField(max_length=16, choices=IncidentSeverity.choices)
    status = models.CharField(
        max_length=16, choices=IncidentStatus.choices, default=IncidentStatus.OPEN
    )
    summary = models.TextField(help_text="Operational description. Never diagnosis/clinical.")
    placed_delivery_on_hold = models.BooleanField(
        default=False,
        help_text="True if opening this incident actually transitioned the delivery to "
        "INCIDENT_HOLD (only SEVERE/CRITICAL severities do this — see HOLD_SEVERITIES).",
    )
    delivery_status_before_hold = models.CharField(
        max_length=32,
        blank=True,
        help_text="Snapshot of DeliveryRequest.status at the moment this incident placed it on "
        "hold, so resolution can restore it. Blank if placed_delivery_on_hold is False.",
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents_opened",
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_type = models.CharField(
        max_length=16, choices=IncidentResolutionType.choices, blank=True
    )
    resolution_note = models.TextField(
        blank=True, help_text="Required at resolution time — see apps.incidents.services."
    )

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self) -> str:
        category = self.get_category_display()
        severity = self.get_severity_display()
        return f"{category} ({severity}) — {self.delivery_request_id}"

    @property
    def is_open(self) -> bool:
        return self.status == IncidentStatus.OPEN


class IncidentActionType(models.TextChoices):
    NOTE = "note", "Note"
    CUSTOMER_NOTIFIED = "customer_notified", "Customer Notified"
    COURIER_SUSPENSION_REVIEW = "courier_suspension_review", "Courier Suspension/Review"
    ESCALATED = "escalated", "Escalated"
    RESOLUTION = "resolution", "Resolution"


class IncidentActionQuerySet(models.QuerySet["IncidentAction"]):
    def update(self, *args: Any, **kwargs: Any) -> int:
        raise ValidationError(
            "IncidentAction rows are append-only; bulk queryset.update() is not allowed."
        )

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "IncidentAction rows are append-only; bulk queryset.delete() is not allowed."
        )


class IncidentAction(models.Model):
    """One append-only action/note row in an incident's response history
    (docs/PRODUCT_REQUIREMENTS.md section 7 "append-only event history")."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="actions")
    action_type = models.CharField(max_length=32, choices=IncidentActionType.choices)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incident_actions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = IncidentActionQuerySet.as_manager()

    class Meta:
        ordering = ["incident_id", "created_at"]

    def __str__(self) -> str:
        return f"{self.incident_id}: {self.get_action_type_display()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ValidationError(
                "IncidentAction rows are append-only; an existing row cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "IncidentAction rows are append-only; existing rows cannot be deleted."
        )


class ReturnResolutionStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    COMPLETED = "completed", "Completed"


class ReturnResolution(models.Model):
    """The return-to-sender flow: `RETURNING -> RETURNED`
    (docs/PRODUCT_REQUIREMENTS.md section 7 "return-to-sender... resolution";
    docs/ARCHITECTURE_AND_DATA_MODEL.md "Custody, proof, and incidents").

    `incident` is nullable so a return can, in principle, be initiated
    without a formal incident, but the primary flow this phase builds and
    tests is incident-driven: `apps.incidents.services.resolve_incident`
    with `resolution_type=RETURN_TO_SENDER` calls `initiate_return`
    internally.
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="return_resolution"
    )
    incident = models.ForeignKey(
        Incident,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_resolutions",
    )
    return_facility = models.ForeignKey(
        "facilities.Facility",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_resolutions",
        help_text="Where the package is being returned to. Defaults to the original pickup "
        "facility if left blank.",
    )
    status = models.CharField(
        max_length=16,
        choices=ReturnResolutionStatus.choices,
        default=ReturnResolutionStatus.INITIATED,
    )
    reason = models.TextField()
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_resolutions_initiated",
    )
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_resolutions_completed",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Return resolution for {self.delivery_request_id} ({self.status})"
