"""Explicit length limits for the free-text fields this app lets a courier's
browser submit directly (Phase 8 — docs/SECURITY_COMPLIANCE_BOUNDARIES.md
section 4 / upload-input-limits acceptance criterion).

This codebase has no real `FileField`/`ImageField` anywhere — the closest
thing to an "upload" is `signature_data_url`, a base64 PNG data: URL
captured from an HTML5 `<canvas>` signature pad and submitted as plain JSON
text (see `apps.custody.models`' module docstring). Django's `TextField` has
no built-in length ceiling the way `CharField(max_length=...)` does at the
database level, so this module is the single place that enforces one, both
as a model-level `MaxLengthValidator` (for any future admin/ModelForm path)
and as an explicit, pre-save check in `apps.custody.services` (since the
JSON-in/JSON-out courier endpoints build model instances directly and never
call `full_clean()` — see that module for the call sites).

The cap (300,000 characters, ~225 KB decoded) is generous for a small
canvas-drawn signature (a 300x100 PNG signature is typically a few KB) while
still bounding the worst case well below `DATA_UPLOAD_MAX_MEMORY_SIZE`
(`config/settings/base.py`, 5 MB) for a request carrying several such
fields.
"""

from __future__ import annotations

from django.core.validators import MaxLengthValidator

MAX_SIGNATURE_DATA_URL_LENGTH = 300_000

signature_data_url_length_validator = MaxLengthValidator(
    MAX_SIGNATURE_DATA_URL_LENGTH,
    message=(
        f"Signature image is too large (max {MAX_SIGNATURE_DATA_URL_LENGTH:,} characters "
        "of base64-encoded data)."
    ),
)


class SignatureTooLargeError(Exception):
    """Raised by `apps.custody.services.capture_proof_of_pickup`/
    `capture_proof_of_delivery` when `signature_data_url` exceeds
    `MAX_SIGNATURE_DATA_URL_LENGTH`."""


def check_signature_data_url_length(signature_data_url: str) -> None:
    if len(signature_data_url) > MAX_SIGNATURE_DATA_URL_LENGTH:
        raise SignatureTooLargeError(
            f"Signature image is too large (max {MAX_SIGNATURE_DATA_URL_LENGTH:,} characters "
            "of base64-encoded data)."
        )


__all__ = [
    "MAX_SIGNATURE_DATA_URL_LENGTH",
    "SignatureTooLargeError",
    "check_signature_data_url_length",
    "signature_data_url_length_validator",
]
