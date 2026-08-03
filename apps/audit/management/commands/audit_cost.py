"""Fail-closed zero-cost policy audit.

Checks that every declared Python dependency is on an explicit allowlist of
open-source/free packages, and that no settings/config source file contains
an indicator string for a known paid/prohibited external service. On
success, (re)writes docs/COST_AUDIT.md. Exits non-zero on any violation
(fail-closed) so this can gate CI.

See docs/TECH_STACK_AND_ZERO_COST_POLICY.md for the policy this enforces.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# ---------------------------------------------------------------------------
# Allowlist of open-source/free Python packages this project may depend on.
# Anything not on this list (in pyproject.toml [project.dependencies] or
# [dependency-groups.dev]) fails the audit. Extend deliberately, one line per
# reviewed package, when a new free/open-source dependency is added.
# ---------------------------------------------------------------------------
ALLOWED_PACKAGES: set[str] = {
    "django",
    "djangorestframework",
    "drf-spectacular",
    "psycopg",
    "psycopg-binary",
    "celery",
    "redis",
    "django-environ",
    "gunicorn",
    "whitenoise",
    "pytest",
    "pytest-django",
    "pytest-cov",
    "factory-boy",
    "ruff",
    "mypy",
    "django-stubs",
    "django-stubs-ext",
    "djangorestframework-stubs",
    "coverage",
    "detect-secrets",
}

# ---------------------------------------------------------------------------
# Indicator strings for known paid/prohibited external services. Matching is
# deliberately case-insensitive substring matching over settings/config
# source. False positives are acceptable (fail-closed); false negatives are
# not, so keep this list broad rather than narrowly-scoped regexes.
# ---------------------------------------------------------------------------
PROHIBITED_INDICATORS: dict[str, str] = {
    "stripe": "Stripe (paid payment processing)",
    "twilio": "Twilio (paid SMS)",
    "auth0": "Auth0 (paid identity provider)",
    "okta": "Okta (paid identity provider)",
    "sentry_sdk": "Sentry SaaS (paid error tracking)",
    "sentry-sdk": "Sentry SaaS (paid error tracking)",
    "checkr": "Checkr (paid background-check API)",
    "pk.eyj": "Mapbox paid-tier public token pattern",
    "sk.eyj": "Mapbox paid-tier secret token pattern",
    "googleapis.com/maps": "Google Maps Platform (paid)",
    "AIzaSy": "Google API key pattern (commonly Maps Platform)",
}

# Directories/files scanned for prohibited-service indicators.
SCAN_PATHS = [
    Path("config"),
    Path("apps"),
    Path(".env.example"),
]

# Files/directories excluded from the indicator scan (this command itself
# necessarily documents the prohibited strings above).
EXCLUDED_PATH_PARTS = {"migrations", "__pycache__"}
EXCLUDED_FILENAMES = {"audit_cost.py"}


def _normalize(name: str) -> str:
    return re.sub(r"[_.]", "-", name).strip().lower()


def _parse_dependency_name(spec: str) -> str:
    """Extract a bare package name from a PEP 508 dependency specifier."""
    spec = spec.strip()
    # Strip environment markers (after ';'), then version/extras specifiers.
    spec = spec.split(";", 1)[0]
    match = re.match(r"^([A-Za-z0-9_.-]+)", spec)
    return _normalize(match.group(1)) if match else _normalize(spec)


class Command(BaseCommand):
    help = "Fail-closed audit of dependencies and config against the zero-cost policy."

    def handle(self, *args: Any, **options: Any) -> None:
        repo_root = Path(settings.BASE_DIR)
        violations: list[str] = []

        dependencies = self._load_dependencies(repo_root, violations)
        self._scan_for_prohibited_indicators(repo_root, violations)

        if violations:
            self.stderr.write(self.style.ERROR("Zero-cost policy audit FAILED:"))
            for violation in violations:
                self.stderr.write(self.style.ERROR(f"  - {violation}"))
            raise CommandError(
                f"{len(violations)} zero-cost policy violation(s) found. "
                "See docs/TECH_STACK_AND_ZERO_COST_POLICY.md."
            )

        self._write_report(repo_root, dependencies)
        self.stdout.write(
            self.style.SUCCESS(
                f"Zero-cost policy audit passed: {len(dependencies)} dependencies checked, "
                f"0 prohibited-service indicators found. Wrote docs/COST_AUDIT.md."
            )
        )

    def _load_dependencies(self, repo_root: Path, violations: list[str]) -> list[str]:
        pyproject_path = repo_root / "pyproject.toml"
        if not pyproject_path.exists():
            violations.append(f"pyproject.toml not found at {pyproject_path}")
            return []

        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)

        specs: list[str] = list(data.get("project", {}).get("dependencies", []))
        for group_deps in data.get("dependency-groups", {}).values():
            specs.extend(str(d) for d in group_deps if isinstance(d, str))

        names: list[str] = []
        for spec in specs:
            name = _parse_dependency_name(spec)
            names.append(name)
            if name not in ALLOWED_PACKAGES:
                violations.append(
                    f"Dependency '{name}' (from '{spec}') is not on the zero-cost ALLOWED_PACKAGES "
                    f"allowlist in apps/audit/management/commands/audit_cost.py"
                )

        return sorted(set(names))

    def _scan_for_prohibited_indicators(self, repo_root: Path, violations: list[str]) -> None:
        for scan_path in SCAN_PATHS:
            full_path = repo_root / scan_path
            if not full_path.exists():
                continue
            files = [full_path] if full_path.is_file() else list(full_path.rglob("*.py"))
            for file_path in files:
                if any(part in EXCLUDED_PATH_PARTS for part in file_path.parts):
                    continue
                if file_path.name in EXCLUDED_FILENAMES:
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                lowered = text.lower()
                for indicator, description in PROHIBITED_INDICATORS.items():
                    if indicator.lower() in lowered:
                        violations.append(
                            f"Prohibited-service indicator '{indicator}' ({description}) found in "
                            f"{file_path.relative_to(repo_root)}"
                        )

    def _write_report(self, repo_root: Path, dependencies: list[str]) -> None:
        docs_dir = repo_root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        report_path = docs_dir / "COST_AUDIT.md"

        generated_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        deps_list = "\n".join(f"- `{name}`" for name in dependencies) or "- (none found)"
        allowlist_list = "\n".join(f"- `{name}`" for name in sorted(ALLOWED_PACKAGES))
        indicators_list = "\n".join(
            f"- `{indicator}` — {description}"
            for indicator, description in PROHIBITED_INDICATORS.items()
        )
        scanned_paths = "\n".join(f"- `{p}`" for p in SCAN_PATHS)

        report = f"""# Cost Audit Report

Generated by `python manage.py audit_cost` on {generated_at}.

## Result: PASS

Every declared dependency is on the zero-cost allowlist, and no
prohibited-service indicator strings were found in the scanned configuration
and application source.

## What was checked

1. **Dependency allowlist** — every entry in `pyproject.toml`
   `[project.dependencies]` and `[dependency-groups]` was resolved to a bare
   package name and checked against an explicit allowlist maintained in
   `apps/audit/management/commands/audit_cost.py`.
2. **Prohibited-service indicators** — every `.py` file under the paths
   below (excluding migrations) plus `.env.example` was scanned
   (case-insensitively) for substrings associated with known paid/required
   external services (payment processors, paid SMS, paid identity providers,
   SaaS error tracking, paid background-check APIs, paid mapping APIs).

Scanned paths:

{scanned_paths}

## Dependencies found ({len(dependencies)})

{deps_list}

## Full allowlist

{allowlist_list}

## Prohibited-service indicators checked for

{indicators_list}

## Important scope note

This confirms zero required *software* cost for the demo prototype.

It does NOT mean a real operating courier business would cost $0 — see
`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` and the `PILOT_MODE` distinction in
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`. A real pilot has unavoidable
non-software costs (legal/compliance review, insurance, background checks,
staffing, production hosting, payment processing, and more).
"""
        report_path.write_text(report, encoding="utf-8")
