"""URLconf for the in-app notification inbox."""

from __future__ import annotations

from django.urls import path

from apps.notifications import views

urlpatterns = [
    path("", views.NotificationInboxView.as_view(), name="notification-inbox"),
    path(
        "<uuid:pk>/read/", views.NotificationMarkReadView.as_view(), name="notification-mark-read"
    ),
]
