"""Regression test for a real production bug: Django's `{# ... #}` comment tag only
strips *single-line* comments — a `{# ... #}` span containing a newline is left as
literal text in the rendered output instead of being stripped, because Django's
comment-matching regex does not match across newlines.

This was discovered live: `templates/base.html` and three other templates used
multi-line `{# ... #}` blocks for extensive inline documentation, and every one of
them leaked verbatim into the rendered HTML of a real deployed page (confirmed via
`docs/CURRENT_STATUS.md`'s dated addendum). Phase 8's own axe-core accessibility
scans never caught this, because extraneous-but-readable text doesn't fail
accessibility checks (contrast, ARIA, labels) — it's a correctness bug, not an
a11y one, so it needs its own dedicated check.

The fix is always the same: use `{% comment %}...{% endcomment %}` instead, which
Django *does* support across multiple lines. This test statically scans every
template file for the broken pattern so it can never silently reappear, rather than
relying on rendering every template with a valid context (many templates require
specific objects in context that this test would otherwise have to fabricate).
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Matches a `{# ... #}` span, DOTALL so it spans newlines — exactly mirroring
# what a human reader (or the previous Django-comment-shaped-but-broken syntax)
# would consider "one comment," regardless of how many lines it covers.
COMMENT_SPAN_RE = re.compile(r"\{#(.*?)#\}", re.DOTALL)


def test_no_template_has_a_multiline_hash_comment() -> None:
    offenders: list[str] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        content = path.read_text(encoding="utf-8")
        for match in COMMENT_SPAN_RE.finditer(content):
            if "\n" in match.group(1):
                line_number = content.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(TEMPLATES_DIR.parent)}:{line_number}")

    assert not offenders, (
        "Found multi-line {# ... #} Django comment(s), which Django does NOT strip "
        "(only single-line {# #} comments are removed — this is a real Django "
        "limitation, not a style preference). Use {% comment %}...{% endcomment %} "
        f"instead, which supports multiple lines. Offending locations: {offenders}"
    )
