"""Demonstrate real QR-code rendering for a `PackageIdentifier`.

Phase 2 only needs to prove the encoded value can be rendered as an actual
QR image (a full scanning UI is Phase 5 per docs/IMPLEMENTATION_ROADMAP.md).
This command renders one identifier's code to a PNG file on disk using
`PackageIdentifier.render_qr_png_bytes` (backed by `segno`, a pure-Python,
zero-cost dependency — see docs/TECH_STACK_AND_ZERO_COST_POLICY.md).

Usage:
    python manage.py render_package_qr <package-identifier-id-or-code> --out /tmp/qr.png
    python manage.py render_package_qr --out /tmp/qr.png   # picks any existing identifier
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.cargo.models import PackageIdentifier


class Command(BaseCommand):
    help = "Render a PackageIdentifier's code as a real PNG QR code, to prove the pipeline works."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "identifier",
            nargs="?",
            default=None,
            help="A PackageIdentifier numeric ID or its `code` (e.g. PKG-ABCDEF012345). If "
            "omitted, the most recently created identifier is used.",
        )
        parser.add_argument(
            "--out",
            default="/tmp/medrelay_package_qr.png",  # noqa: S108 - demo output path, not a secret
            help="Output PNG file path (default: /tmp/medrelay_package_qr.png).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        identifier_arg = options["identifier"]
        if identifier_arg is None:
            identifier = PackageIdentifier.objects.order_by("-created_at").first()
            if identifier is None:
                raise CommandError(
                    "No PackageIdentifier rows exist yet. Create a delivery request first, "
                    "or pass an identifier explicitly."
                )
        elif identifier_arg.isdigit():
            try:
                identifier = PackageIdentifier.objects.get(pk=int(identifier_arg))
            except PackageIdentifier.DoesNotExist as exc:
                raise CommandError(f"No PackageIdentifier with id={identifier_arg}.") from exc
        else:
            try:
                identifier = PackageIdentifier.objects.get(code=identifier_arg)
            except PackageIdentifier.DoesNotExist as exc:
                raise CommandError(f"No PackageIdentifier with code={identifier_arg!r}.") from exc

        png_bytes = identifier.render_qr_png_bytes()
        out_path = Path(options["out"])
        out_path.write_bytes(png_bytes)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rendered QR for identifier code {identifier.code!r} "
                f"({len(png_bytes)} PNG bytes) to {out_path}."
            )
        )
