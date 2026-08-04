"""Generic CSV/HTML tabular renderers shared by `apps.reporting`'s report
exports and `apps.billing`'s invoice export.

Deliberately dependency-free (no Django model imports) so both apps can
import it without creating an app-to-app model coupling — see
docs/CURRENT_STATUS.md "Phase 7" for the "HTML/CSV, not PDF" design decision
this module implements (docs/TECH_STACK_AND_ZERO_COST_POLICY.md: "WeasyPrint
is optional only if its system dependencies remain fully local/free;
otherwise use HTML/CSV exports first" — WeasyPrint's system dependencies
(Pango/Cairo/GDK-Pixbuf) were not confirmed available in this environment, so
this phase defaults to HTML/CSV, exactly as the policy directs when that
confirmation hasn't been done).

Every export carries the project's required disclaimer verbatim
(`config.context_processors.DEMO_DISCLAIMER`) — a generated report is
exactly the kind of "relevant document" CLAUDE.md requires it on.
"""

from __future__ import annotations

import csv
import html
import io
from collections.abc import Iterable, Mapping
from typing import Any

from config.context_processors import DEMO_DISCLAIMER


def rows_to_csv(fieldnames: Iterable[str], rows: Iterable[Mapping[str, Any]], *, title: str) -> str:
    """Render `rows` as CSV text, with a leading disclaimer/title row and a
    blank separator row before the real header row — spreadsheet tools treat
    this as an ordinary (if slightly odd-looking) first data row, not a
    parse error, since every exported file always has at least one column."""
    fieldnames = list(fieldnames)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"{title} — {DEMO_DISCLAIMER}"])
    writer.writerow([])
    dict_writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    dict_writer.writeheader()
    for row in rows:
        dict_writer.writerow({key: row.get(key, "") for key in fieldnames})
    return buffer.getvalue()


def rows_to_html(
    fieldnames: Iterable[str], rows: Iterable[Mapping[str, Any]], *, title: str
) -> str:
    """Render `rows` as a minimal, dependency-free standalone HTML page —
    matching the project's plain-HTML template convention, not a styled
    design-system page (Phase 8 is the real UX pass)."""
    fieldnames = list(fieldnames)
    header_html = "".join(f"<th>{html.escape(str(f))}</th>" for f in fieldnames)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(row.get(f, '')))}</td>" for f in fieldnames)
        body_rows.append(f"<tr>{cells}</tr>")
    body_html = "".join(body_rows) or f"<tr><td colspan='{len(fieldnames)}'>No rows.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)} — MedRelay (Demo Prototype)</title>
</head>
<body style="font-family:sans-serif;">
<div style="background:#7a1f1f;color:#fff;padding:0.75rem 1rem;font-size:0.9rem;">
{html.escape(DEMO_DISCLAIMER)}
</div>
<h1>{html.escape(title)}</h1>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
<thead><tr>{header_html}</tr></thead>
<tbody>{body_html}</tbody>
</table>
</body>
</html>
"""


__all__ = ["rows_to_csv", "rows_to_html"]
