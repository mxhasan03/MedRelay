"""Liveness and readiness views.

`healthz` has no dependencies at all — it only proves the process is up and
serving requests. `readyz` additionally checks that the configured database
and cache are reachable, and returns 503 if either check fails.
"""

import logging
from typing import Any

from django.core.cache import cache
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request: Any) -> JsonResponse:
    """Liveness probe: always 200 if the process can handle a request."""
    return JsonResponse({"status": "ok"})


def readyz(request: Any) -> JsonResponse:
    """Readiness probe: 200 only if the database and cache are reachable."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        connection = connections["default"]
        connection.ensure_connection()
        checks["database"] = "ok"
    except OperationalError:
        logger.exception("Readiness check: database unreachable")
        checks["database"] = "unreachable"
        healthy = False

    try:
        marker = "__readyz_probe__"
        cache.set(marker, "1", timeout=5)
        if cache.get(marker) != "1":
            raise RuntimeError("cache readback mismatch")
        checks["cache"] = "ok"
    except Exception:
        logger.exception("Readiness check: cache unreachable")
        checks["cache"] = "unreachable"
        healthy = False

    status_code = 200 if healthy else 503
    payload = {"status": "ok" if healthy else "unavailable", "checks": checks}
    return JsonResponse(payload, status=status_code)
