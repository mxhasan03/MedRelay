"""A minimal in-app notification inbox: list the logged-in user's own
notifications, mark one (or all) read. Every queryset is filtered to
`request.user` — a notification is per-recipient personal data, so there is
no cross-user visibility concern to gate here the way tenant-scoping gates
organization data elsewhere in this codebase."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.notifications.models import Notification


class NotificationInboxView(LoginRequiredMixin, ListView):
    template_name = "notifications/inbox.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self) -> Any:
        return Notification.objects.for_user(self.request.user)


class NotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.mark_read()
        return redirect(reverse("notification-inbox"))
