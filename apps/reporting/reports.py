"""Report-row builders — every function here takes a single
`organization_id` (already permission-checked by the caller via
`apps.organizations.services.can_export_reports`) and returns a list of
plain `dict` rows built only from operational identifiers/status values,
never raw contact/PHI-adjacent fields, matching the same data-minimization
convention `apps.notifications.payload` enforces for notification logs.

Each report reuses an existing app's models/state directly (read-only) —
per the task's own instruction, this module never recomputes chain-of-
custody hashing, pricing, or eligibility logic; it only reads what those
apps already computed and persisted.
"""

from __future__ import annotations

from typing import Any


class ReportType:
    DELIVERY_SUMMARY = "delivery_summary"
    CUSTODY_TIMELINE = "custody_timeline"
    PROOF_OF_DELIVERY = "proof_of_delivery"
    INCIDENT_SUMMARY = "incident_summary"
    ON_TIME_PERFORMANCE = "on_time_performance"
    INVOICE_SUMMARY = "invoice_summary"


DELIVERY_SUMMARY_FIELDS = [
    "delivery_id",
    "status",
    "service_level",
    "created_at",
    "required_delivery_by",
    "estimated_price",
]

CUSTODY_TIMELINE_FIELDS = ["delivery_id", "sequence", "event_type", "actor_type", "occurred_at"]

PROOF_OF_DELIVERY_FIELDS = ["delivery_id", "proof_type", "has_signature", "captured_at"]

INCIDENT_SUMMARY_FIELDS = [
    "incident_id",
    "delivery_id",
    "category",
    "severity",
    "status",
    "opened_at",
    "resolved_at",
]

ON_TIME_PERFORMANCE_FIELDS = [
    "delivery_id",
    "service_level",
    "required_delivery_by",
    "completed_at",
    "on_time",
]

INVOICE_SUMMARY_FIELDS = ["invoice_number", "delivery_id", "payment_status", "total", "issued_at"]


def delivery_summary_rows(organization_id: Any) -> list[dict[str, Any]]:
    from apps.deliveries.models import DeliveryRequest

    qs = DeliveryRequest.objects.filter(organization_id=organization_id).order_by("-created_at")
    return [
        {
            "delivery_id": str(d.pk),
            "status": d.get_status_display(),
            "service_level": d.get_service_level_display(),
            "created_at": d.created_at.isoformat(),
            "required_delivery_by": d.required_delivery_by.isoformat(),
            "estimated_price": str(d.estimated_price) if d.estimated_price is not None else "",
        }
        for d in qs
    ]


def custody_timeline_rows(organization_id: Any) -> list[dict[str, Any]]:
    from apps.custody.models import CustodyEvent

    qs = (
        CustodyEvent.objects.filter(delivery_request__organization_id=organization_id)
        .select_related("delivery_request")
        .order_by("delivery_request_id", "sequence")
    )
    return [
        {
            "delivery_id": str(e.delivery_request_id),
            "sequence": e.sequence,
            "event_type": e.get_event_type_display(),
            "actor_type": e.get_actor_type_display(),
            "occurred_at": e.occurred_at.isoformat(),
        }
        for e in qs
    ]


def proof_of_delivery_rows(organization_id: Any) -> list[dict[str, Any]]:
    from apps.custody.models import ProofOfDelivery, ProofOfPickup

    rows: list[dict[str, Any]] = []
    pickups = ProofOfPickup.objects.filter(
        delivery_request__organization_id=organization_id
    ).select_related("delivery_request")
    for pickup in pickups:
        rows.append(
            {
                "delivery_id": str(pickup.delivery_request_id),
                "proof_type": "pickup",
                "has_signature": pickup.has_signature,
                "captured_at": pickup.captured_at.isoformat(),
            }
        )
    deliveries = ProofOfDelivery.objects.filter(
        delivery_request__organization_id=organization_id
    ).select_related("delivery_request")
    for delivery in deliveries:
        rows.append(
            {
                "delivery_id": str(delivery.delivery_request_id),
                "proof_type": "delivery",
                "has_signature": delivery.has_signature,
                "captured_at": delivery.captured_at.isoformat(),
            }
        )
    return rows


def incident_summary_rows(organization_id: Any) -> list[dict[str, Any]]:
    from apps.incidents.models import Incident

    qs = Incident.objects.filter(delivery_request__organization_id=organization_id).select_related(
        "delivery_request"
    )
    return [
        {
            "incident_id": str(i.pk),
            "delivery_id": str(i.delivery_request_id),
            "category": i.get_category_display(),
            "severity": i.get_severity_display(),
            "status": i.get_status_display(),
            "opened_at": i.opened_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else "",
        }
        for i in qs
    ]


def on_time_performance_rows(organization_id: Any) -> list[dict[str, Any]]:
    """One row per **delivered** delivery request, comparing the
    `delivered` `DeliveryStatusTransition`'s timestamp (the authoritative,
    append-only record of when the delivery actually reached `DELIVERED` —
    reused rather than re-derived from `CustodyEvent` payload) against
    `required_delivery_by`."""
    from apps.deliveries.models import DeliveryRequest, DeliveryStatus, DeliveryStatusTransition

    qs = DeliveryRequest.objects.filter(
        organization_id=organization_id, status=DeliveryStatus.DELIVERED
    )
    rows: list[dict[str, Any]] = []
    for d in qs:
        transition = (
            DeliveryStatusTransition.objects.filter(
                delivery_request=d, to_status=DeliveryStatus.DELIVERED
            )
            .order_by("-occurred_at")
            .first()
        )
        completed_at = transition.occurred_at if transition else None
        on_time = bool(completed_at and completed_at <= d.required_delivery_by)
        rows.append(
            {
                "delivery_id": str(d.pk),
                "service_level": d.get_service_level_display(),
                "required_delivery_by": d.required_delivery_by.isoformat(),
                "completed_at": completed_at.isoformat() if completed_at else "",
                "on_time": on_time,
            }
        )
    return rows


def invoice_summary_rows(organization_id: Any) -> list[dict[str, Any]]:
    from apps.billing.models import Invoice

    qs = Invoice.objects.filter(organization_id=organization_id).order_by("-issued_at")
    return [
        {
            "invoice_number": inv.invoice_number,
            "delivery_id": str(inv.delivery_request_id),
            "payment_status": inv.get_payment_status_display(),
            "total": str(inv.total),
            "issued_at": inv.issued_at.isoformat(),
        }
        for inv in qs
    ]


# Registry consulted by apps.reporting.views/services — (fields, row_builder, title).
REPORT_REGISTRY: dict[str, tuple[list[str], Any, str]] = {
    ReportType.DELIVERY_SUMMARY: (
        DELIVERY_SUMMARY_FIELDS,
        delivery_summary_rows,
        "Delivery Summary",
    ),
    ReportType.CUSTODY_TIMELINE: (
        CUSTODY_TIMELINE_FIELDS,
        custody_timeline_rows,
        "Custody Timeline",
    ),
    ReportType.PROOF_OF_DELIVERY: (
        PROOF_OF_DELIVERY_FIELDS,
        proof_of_delivery_rows,
        "Pickup/Delivery Proof",
    ),
    ReportType.INCIDENT_SUMMARY: (
        INCIDENT_SUMMARY_FIELDS,
        incident_summary_rows,
        "Incident Summary",
    ),
    ReportType.ON_TIME_PERFORMANCE: (
        ON_TIME_PERFORMANCE_FIELDS,
        on_time_performance_rows,
        "On-Time Performance",
    ),
    ReportType.INVOICE_SUMMARY: (INVOICE_SUMMARY_FIELDS, invoice_summary_rows, "Invoice Summary"),
}

REPORT_TYPE_CHOICES = [(key, title) for key, (_, _, title) in REPORT_REGISTRY.items()]


__all__ = [
    "REPORT_REGISTRY",
    "REPORT_TYPE_CHOICES",
    "ReportType",
    "custody_timeline_rows",
    "delivery_summary_rows",
    "incident_summary_rows",
    "invoice_summary_rows",
    "on_time_performance_rows",
    "proof_of_delivery_rows",
]
