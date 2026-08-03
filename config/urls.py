"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.health import healthz, readyz

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="organization-list", permanent=False)),
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Built-in Django auth views (login/logout/password change) — no self-service signup in this
    # prototype; accounts are provisioned via the admin or `seed_demo_data` (see
    # apps/organizations/management/commands/seed_demo_data.py).
    path("accounts/", include("django.contrib.auth.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("facilities/", include("apps.facilities.urls")),
]
