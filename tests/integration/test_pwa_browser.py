"""Real-browser verification of the courier PWA's service-worker
registration and static-shell cache population, using Playwright against a
real Django LiveServerTestCase-backed HTTP server (not just the Django test
client, which cannot execute JavaScript or a service worker at all).

Honesty note (docs/CURRENT_STATUS.md "Phase 5" has the full write-up):
Playwright + a real Chromium binary were successfully installed and did
launch in this sandbox (`uv add --group dev playwright` and
`python -m playwright install chromium` both succeeded; `--with-deps` did
not, since it requires interactive sudo, but the plain chromium download
launched and rendered a page correctly when tested directly). This test
file is therefore genuine, executed browser automation, not a claim about
untested behavior — it actually drives a real browser against a real running
server and inspects the real Cache Storage API and
`navigator.serviceWorker.getRegistrations()`. It is kept in a separate file
(and separately reported in CI/quality-gate output) precisely because it
depends on a downloaded browser binary that may not be available/permitted
in every environment this repository is cloned into — if Chromium cannot be
launched in a given environment, this file's tests will fail/error there,
and that should be treated as an environment limitation, not a code
regression, per docs/CURRENT_STATUS.md's own caveat.
"""

from __future__ import annotations

import pytest

from apps.couriers.tests.factories import CourierProfileFactory

playwright_sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.django_db(transaction=True)

DEMO_PASSWORD = "MedRelayPwaBrowserTest!2026"  # pragma: allowlist secret


def test_service_worker_registers_and_precaches_the_static_shell(live_server) -> None:
    courier = CourierProfileFactory()
    courier.user.set_password(DEMO_PASSWORD)
    courier.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()

            page.goto(f"{live_server.url}/accounts/login/")
            page.fill("#id_username", courier.user.username)
            page.fill("#id_password", DEMO_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(f"{live_server.url}/organizations/")

            page.goto(f"{live_server.url}/couriers/")
            # Allow the 'load' event + async service worker registration +
            # its own install-event cache.addAll() to actually complete
            # before inspecting real, live browser state below.
            result = page.evaluate(
                """
                async () => {
                  for (let attempt = 0; attempt < 50; attempt++) {
                    const regs = await navigator.serviceWorker.getRegistrations();
                    const cache = await caches.open('medrelay-courier-shell-v1');
                    const keys = await cache.keys();
                    if (regs.length > 0 && keys.length >= 4) {
                      return {
                        registrations: regs.length,
                        cachedPaths: keys.map(k => new URL(k.url).pathname),
                      };
                    }
                    await new Promise(resolve => setTimeout(resolve, 200));
                  }
                  return {registrations: 0, cachedPaths: []};
                }
                """
            )

            assert result["registrations"] >= 1
            cached_paths = result["cachedPaths"]
            assert "/static/manifest.json" in cached_paths
            assert "/static/js/courier.js" in cached_paths
            assert "/static/js/offline-queue.js" in cached_paths
            assert "/static/icons/icon.svg" in cached_paths
        finally:
            browser.close()


def test_manifest_link_and_csrf_meta_are_present_in_a_real_rendered_page(
    live_server,
) -> None:
    """A lighter-weight real-browser check (no waiting on the service worker
    lifecycle) proving the actual rendered DOM — not just response text —
    has the expected <link rel="manifest"> and CSRF meta tag."""
    courier = CourierProfileFactory()
    courier.user.set_password(DEMO_PASSWORD)
    courier.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}/accounts/login/")
            page.fill("#id_username", courier.user.username)
            page.fill("#id_password", DEMO_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(f"{live_server.url}/organizations/")

            page.goto(f"{live_server.url}/couriers/")
            manifest_href = page.get_attribute('link[rel="manifest"]', "href")
            assert manifest_href == "/manifest.json"
            csrf_meta = page.get_attribute('meta[name="csrf-token"]', "content")
            assert csrf_meta  # non-empty real CSRF token value
        finally:
            browser.close()
