"""Serves the PWA manifest and service worker at root-level URLs
(`/manifest.json`, `/sw.js`) with the correct content types.

Why not just point at `{% static %}` directly: `STATIC_URL` is `/static/`
(`config/settings/base.py`), so a service worker registered from
`/static/sw.js` would default to a `/static/`-scoped registration (a
service worker's default scope is the directory containing its own URL) —
fine for caching static shell assets, but narrower than the conventional
root-scoped PWA registration. Serving the exact same file's *content* at a
root path instead gives the worker root scope without needing to set the
`Service-Worker-Allowed` response header. The files still live in
`static/` too, and in a real deployment (once `collectstatic` has actually
run, populating `STATIC_ROOT`) whitenoise also serves them at
`/static/sw.js`/`/static/manifest.json` — a harmless duplicate route, not
relied on by anything in this codebase. In this project's test settings
`collectstatic` is never run, so only the two root-level routes below are
actually exercised by the test suite (see tests/integration/test_pwa.py).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse


def service_worker(request: HttpRequest) -> HttpResponse:
    content = (Path(settings.BASE_DIR) / "static" / "sw.js").read_text()
    return HttpResponse(content, content_type="application/javascript")


def web_manifest(request: HttpRequest) -> HttpResponse:
    content = (Path(settings.BASE_DIR) / "static" / "manifest.json").read_text()
    return HttpResponse(content, content_type="application/manifest+json")
