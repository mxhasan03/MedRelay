"""Invoice generation, payment-status mutation, and CSV/HTML export —
reuses `apps.deliveries.pricing`'s quote engine rather than recomputing
pricing logic (per CLAUDE.md's "cross-app calls go through explicit service
functions" rule and the task's own "don't recompute pricing logic twice").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from apps.billing.models import Invoice, InvoiceLine, PaymentStatus
from apps.reporting.rendering import rows_to_csv, rows_to_html

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.deliveries.models import DeliveryRequest

# Maps each non-zero `QuoteBreakdown` field to its invoice-line description.
# Order here is the order lines are written in.
_LINE_LABELS: list[tuple[str, str]] = [
    ("base_fee", "Base fee"),
    ("distance_time_fee", "Distance/time estimate"),
    ("service_level_surcharge", "Service-level surcharge"),
    ("cargo_equipment_surcharge", "Cargo/equipment surcharge"),
    ("toll_estimate", "Toll estimate"),
    ("wait_time_fee", "Wait-time placeholder"),
    ("after_hours_surcharge", "After-hours surcharge"),
    ("return_trip_fee", "Return-trip fee"),
]


class InvoiceGenerationError(Exception):
    """Raised when an invoice cannot be generated for a delivery request."""


def _next_invoice_number() -> str:
    """A simple sequential `INV-000001`-style label.

    Honest, demo-scale limitation: this counts existing rows rather than
    using a dedicated database sequence, so it is not safe under real
    concurrent invoice generation (two simultaneous calls could compute the
    same next number and collide on `Invoice.invoice_number`'s uniqueness
    constraint, raising `IntegrityError` rather than silently duplicating a
    number). Invoice generation is not a high-concurrency code path
    anywhere in this prototype (there is no bulk/batch invoicing job), so
    this is an acceptable, documented simplification rather than the
    `select_for_update()`-guarded counter `apps.dispatch.services` uses for
    its genuinely concurrent assignment path.
    """
    count = Invoice.objects.count()
    return f"INV-{count + 1:06d}"


@transaction.atomic
def generate_invoice_for_delivery(
    delivery_request: DeliveryRequest, *, created_by: User | None = None
) -> Invoice:
    """Generate (or return the existing) invoice for `delivery_request`.

    Reuses `apps.deliveries.pricing.quote_delivery_request` to obtain the
    quote breakdown (computing/persisting one first if none exists yet) —
    this never recomputes pricing logic itself. If an invoice already
    exists for this delivery, it is returned **unchanged** — an invoice is
    a financial record, not a recomputed-on-demand estimate like `Quote`;
    silently overwriting a possibly-already-issued/paid invoice's amounts
    would be a real correctness bug, not a convenience. Call
    `apps.billing.services.void_invoice`/`mark_invoice_paid` to change an
    existing invoice's state instead.
    """
    existing = Invoice.objects.filter(delivery_request=delivery_request).first()
    if existing is not None:
        return existing

    quote = getattr(delivery_request, "quote", None)
    if quote is None:
        from apps.deliveries.pricing import quote_delivery_request

        quote = quote_delivery_request(delivery_request)

    invoice = Invoice.objects.create(
        organization=delivery_request.organization,
        delivery_request=delivery_request,
        invoice_number=_next_invoice_number(),
        payment_status=PaymentStatus.UNPAID,
        subtotal=quote.total_price,
        total=quote.total_price,
        created_by=created_by,
    )

    sequence = 1
    for field_name, description in _LINE_LABELS:
        amount = getattr(quote, field_name)
        if amount:
            InvoiceLine.objects.create(
                invoice=invoice, sequence=sequence, description=description, amount=amount
            )
            sequence += 1

    return invoice


def mark_invoice_paid(invoice: Invoice) -> Invoice:
    """Synthetic payment-status mock only — no real payment processor is
    ever contacted (see module/model docstrings)."""
    invoice.payment_status = PaymentStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["payment_status", "paid_at"])
    return invoice


def mark_invoice_unpaid(invoice: Invoice) -> Invoice:
    invoice.payment_status = PaymentStatus.UNPAID
    invoice.paid_at = None
    invoice.save(update_fields=["payment_status", "paid_at"])
    return invoice


_INVOICE_FIELDS = ["sequence", "description", "amount"]


def invoice_line_rows(invoice: Invoice) -> list[dict[str, Any]]:
    return [
        {"sequence": line.sequence, "description": line.description, "amount": str(line.amount)}
        for line in invoice.lines.all()
    ]


def render_invoice_csv(invoice: Invoice) -> str:
    return rows_to_csv(
        _INVOICE_FIELDS, invoice_line_rows(invoice), title=f"Invoice {invoice.invoice_number}"
    )


def render_invoice_html(invoice: Invoice) -> str:
    return rows_to_html(
        _INVOICE_FIELDS, invoice_line_rows(invoice), title=f"Invoice {invoice.invoice_number}"
    )


__all__ = [
    "InvoiceGenerationError",
    "generate_invoice_for_delivery",
    "invoice_line_rows",
    "mark_invoice_paid",
    "mark_invoice_unpaid",
    "render_invoice_csv",
    "render_invoice_html",
]
