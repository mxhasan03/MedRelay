"""ASGI config for the MedRelay project.

Django Channels (for WebSocket status/location updates) is planned for a
later phase per docs/IMPLEMENTATION_ROADMAP.md and docs/CURRENT_STATUS.md,
but is not required or wired in for Phase 0. This is the plain Django ASGI
application.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_asgi_application()
