"""Genuine, real-browser critical-path test (Phase 8 acceptance criterion):
log in as a dispatcher, view the dispatch board, open a specific delivery's
dispatch detail, and assign an eligible courier through the real HTML form —
then confirm the assignment actually landed by reloading the dispatch board.

Why this flow: dispatch/assignment (`apps.dispatch`) is the operational core
of this platform — the one console an internal ops user drives every real
delivery through. It touches four real pages (login, dispatch board list,
dispatch board detail, and the post-assign redirect back to detail) and one
real state-changing POST, all through a real Chromium browser rather than
the Django test client, which cannot execute JavaScript, evaluate real
network round-trips, or verify that server-rendered HTML forms actually
work end-to-end the way a human dispatcher would drive them.

Same working Playwright setup as the Phase 5 test
(`tests/integration/test_pwa_browser.py`): a real `live_server` +
`playwright.sync_api`, real Chromium (confirmed working in this sandbox
without `--with-deps`/sudo).
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import InternalRoleAssignmentFactory
from apps.cargo.models import CargoClassCode, TemperatureProfileCode
from apps.cargo.tests.factories import (
    CargoClassFactory,
    CargoPolicyFactory,
    PackagingAttestationFactory,
    TemperatureProfileFactory,
)
from apps.couriers.models import CourierCredentialType, CourierStatus, IdentityReviewStatus
from apps.couriers.tests.factories import (
    CargoAuthorizationFactory,
    CourierAvailabilityFactory,
    CourierCredentialFactory,
    CourierProfileFactory,
    VehicleFactory,
)
from apps.deliveries.models import DeliveryStatus, StopType
from apps.deliveries.state_machine import transition_delivery_request
from apps.deliveries.tests.factories import DeliveryRequestFactory, DeliveryStopFactory
from apps.dispatch.models import AssignmentStatus, DeliveryAssignment
from apps.facilities.tests.factories import FacilityFactory, ServiceZoneFactory

playwright_sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.django_db(transaction=True)

DEMO_PASSWORD = "MedRelayDispatchE2ETest!2026"  # pragma: allowlist secret


def _ready_for_dispatch_delivery(*, pickup_zone=None):
    cargo_class = CargoClassFactory(code=CargoClassCode.CLASS_2)
    CargoPolicyFactory(cargo_class=cargo_class, allows_ambient=True, allows_refrigerated=True)
    temperature_profile = TemperatureProfileFactory(code=TemperatureProfileCode.AMBIENT)
    delivery_request = DeliveryRequestFactory(
        cargo_class=cargo_class, temperature_profile=temperature_profile
    )
    pickup_facility = FacilityFactory(service_zone=pickup_zone)
    destination_facility = FacilityFactory()
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.PICKUP,
        sequence=1,
        facility=pickup_facility,
    )
    DeliveryStopFactory(
        delivery_request=delivery_request,
        stop_type=StopType.DESTINATION,
        sequence=2,
        facility=destination_facility,
    )
    PackagingAttestationFactory(delivery_request=delivery_request)
    transition_delivery_request(delivery_request, DeliveryStatus.SUBMITTED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.VALIDATION_REQUIRED, actor=None)
    transition_delivery_request(delivery_request, DeliveryStatus.READY_FOR_DISPATCH, actor=None)
    return delivery_request, cargo_class


def _eligible_courier(cargo_class, zone):
    courier = CourierProfileFactory(
        status=CourierStatus.APPROVED, identity_review_status=IdentityReviewStatus.APPROVED
    )
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.DRIVER_LICENSE)
    CourierCredentialFactory(courier=courier, credential_type=CourierCredentialType.INSURANCE)
    CargoAuthorizationFactory(courier=courier, cargo_class=cargo_class)
    VehicleFactory(courier=courier)
    CourierAvailabilityFactory(courier=courier, is_online=True, current_service_zone=zone)
    return courier


def test_dispatcher_logs_in_and_assigns_a_courier_through_the_real_dispatch_board(
    live_server,
) -> None:
    zone = ServiceZoneFactory()
    delivery_request, cargo_class = _ready_for_dispatch_delivery(pickup_zone=zone)
    courier = _eligible_courier(cargo_class, zone)

    dispatcher = InternalRoleAssignmentFactory().user
    dispatcher.set_password(DEMO_PASSWORD)
    dispatcher.save()

    with playwright_sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()

            # Step 1: real login through the real /accounts/login/ view.
            page.goto(f"{live_server.url}/accounts/login/")
            page.fill("#id_username", dispatcher.username)
            page.fill("#id_password", DEMO_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(f"{live_server.url}/organizations/")

            # Step 2: the dispatch board lists the unassigned delivery.
            page.goto(f"{live_server.url}/dispatch/")
            assert page.locator("h1", has_text="Dispatch Board").is_visible()
            # `{{ delivery_request.pk|truncatechars:8 }}` renders the first 7
            # raw characters plus an ellipsis, so match on 7, not 8.
            short_id = str(delivery_request.pk)[:7]
            page.click(f"text={short_id} >> xpath=ancestor::tr >> a:has-text('Dispatch')")
            page.wait_for_url(f"{live_server.url}/dispatch/{delivery_request.pk}/")

            # Step 3: assign the eligible courier via the real HTML form (not
            # a direct service-layer call) — this is the actual state-changing
            # action a real dispatcher performs.
            assign_form = page.locator(f"tr:has-text('{courier.user}') form[action$='/assign/']")
            assign_form.locator("input[name=reason]").fill(
                "E2E critical-path test — top-ranked candidate."
            )
            assign_form.locator("button[type=submit]").click()
            page.wait_for_url(f"{live_server.url}/dispatch/{delivery_request.pk}/")

            # Step 4: the now-reloaded page shows the real, committed
            # assignment — confirmed both in the rendered DOM and in the
            # database (the actual server-side effect of the browser action).
            assert page.locator("text=Currently assigned").is_visible()
            assert page.locator(f"text={courier.user}").first.is_visible()
        finally:
            browser.close()

    delivery_request.refresh_from_db()
    assert delivery_request.status == DeliveryStatus.ASSIGNED
    active_assignment = DeliveryAssignment.objects.get(
        delivery_request=delivery_request, status=AssignmentStatus.ACTIVE
    )
    assert active_assignment.courier_id == courier.pk
