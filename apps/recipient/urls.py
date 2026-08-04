"""URLconf for the anonymous recipient tracking page."""

from __future__ import annotations

from django.urls import path

from apps.recipient import views

urlpatterns = [
    path("<str:token>/", views.RecipientTrackingView.as_view(), name="recipient-tracking"),
]
