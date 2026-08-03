"""Tests for the deliberately crude prohibited-cargo keyword guard.

See apps/cargo/validation.py's module docstring: this is a documented,
best-effort placeholder, not a compliance control.
"""

from __future__ import annotations

import pytest

from apps.cargo.validation import find_prohibited_cargo_keywords

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "text",
    [
        "Please transport the patient along with this package.",
        "Contains a Category A infectious substance sample.",
        "This includes a controlled substance, handle carefully.",
        "Package contains a human organ for transplant.",
        "Radioactive material inside, handle with lead gloves.",
        "This is regulated medical waste from the ward.",
        "Loose sharps included in this box.",
        "This is an unsealed specimen, please seal before pickup.",
        "Contains a specialized blood product for transfusion.",
        "This is emergency response cargo for the incident team.",
        "Needs to go out as an air shipment tonight.",
        "Courier will repack the contents before delivery.",
    ],
)
def test_find_prohibited_cargo_keywords_detects_excluded_categories(text: str) -> None:
    hits = find_prohibited_cargo_keywords(text)
    assert hits, f"expected at least one hit for: {text!r}"


def test_find_prohibited_cargo_keywords_is_case_insensitive() -> None:
    assert find_prohibited_cargo_keywords("CONTROLLED SUBSTANCE onboard") != []


def test_find_prohibited_cargo_keywords_empty_for_ordinary_text() -> None:
    assert (
        find_prohibited_cargo_keywords("Ring the bell at the loading dock and ask for intake.")
        == []
    )


def test_find_prohibited_cargo_keywords_empty_string() -> None:
    assert find_prohibited_cargo_keywords("") == []
