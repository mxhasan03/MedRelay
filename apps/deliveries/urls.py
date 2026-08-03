"""URLconf for the minimal delivery-request CRUD/action UI."""

from __future__ import annotations

from django.urls import path

from apps.deliveries import views

urlpatterns = [
    path("", views.DeliveryRequestListView.as_view(), name="deliveryrequest-list"),
    path(
        "organizations/<int:organization_pk>/new/",
        views.DeliveryRequestCreateView.as_view(),
        name="deliveryrequest-create",
    ),
    path("<uuid:pk>/", views.DeliveryRequestDetailView.as_view(), name="deliveryrequest-detail"),
    path(
        "<uuid:pk>/submit/",
        views.DeliveryRequestSubmitView.as_view(),
        name="deliveryrequest-submit",
    ),
    path(
        "<uuid:pk>/cancel/",
        views.DeliveryRequestCancelView.as_view(),
        name="deliveryrequest-cancel",
    ),
]
