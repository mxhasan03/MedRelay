"""A deliberately crude, keyword-based guard against describing excluded cargo.

docs/PRODUCT_REQUIREMENTS.md section 3 explicitly excludes patient
transportation, Category A infectious substances, controlled substances,
human organs, radioactive material, regulated medical waste, loose sharps,
unsealed specimens, specialized blood products, emergency-response cargo,
air shipments, and courier packaging/repacking. Phase 2's primary defense
against these is structural: only three `CargoClass` rows exist
(docs/CURRENT_STATUS.md), and none of them describe an excluded category —
there is no form field, API field, or admin action anywhere that lets a
caller select or create a cargo class outside those three.

This module is the secondary, best-effort guard for free-text fields
(delivery-request instructions, packaging-attestation notes) where a user
could still *describe* excluded cargo in prose even though they can't
formally classify it that way. It is a simple case-insensitive substring
match against a fixed keyword list — NOT a compliance control, NOT
real NLP, and trivially evadable (misspellings, synonyms not on the list,
non-English text). It exists to catch the obvious/accidental case and to
give this prototype *some* documented safety-conscious behavior here, not to
provide any real assurance. See docs/CURRENT_STATUS.md "Phase 2" section for
the same disclosure.
"""

from __future__ import annotations

# keyword (lowercase substring) -> human-readable excluded-category label.
# Several keywords intentionally map to the same label (near-synonyms).
PROHIBITED_CARGO_KEYWORDS: dict[str, str] = {
    "patient transport": "patient transportation",
    "transport the patient": "patient transportation",
    "category a infectious": "Category A infectious substance",
    "infectious substance": "Category A infectious substance",
    "controlled substance": "controlled substance",
    "schedule ii": "controlled substance",
    "schedule 2": "controlled substance",
    "narcotic": "controlled substance",
    "human organ": "human organ",
    "donor organ": "human organ",
    "radioactive": "radioactive material",
    "regulated medical waste": "regulated medical waste",
    "medical waste": "regulated medical waste",
    "biohazard waste": "regulated medical waste",
    "loose sharps": "loose sharps",
    "uncontained sharps": "loose sharps",
    "unsealed specimen": "unsealed specimen",
    "open specimen": "unsealed specimen",
    "specialized blood product": "specialized blood product",
    "whole blood unit": "specialized blood product",
    "emergency response": "emergency-response cargo",
    "emergency medical response": "emergency-response cargo",
    "air shipment": "air shipment",
    "air freight": "air shipment",
    "repackage": "courier packaging/repacking",
    "repacking": "courier packaging/repacking",
    "courier will repack": "courier packaging/repacking",
}


def find_prohibited_cargo_keywords(text: str) -> list[str]:
    """Return the sorted, de-duplicated set of excluded-category labels a
    free-text field's content appears to reference, or an empty list."""
    if not text:
        return []
    lowered = text.lower()
    hits = {label for keyword, label in PROHIBITED_CARGO_KEYWORDS.items() if keyword in lowered}
    return sorted(hits)
