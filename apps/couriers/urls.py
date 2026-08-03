"""URLconf for the courier-facing PWA (job offers, active delivery, pickup/
transit status advancement, package scan)."""

from __future__ import annotations

from django.urls import path

from apps.couriers import views

urlpatterns = [
    path("", views.CourierHomeView.as_view(), name="courier-home"),
    path("offers/", views.JobOfferListView.as_view(), name="courier-job-offer-list"),
    path(
        "offers/<int:pk>/accept/",
        views.JobOfferAcceptView.as_view(),
        name="courier-job-offer-accept",
    ),
    path(
        "offers/<int:pk>/decline/",
        views.JobOfferDeclineView.as_view(),
        name="courier-job-offer-decline",
    ),
    path(
        "deliveries/<uuid:pk>/",
        views.ActiveDeliveryView.as_view(),
        name="courier-active-delivery",
    ),
    path(
        "deliveries/<uuid:pk>/advance/",
        views.DeliveryStatusAdvanceView.as_view(),
        name="courier-delivery-advance",
    ),
    path(
        "deliveries/<uuid:pk>/scan/",
        views.PackageScanView.as_view(),
        name="courier-package-scan",
    ),
]
