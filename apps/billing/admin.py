"""Django admin registrations for invoices."""

from __future__ import annotations

from django.contrib import admin

from apps.billing.models import Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "invoice_number",
        "organization",
        "delivery_request",
        "payment_status",
        "total",
        "issued_at",
    ]
    list_filter = ["payment_status"]
    search_fields = ["invoice_number", "organization__name"]
    autocomplete_fields = ["organization", "delivery_request", "created_by"]
    readonly_fields = ["invoice_number", "issued_at"]
    inlines = [InvoiceLineInline]
