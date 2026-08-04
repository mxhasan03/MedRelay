"""The `NotificationProvider` adapter interface (docs/TECH_STACK_AND_ZERO_COST_POLICY.md
section 4's "adapter rule": every external capability goes through an
interface such as `NotificationProvider`, shipping with a local/mock
implementation; paid adapters are deferred indefinitely, not just "for now").

This is the first phase to actually build one of these named adapters as a
real Python `Protocol` (earlier phases only mentioned the *concept* in
docstrings — see e.g. `apps/custody/models.py`'s `ObjectStorageProvider`
mention, `apps/temperature/models.py`'s `TemperatureSensorProvider`
mention). Every provider call returns a `ProviderResult`: which provider
handled the request, its operating `mode` (`LOCAL` — a real local service
like Mailpit's SMTP capture, or `MOCK` — no real network call at all, like
the simulated SMS adapter), when the result was produced, a request
correlation ID (so a single logical notification's email+SMS+webhook fan-out
can be tied together in the logs), a `source`/`version` string identifying
the concrete implementation, and any warnings. `detail` is always the exact
allow-listed payload dict that was persisted (see
`apps.notifications.payload.build_notification_payload`) — never a raw
provider response blob that might smuggle in something outside the
allow-list.

Two implementations ship in this phase:

- `EmailNotificationProvider` — `mode=LOCAL`. Sends real SMTP via Django's
  `EMAIL_BACKEND` (`config/settings/base.py` already points this at the
  Mailpit compose service — see that file's "Email" section). This is a
  genuine local network call to a locally-hosted, zero-cost service, not a
  simulation.
- `SimulatedSmsProvider` — `mode=MOCK`. Never attempts a real network call
  to any SMS API (every paid SMS provider is prohibited per
  docs/TECH_STACK_AND_ZERO_COST_POLICY.md) — it only ever writes an
  `apps.notifications.models.SmsLogEntry` row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from django.utils import timezone


class ProviderMode:
    """String constants, not `TextChoices` (this dataclass is never stored
    directly on a model field — `apps.notifications.models.ProviderMode` is
    the model-facing `TextChoices` twin of this)."""

    LOCAL = "local"
    MOCK = "mock"


@dataclass(frozen=True)
class ProviderResult:
    """The uniform response shape every `NotificationProvider` call returns."""

    provider_name: str
    mode: str
    retrieved_at: datetime
    correlation_id: str
    source: str
    version: str
    success: bool
    detail: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class NotificationProvider(Protocol):
    """The adapter interface every notification-channel implementation
    satisfies. `send` never raises for an ordinary delivery failure (a
    "provider" that could not deliver still returns a `ProviderResult` with
    `success=False` and a warning) — it may only raise for a genuine
    programming error (e.g. a missing recipient email address), exactly the
    same "loud, not silent" convention `apps.notifications.payload` uses.
    """

    provider_name: str
    mode: str

    def send(
        self, *, notification_type: str, payload: dict[str, Any], **kwargs: Any
    ) -> ProviderResult: ...


def new_correlation_id() -> str:
    return uuid.uuid4().hex


class EmailNotificationProvider:
    """Real local SMTP delivery via Django's `EMAIL_BACKEND` (Mailpit in the
    compose stack; `django.core.mail.backends.locmem` in `config.settings.test`
    — see that module's comment). `mode=LOCAL`: this is a genuine network
    call to a locally-hosted, zero-cost service, not a simulation."""

    provider_name = "django-smtp"
    mode = ProviderMode.LOCAL
    version = "1.0"

    def send(
        self,
        *,
        notification_type: str,
        payload: dict[str, Any],
        to_email: str,
        subject: str,
        body: str,
    ) -> ProviderResult:
        from django.core.mail import send_mail

        correlation_id = new_correlation_id()
        warnings: list[str] = []
        success = True
        if not to_email:
            warnings.append("No recipient email address was available; message was not sent.")
            success = False
        else:
            send_mail(
                subject=subject,
                message=body,
                from_email=None,  # DEFAULT_FROM_EMAIL
                recipient_list=[to_email],
                fail_silently=False,
            )
        return ProviderResult(
            provider_name=self.provider_name,
            mode=self.mode,
            retrieved_at=timezone.now(),
            correlation_id=correlation_id,
            source="django.core.mail (Mailpit SMTP capture)",
            version=self.version,
            success=success,
            detail=payload,
            warnings=warnings,
        )


class SimulatedSmsProvider:
    """The simulated SMS adapter (docs/PRODUCT_REQUIREMENTS.md section 15:
    "logged/simulated SMS events... do not require a paid SMS or email
    provider"). `mode=MOCK`: **never** attempts a real network call to any
    SMS provider — this method makes no HTTP/socket call of any kind. Persisting
    the resulting `SmsLogEntry` row is the caller's job
    (`apps.notifications.services.send_sms_notification`), not this
    adapter's — `send` here only computes the synthetic provider response.
    """

    provider_name = "simulated-sms"
    mode = ProviderMode.MOCK
    version = "1.0"

    def send(
        self, *, notification_type: str, payload: dict[str, Any], **kwargs: Any
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.provider_name,
            mode=self.mode,
            retrieved_at=timezone.now(),
            correlation_id=new_correlation_id(),
            source="apps.notifications.providers.SimulatedSmsProvider (no real network call)",
            version=self.version,
            success=True,
            detail=payload,
            warnings=["Simulated SMS event only — no real carrier/API was contacted."],
        )


__all__ = [
    "EmailNotificationProvider",
    "NotificationProvider",
    "ProviderMode",
    "ProviderResult",
    "SimulatedSmsProvider",
    "new_correlation_id",
]
