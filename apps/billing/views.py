"""Invoice list/detail/CSV export and a "generate invoice" action for a
delivered/completed delivery request. Every view is gated through
`apps.organizations.services.can_view_billing`/`can_manage_billing` — never
trusting a client-supplied organization/invoice ID without that check."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.models import User
from apps.billing.models import Invoice
from apps.billing.services import (
    generate_invoice_for_delivery,
    mark_invoice_paid,
    mark_invoice_unpaid,
    render_invoice_csv,
    render_invoice_html,
)
from apps.deliveries.models import DeliveryRequest
from apps.organizations.services import can_manage_billing, can_view_billing


class InvoiceListView(LoginRequiredMixin, ListView):
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"

    def get_queryset(self) -> Any:
        return Invoice.objects.for_user(self.request.user).select_related(
            "organization", "delivery_request"
        )


def _get_invoice_or_403(request: HttpRequest, pk: Any) -> Invoice:
    """Shared tenant-scoping check for the detail/CSV/HTML export views
    below — a plain function rather than a shared mixin class, so mypy sees
    concrete `HttpRequest`/`Any` types instead of relying on a mixin's
    assumed-present `self.request`/`self.kwargs` attributes."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("organization", "delivery_request"), pk=pk
    )
    if not can_view_billing(request.user, invoice.organization_id):
        raise PermissionDenied("You do not have billing access for this organization.")
    return invoice


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    template_name = "billing/invoice_detail.html"
    context_object_name = "invoice"

    def get_object(self, queryset: Any = None) -> Invoice:
        return _get_invoice_or_403(self.request, self.kwargs["pk"])


class InvoiceCsvExportView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: Any) -> HttpResponse:
        invoice = _get_invoice_or_403(request, pk)
        response = HttpResponse(render_invoice_csv(invoice), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.csv"'
        return response


class InvoiceHtmlExportView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: Any) -> HttpResponse:
        invoice = _get_invoice_or_403(request, pk)
        return HttpResponse(render_invoice_html(invoice), content_type="text/html")


class GenerateInvoiceView(LoginRequiredMixin, View):
    """`POST /billing/deliveries/<uuid:delivery_id>/generate/` — the
    delivery detail page's "Generate invoice" action."""

    def post(self, request: HttpRequest, delivery_id: Any) -> HttpResponse:
        delivery_request = get_object_or_404(DeliveryRequest, pk=delivery_id)
        if not can_manage_billing(request.user, delivery_request.organization_id):
            raise PermissionDenied("You do not have billing management access.")
        assert isinstance(request.user, User)  # guaranteed by LoginRequiredMixin above
        invoice = generate_invoice_for_delivery(delivery_request, created_by=request.user)
        messages.success(request, f"Invoice {invoice.invoice_number} ready.")
        return redirect(reverse("invoice-detail", kwargs={"pk": invoice.pk}))


class InvoiceMarkPaidView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        invoice = get_object_or_404(Invoice, pk=pk)
        if not can_manage_billing(request.user, invoice.organization_id):
            raise PermissionDenied("You do not have billing management access.")
        if request.POST.get("action") == "unpaid":
            mark_invoice_unpaid(invoice)
            messages.success(request, "Invoice marked unpaid.")
        else:
            mark_invoice_paid(invoice)
            messages.success(request, "Invoice marked paid.")
        return redirect(reverse("invoice-detail", kwargs={"pk": invoice.pk}))
