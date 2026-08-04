"""Tests for apps.billing.services: invoice generation reuses the pricing
engine (never recomputes it), payment-status mutation is synthetic-only,
and CSV/HTML export renders the real line items."""

from __future__ import annotations

import pytest

from apps.billing.models import Invoice, PaymentStatus
from apps.billing.services import (
    generate_invoice_for_delivery,
    mark_invoice_paid,
    mark_invoice_unpaid,
    render_invoice_csv,
    render_invoice_html,
)
from apps.deliveries.pricing import quote_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory

pytestmark = pytest.mark.django_db


def test_generate_invoice_computes_a_quote_when_none_exists_yet() -> None:
    delivery_request = DeliveryRequestFactory()
    assert not hasattr(delivery_request, "quote")

    invoice = generate_invoice_for_delivery(delivery_request)

    delivery_request.refresh_from_db()
    assert invoice.total == delivery_request.quote.total_price
    assert invoice.payment_status == PaymentStatus.UNPAID


def test_generate_invoice_reuses_an_existing_quote_rather_than_recomputing() -> None:
    delivery_request = DeliveryRequestFactory()
    quote = quote_delivery_request(delivery_request)

    invoice = generate_invoice_for_delivery(delivery_request)

    assert invoice.total == quote.total_price


def test_generate_invoice_line_items_mirror_the_quote_breakdown() -> None:
    delivery_request = DeliveryRequestFactory()
    quote = quote_delivery_request(delivery_request)

    invoice = generate_invoice_for_delivery(delivery_request)

    line_total = sum(line.amount for line in invoice.lines.all())
    assert line_total == quote.total_price
    descriptions = {line.description for line in invoice.lines.all()}
    assert "Base fee" in descriptions


def test_generate_invoice_is_idempotent_and_never_overwrites_an_existing_invoice() -> None:
    delivery_request = DeliveryRequestFactory()
    first = generate_invoice_for_delivery(delivery_request)
    mark_invoice_paid(first)

    second = generate_invoice_for_delivery(delivery_request)

    assert second.pk == first.pk
    assert second.payment_status == PaymentStatus.PAID
    assert Invoice.objects.filter(delivery_request=delivery_request).count() == 1


def test_mark_invoice_paid_and_unpaid_are_synthetic_state_only() -> None:
    delivery_request = DeliveryRequestFactory()
    invoice = generate_invoice_for_delivery(delivery_request)

    mark_invoice_paid(invoice)
    assert invoice.payment_status == PaymentStatus.PAID
    assert invoice.paid_at is not None

    mark_invoice_unpaid(invoice)
    assert invoice.payment_status == PaymentStatus.UNPAID
    assert invoice.paid_at is None


def test_render_invoice_csv_contains_the_disclaimer_and_line_items() -> None:
    delivery_request = DeliveryRequestFactory()
    invoice = generate_invoice_for_delivery(delivery_request)

    csv_text = render_invoice_csv(invoice)

    assert "synthetic data" in csv_text
    assert "Base fee" in csv_text
    assert invoice.invoice_number in csv_text


def test_render_invoice_html_contains_the_disclaimer_and_line_items() -> None:
    delivery_request = DeliveryRequestFactory()
    invoice = generate_invoice_for_delivery(delivery_request)

    html_text = render_invoice_html(invoice)

    assert "synthetic data" in html_text
    assert "Base fee" in html_text
    assert "<table" in html_text
