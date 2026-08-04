"""Synthetic invoices generated from a delivery request's existing quote.

Per docs/PRODUCT_REQUIREMENTS.md section 14 ("internal invoice records...
payment-status mock") and CLAUDE.md's do-not-build list ("billing stays a
synthetic quote/invoice prototype... any real payment processing... is
explicitly out of scope"): `PaymentStatus` is a plain, manually-set field —
there is no `PaymentProvider` adapter with a real implementation, and there
never should be one in this repository (every paid payment processor is
prohibited per docs/TECH_STACK_AND_ZERO_COST_POLICY.md).

`Invoice`/`InvoiceLine` are generated from `apps.deliveries.models.Quote` —
see `apps.billing.services.generate_invoice_for_delivery`, which reuses
`apps.deliveries.pricing.quote_delivery_request` rather than recomputing
pricing logic. One invoice per delivery request (`OneToOneField`), matching
`Quote`'s own "one row per delivery request" precedent — but, unlike `Quote`
(recomputed/overwritten on every call), an invoice is a financial record:
once one exists for a delivery, `generate_invoice_for_delivery` returns it
unchanged rather than silently overwriting a possibly-already-issued/paid
invoice. See that function's docstring.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models


class PaymentStatus(models.TextChoices):
    """A synthetic mock field only — see module docstring. No real payment
    processor is connected; nothing here ever calls out to one."""

    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    VOID = "void", "Void"


class InvoiceQuerySet(models.QuerySet["Invoice"]):
    def for_user(self, user: Any) -> Any:
        from apps.organizations.services import scope_queryset_to_user_orgs

        return scope_queryset_to_user_orgs(self, user, org_field="organization_id")


class Invoice(models.Model):
    """One synthetic invoice for one delivery request.

    `invoice_number` is a display-friendly sequential label
    (`INV-000001`-style, assigned at creation time via a simple `Max(id)+1`
    style counter — see `apps.billing.services._next_invoice_number` for the
    honest, demo-scale limitation of this approach under real concurrency,
    which does not matter here since invoice generation is not a
    high-concurrency code path anywhere in this prototype).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="invoices"
    )
    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.PROTECT, related_name="invoice"
    )
    invoice_number = models.CharField(max_length=32, unique=True)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    subtotal = models.DecimalField(max_digits=8, decimal_places=2)
    total = models.DecimalField(max_digits=8, decimal_places=2)
    issued_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_created",
    )

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"{self.invoice_number} ({self.get_payment_status_display()})"


class InvoiceLine(models.Model):
    """One line item on an invoice — one row per non-zero component of the
    delivery's `apps.deliveries.pricing.QuoteBreakdown`
    (`apps.billing.services.generate_invoice_for_delivery` is the only
    intended writer)."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["invoice_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "sequence"], name="unique_invoice_line_sequence"
            )
        ]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_number}: {self.description} = {self.amount}"


__all__ = ["Invoice", "InvoiceLine", "PaymentStatus"]
