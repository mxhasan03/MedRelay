"""URLconf for the minimal Facility CRUD UI."""

from __future__ import annotations

from django.urls import path

from apps.facilities import views

urlpatterns = [
    path("", views.FacilityListView.as_view(), name="facility-list"),
    path(
        "organizations/<int:organization_pk>/new/",
        views.FacilityCreateView.as_view(),
        name="facility-create",
    ),
    path("<int:pk>/", views.FacilityDetailView.as_view(), name="facility-detail"),
    path("<int:pk>/edit/", views.FacilityUpdateView.as_view(), name="facility-update"),
    path("<int:pk>/delete/", views.FacilityDeleteView.as_view(), name="facility-delete"),
]
