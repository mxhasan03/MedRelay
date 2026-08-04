"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.accounts.mfa import MedRelayLoginView, MfaEnrollView, MfaVerifyView
from config.health import healthz, readyz
from config.pwa import service_worker, web_manifest

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="organization-list", permanent=False)),
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # PWA shell (Phase 5 — see config/pwa.py's docstring for why these are
    # root-level routes rather than plain {% static %} references).
    path("manifest.json", web_manifest, name="web-manifest"),
    path("sw.js", service_worker, name="service-worker"),
    # Login is MedRelayLoginView (apps.accounts.mfa), not the stock
    # django.contrib.auth LoginView, so that a privileged user with a
    # confirmed TOTP device is routed through the MFA-verify step (Phase 8)
    # before a real session is established. Logout/password-change/reset
    # remain the stock Django auth views — no self-service signup in this
    # prototype; accounts are provisioned via the admin or `seed_demo_data`
    # (see apps/organizations/management/commands/seed_demo_data.py).
    path("accounts/login/", MedRelayLoginView.as_view(), name="login"),
    path("accounts/mfa/verify/", MfaVerifyView.as_view(), name="mfa-verify"),
    path("accounts/mfa/enroll/", MfaEnrollView.as_view(), name="mfa-enroll"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("facilities/", include("apps.facilities.urls")),
    path("deliveries/", include("apps.deliveries.urls")),
    path("dispatch/", include("apps.dispatch.urls")),
    path("couriers/", include("apps.couriers.urls")),
    path("tracking/", include("apps.tracking.urls")),
    path("incidents/", include("apps.incidents.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("recipient/", include("apps.recipient.urls")),
    path("billing/", include("apps.billing.urls")),
    path("reporting/", include("apps.reporting.urls")),
    path("audit/", include("apps.audit.urls")),
]
