"""URLconf for the minimal Organization CRUD UI."""

from __future__ import annotations

from django.urls import path

from apps.organizations import views

urlpatterns = [
    path("", views.OrganizationListView.as_view(), name="organization-list"),
    path("new/", views.OrganizationCreateView.as_view(), name="organization-create"),
    path("<int:pk>/", views.OrganizationDetailView.as_view(), name="organization-detail"),
    path("<int:pk>/edit/", views.OrganizationUpdateView.as_view(), name="organization-update"),
]
