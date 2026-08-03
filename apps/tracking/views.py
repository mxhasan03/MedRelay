"""The browser Geolocation ping endpoint for the courier PWA.

The courier's browser posts here periodically via `navigator.geolocation.
watchPosition` (see `static/js/courier.js`), routed through the offline
event queue (`static/js/offline-queue.js`) so a lost-connectivity period
does not drop pings — they queue locally in the browser and retry once back
online, each carrying a client-generated `Idempotency-Key`.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.accounts.models import User
from apps.couriers.idempotency import idempotent_call
from apps.couriers.services import can_access_courier_portal
from apps.dispatch.models import DeliveryAssignment
from apps.tracking.services import LocationPingRejectedError, record_location_ping


class LocationPingView(View):
    """`POST /tracking/assignments/<assignment_id>/ping/`

    Body: JSON `{"latitude": ..., "longitude": ..., "accuracy_meters": ...}`
    (the last is optional). Requires an `Idempotency-Key` header (or an
    `idempotency_key` field in the JSON body, for parity with the other
    courier action endpoints' no-JS fallback).

    Responses:
    - `201` — ping recorded (or replayed from an identical prior request with
      the same Idempotency-Key).
    - `409` — the assignment/delivery has reached a terminal state; no
      `CourierLocationPing` row is created. See
      `apps.tracking.services.record_location_ping`'s docstring for why this
      is a real rejection, not a silent 2xx no-op.
    - `400`/`403`/`404` — malformed body, wrong courier, or unknown
      assignment, respectively.
    """

    def post(self, request: HttpRequest, assignment_id: int) -> HttpResponse:
        if not can_access_courier_portal(request.user):
            raise PermissionDenied("Courier portal access required.")
        assert isinstance(request.user, User)  # guaranteed by can_access_courier_portal above
        courier = request.user.courier_profile

        try:
            payload: dict[str, Any] = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Request body must be valid JSON."}, status=400)

        idempotency_key = request.headers.get("Idempotency-Key") or payload.get(
            "idempotency_key", ""
        )
        if not idempotency_key:
            return JsonResponse({"error": "An Idempotency-Key is required."}, status=400)

        try:
            latitude = Decimal(str(payload["latitude"]))
            longitude = Decimal(str(payload["longitude"]))
            accuracy_raw = payload.get("accuracy_meters")
            accuracy_meters = Decimal(str(accuracy_raw)) if accuracy_raw is not None else None
        except (KeyError, InvalidOperation, TypeError):
            return JsonResponse(
                {"error": "latitude/longitude are required and must be numeric."}, status=400
            )

        assignment = get_object_or_404(
            DeliveryAssignment.objects.select_related("delivery_request", "courier"),
            pk=assignment_id,
        )
        if assignment.courier_id != courier.pk:
            raise PermissionDenied("This assignment does not belong to you.")

        def _do() -> dict[str, Any]:
            ping = record_location_ping(
                assignment,
                courier=courier,
                latitude=latitude,
                longitude=longitude,
                accuracy_meters=accuracy_meters,
            )
            return {
                "id": ping.pk,
                "assignment_id": assignment.pk,
                "recorded_at": ping.recorded_at.isoformat(),
            }

        try:
            data, status_code = idempotent_call(
                courier=courier,
                endpoint="location_ping",
                key=idempotency_key,
                fn=_do,
                status_code=201,
            )
        except LocationPingRejectedError as exc:
            return JsonResponse({"error": str(exc)}, status=409)

        return JsonResponse(data, status=status_code)
