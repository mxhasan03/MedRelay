"""Real automated accessibility scans (axe-core, via Playwright) against a
real running Django server — not the Django test client, which cannot
execute JavaScript or evaluate live DOM/ARIA state at all.

## Where axe-core comes from (dev-only, never shipped to the app)

`axe-core` is installed as a plain npm devDependency in
`tests/accessibility/package.json` (`npm install`, run once in that
directory — Node/npm were confirmed available in this sandbox: Node v18.19.1,
npm 9.2.0, and `npm install axe-core` completed in well under a minute with
zero vulnerabilities reported). The installed file
(`tests/accessibility/node_modules/axe-core/axe.min.js`) is injected
directly into each page under test via Playwright's `page.add_script_tag(path=...)`
— a **local file path**, never a CDN URL fetched by the running Django
application or referenced from any template/static asset. `node_modules/`
is gitignored (see `.gitignore`); `package.json`/`package-lock.json` are
committed so `npm install` reproduces the exact same axe-core version.
Nothing under `tests/` is collected into `STATICFILES_DIRS` or served by the
app — this is unambiguously test-only tooling, on the same footing as
Playwright itself (already an approved dev dependency since Phase 5).

## Reused setup

Same real-browser pattern as `tests/integration/test_pwa_browser.py` (Phase
5's working Playwright setup): `live_server` (a real HTTP server backed by
the real Django app) + `playwright.sync_api`. Login is a real form
submission through the real `/accounts/login/` view, not a shortcut.

## Scope and honesty about results

Scans run against: the login page, the organization list (customer portal),
the facility list, the dispatch board (internal ops console), a courier PWA
view (job offer list), and the anonymous recipient tracking page — the set
named in `docs/IMPLEMENTATION_ROADMAP.md` Phase 8's acceptance criteria.
Each test asserts there are zero `critical`/`serious`-impact violations
(axe-core's own severity scale) and prints/records any `moderate`/`minor`
findings for the honest "documented, not silently ignored" write-up in
`docs/CURRENT_STATUS.md` "Phase 8".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.accounts.tests.factories import InternalRoleAssignmentFactory, UserFactory
from apps.couriers.tests.factories import CourierProfileFactory
from apps.deliveries.tests.factories import DeliveryRequestFactory
from apps.organizations.models import CustomerRole
from apps.organizations.tests.factories import OrganizationMembershipFactory
from apps.recipient.tokens import generate_recipient_tracking_token

playwright_sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.django_db(transaction=True)

DEMO_PASSWORD = "MedRelayAxeScanTest!2026"  # pragma: allowlist secret

AXE_SCRIPT_PATH = Path(__file__).resolve().parent / "node_modules" / "axe-core" / "axe.min.js"

# axe-core impact levels, most to least severe. Only "critical"/"serious"
# fail the build here; "moderate"/"minor" are reported, not enforced, and
# any accepted ones are written up honestly in docs/CURRENT_STATUS.md.
BLOCKING_IMPACTS = {"critical", "serious"}


def _run_axe(page: Any) -> list[dict[str, Any]]:
    assert AXE_SCRIPT_PATH.exists(), (
        f"axe-core not found at {AXE_SCRIPT_PATH} — run `npm install` in "
        "tests/accessibility/ first (see this module's docstring)."
    )
    page.add_script_tag(path=str(AXE_SCRIPT_PATH))
    results_json = page.evaluate(
        "async () => JSON.stringify(await axe.run(document, "
        "{runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa']}}))"
    )
    results = json.loads(results_json)
    violations: list[dict[str, Any]] = results["violations"]
    return violations


def _assert_no_blocking_violations(violations: list[dict[str, Any]], page_label: str) -> None:
    blocking = [v for v in violations if v["impact"] in BLOCKING_IMPACTS]
    if blocking:
        details = "\n".join(
            f"  - [{v['impact']}] {v['id']}: {v['help']} "
            f"({len(v['nodes'])} node(s), e.g. {v['nodes'][0]['target']})"
            for v in blocking
        )
        pytest.fail(f"axe-core found blocking violations on {page_label}:\n{details}")


def _login(page: Any, live_server: Any, username: str, password: str, redirect_to: str) -> None:
    page.goto(f"{live_server.url}/accounts/login/")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server.url}{redirect_to}")


def test_login_page_has_no_blocking_accessibility_violations(live_server) -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}/accounts/login/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "login page")
        finally:
            browser.close()


def test_organization_list_has_no_blocking_accessibility_violations(live_server) -> None:
    membership = OrganizationMembershipFactory(user=UserFactory(), role=CustomerRole.OWNER)
    membership.user.set_password(DEMO_PASSWORD)
    membership.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            _login(page, live_server, membership.user.username, DEMO_PASSWORD, "/organizations/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "organization list")
        finally:
            browser.close()


def test_facility_list_has_no_blocking_accessibility_violations(live_server) -> None:
    membership = OrganizationMembershipFactory(user=UserFactory(), role=CustomerRole.OWNER)
    membership.user.set_password(DEMO_PASSWORD)
    membership.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            _login(page, live_server, membership.user.username, DEMO_PASSWORD, "/organizations/")
            page.goto(f"{live_server.url}/facilities/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "facility list")
        finally:
            browser.close()


def test_dispatch_board_has_no_blocking_accessibility_violations(live_server) -> None:
    dispatcher = InternalRoleAssignmentFactory().user
    dispatcher.set_password(DEMO_PASSWORD)
    dispatcher.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            _login(page, live_server, dispatcher.username, DEMO_PASSWORD, "/organizations/")
            page.goto(f"{live_server.url}/dispatch/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "dispatch board")
        finally:
            browser.close()


def test_courier_job_offer_list_has_no_blocking_accessibility_violations(live_server) -> None:
    courier = CourierProfileFactory()
    courier.user.set_password(DEMO_PASSWORD)
    courier.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            # Mobile-first courier PWA — scan at a narrow phone viewport,
            # matching docs/PRODUCT_REQUIREMENTS.md section 6.
            page.set_viewport_size({"width": 390, "height": 844})
            _login(page, live_server, courier.user.username, DEMO_PASSWORD, "/organizations/")
            page.goto(f"{live_server.url}/couriers/offers/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "courier job offer list (mobile viewport)")
        finally:
            browser.close()


def test_courier_availability_screen_has_no_blocking_accessibility_violations(
    live_server,
) -> None:
    """New screen (courier PWA availability/profile/active-delivery pass, see
    docs/CURRENT_STATUS.md) — scanned the same way as the pre-existing
    courier job offer list: real login, mobile viewport, zero
    critical/serious axe-core violations required."""
    courier = CourierProfileFactory()
    courier.user.set_password(DEMO_PASSWORD)
    courier.user.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 390, "height": 844})
            _login(page, live_server, courier.user.username, DEMO_PASSWORD, "/organizations/")
            page.goto(f"{live_server.url}/couriers/availability/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(
                violations, "courier availability screen (mobile viewport)"
            )
        finally:
            browser.close()


def test_recipient_tracking_page_has_no_blocking_accessibility_violations(live_server) -> None:
    delivery_request = DeliveryRequestFactory()
    token = generate_recipient_tracking_token(delivery_request)

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{live_server.url}/recipient/{token}/")
            violations = _run_axe(page)
            _assert_no_blocking_violations(violations, "recipient tracking page")
        finally:
            browser.close()
