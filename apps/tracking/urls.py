"""URLconf for the courier location-ping endpoint."""

from __future__ import annotations

from django.urls import path

from apps.tracking import views

urlpatterns = [
    path(
        "assignments/<int:assignment_id>/ping/",
        views.LocationPingView.as_view(),
        name="location-ping",
    ),
]
