"""Hard acceptance criterion: no sensitive data in notification logs.

`apps.notifications.payload.build_notification_payload` is the explicit,
testable choke point — this file proves both halves: a disallowed field is
rejected (never silently persisted), and a normal, compliant payload is
accepted and returned intact.
"""

from __future__ import annotations

import pytest

from apps.notifications.payload import (
    ALLOWED_NOTIFICATION_FIELDS,
    DisallowedNotificationFieldError,
    build_notification_payload,
)


def test_compliant_payload_is_accepted_and_returned_intact() -> None:
    fields = {
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "organization_id": 42,
        "status": "in_transit",
        "package_code": "PKG-ABC123",
    }
    payload = build_notification_payload(fields)
    assert payload == fields


def test_none_values_are_dropped_from_a_compliant_payload() -> None:
    payload = build_notification_payload({"delivery_id": "abc", "facility_id": None})
    assert payload == {"delivery_id": "abc"}


@pytest.mark.parametrize(
    "disallowed_field",
    [
        "recipient_contact_name",
        "recipient_contact_phone",
        "sender_contact_name",
        "sender_contact_phone",
        "address",
        "signature_data_url",
        "typed_signature_name",
        "pin",
        "pin_hash",
        "facility_instructions",
        "diagnosis",
        "notes",
    ],
)
def test_disallowed_field_is_rejected_not_silently_persisted(disallowed_field: str) -> None:
    """Attempting to construct a notification payload containing a
    sender/recipient contact name/phone, a signature, a raw PIN, or
    PHI-adjacent free text must raise, never silently strip-and-succeed
    (which could hide a real bug where sensitive data almost made it into a
    log) and never silently persist the field."""
    fields = {"delivery_id": "abc", disallowed_field: "sensitive-value"}
    with pytest.raises(DisallowedNotificationFieldError) as exc_info:
        build_notification_payload(fields)
    assert disallowed_field in str(exc_info.value)
    assert disallowed_field not in ALLOWED_NOTIFICATION_FIELDS


def test_disallowed_field_error_lists_every_offending_key() -> None:
    with pytest.raises(DisallowedNotificationFieldError) as exc_info:
        build_notification_payload(
            {"delivery_id": "abc", "recipient_contact_name": "Jane Doe", "pin": "1234"}
        )
    message = str(exc_info.value)
    assert "recipient_contact_name" in message
    assert "pin" in message


def test_a_mixed_payload_with_one_bad_field_is_rejected_wholesale() -> None:
    """A payload that is mostly compliant but carries one disallowed field
    must be rejected in full — never partially persisted with only the
    disallowed field stripped, since that would hide the caller's bug."""
    with pytest.raises(DisallowedNotificationFieldError):
        build_notification_payload(
            {"delivery_id": "abc", "organization_id": 1, "status": "delivered", "phone": "555-0100"}
        )
