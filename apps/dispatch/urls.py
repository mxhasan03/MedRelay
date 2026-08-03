"""URLconf for the minimal dispatch board/control-tower UI."""

from __future__ import annotations

from django.urls import path

from apps.dispatch import views

urlpatterns = [
    path("", views.DispatchBoardListView.as_view(), name="dispatch-board"),
    path("<uuid:pk>/", views.DispatchBoardDetailView.as_view(), name="dispatch-board-detail"),
    path("<uuid:pk>/assign/", views.DispatchAssignView.as_view(), name="dispatch-assign"),
    path("<uuid:pk>/reassign/", views.DispatchReassignView.as_view(), name="dispatch-reassign"),
    path("<uuid:pk>/offer/", views.DispatchOfferView.as_view(), name="dispatch-offer"),
]
