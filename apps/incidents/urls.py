"""URLconf for the incident console."""

from __future__ import annotations

from django.urls import path

from apps.incidents import views

urlpatterns = [
    path("", views.IncidentListView.as_view(), name="incident-list"),
    path("<uuid:pk>/", views.IncidentDetailView.as_view(), name="incident-detail"),
    path("<uuid:pk>/resolve/", views.IncidentResolveView.as_view(), name="incident-resolve"),
]
