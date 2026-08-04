"""URLconf for the internal audit viewer."""

from __future__ import annotations

from django.urls import path

from apps.audit import views

urlpatterns = [
    path("", views.AuditEventListView.as_view(), name="audit-event-list"),
]
