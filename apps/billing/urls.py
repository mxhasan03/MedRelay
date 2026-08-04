"""URLconf for invoices."""

from __future__ import annotations

from django.urls import path

from apps.billing import views

urlpatterns = [
    path("invoices/", views.InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<uuid:pk>/", views.InvoiceDetailView.as_view(), name="invoice-detail"),
    path(
        "invoices/<uuid:pk>/export.csv",
        views.InvoiceCsvExportView.as_view(),
        name="invoice-export-csv",
    ),
    path(
        "invoices/<uuid:pk>/export.html",
        views.InvoiceHtmlExportView.as_view(),
        name="invoice-export-html",
    ),
    path(
        "invoices/<uuid:pk>/mark-paid/",
        views.InvoiceMarkPaidView.as_view(),
        name="invoice-mark-paid",
    ),
    path(
        "deliveries/<uuid:delivery_id>/generate-invoice/",
        views.GenerateInvoiceView.as_view(),
        name="invoice-generate",
    ),
]
