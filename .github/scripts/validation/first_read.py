"""First-read routing checks for root surfaces and the first-contribution map."""

from __future__ import annotations

import re
from pathlib import Path

from . import entrypoints
from .common import read_text, rel

FIRST_CONTRIBUTION = "docs/agents/first-contribution.md"

README_REQUIREMENTS = (
    "<!-- BEGIN GENERATED START-HERE ROUTE -->",
    "**Mission.**",
    "**Next useful action.**",
    "**Access.**",
    "python scripts/start_here.py",
    "docs/agents/start-here.md",
    "explicitly authorized live campaign",
    "## §0 —",
)

AGENTS_REQUIREMENTS = (
    "<!-- BEGIN GENERATED START-HERE ROUTE -->",
    "**Mission.**",
    "**Immediate action.**",
    "**Access.**",
    "docs/agents/start-here.md",
    FIRST_CONTRIBUTION,
    "README.md",
    "python scripts/start_here.py",
)

CLAUDE_REQUIREMENTS = (
    "<!-- BEGIN GENERATED START-HERE ROUTE -->",
    "**Mission.**",
    "**Immediate action.**",
    "**Access.**",
    "docs/agents/start-here.md",
    "README.md",
    "python scripts/start_here.py",
)

FIRST_CONTRIBUTION_REQUIREMENTS = (
    "## Context capsule",
    "## Task-to-file-to-validation map",
    "## Ranked ways to contribute",
    "## First action and done criteria",
    "user_objective",
    "authoritative_files",
    "current_evidence",
    "next_action",
    "user instructed",
    "fabricate",
    "requirements-docs.txt",
    "## Optional fixture completion route",
    "--check-completion",
    "manual review",
)

BANNED_BLANKET_PATTERNS = (
    (
        r"(?<![\w-])read every (?:bundled )?(?:skill|SKILL)",
        "blanket read-every-skill requirement",
    ),
    (r"every printed path", "blanket read-every-printed-path requirement"),
    (
        r"register[^.\n]{0,60}before[^.\n]{0,40}(?:edit|writing|documentation)",
        "register-before-docs requirement",
    ),
    (
        r"(?:bootstrap|wallet)[^.\n]{0,60}before[^.\n]{0,40}(?:edit|writing|documentation)",
        "bootstrap-before-docs requirement",
    ),
)

ROOT_FILES = ("README.md", "AGENTS.md", "CLAUDE.md")


def _require(report, path: Path, root: Path, needles) -> None:
    if not path.is_file():
        report.fail(f"first-read file missing: {rel(root, path)}")
        return
    text = read_text(path)
    for needle in needles:
        if needle not in text:
            report.fail(f"{rel(root, path)}: missing first-read content {needle!r}")


def check_first_read(report, root: Path) -> None:
    entrypoints.check_entry_consistency(report, root)
    _require(report, root / "README.md", root, README_REQUIREMENTS)
    _require(report, root / "AGENTS.md", root, AGENTS_REQUIREMENTS)
    _require(report, root / "CLAUDE.md", root, CLAUDE_REQUIREMENTS)
    _require(report, root / FIRST_CONTRIBUTION, root, FIRST_CONTRIBUTION_REQUIREMENTS)

    readme = root / "README.md"
    if readme.is_file():
        text = read_text(readme)
        campaign = text.find("## §0 —")
        first_screen = text[:campaign] if campaign >= 0 else text
        mission = first_screen.find("**Mission.**")
        action = first_screen.find("**Next useful action.**")
        access = first_screen.find("**Access.**")
        if min(mission, action, access) < 0 or not mission < action < access:
            report.fail("README.md: first screen must present mission, useful action, then live-campaign access scope")
        if "CLOSED BETA" in first_screen.upper():
            report.fail("README.md: invite access must not gate offline research or contribution onboarding")

    for relpath in ("AGENTS.md", "CLAUDE.md"):
        path = root / relpath
        if not path.is_file():
            continue
        text = read_text(path)
        start = text.find("<!-- BEGIN GENERATED START-HERE ROUTE -->")
        end = text.find("<!-- END GENERATED START-HERE ROUTE -->")
        if start < 0 or end < start:
            continue
        route = text[start:end]
        if "**Mission.**" not in route or "**Immediate action.**" not in route or "**Essential rules.**" not in route:
            report.fail(f"{relpath}: generated root route must be self-contained with mission, action, and essential rules")
        if re.search(r"ScheduleWakeup|CronCreate|update_plan|hermes cron|opencode run|openclaw cron", route, re.I):
            report.fail(f"{relpath}: generated root route duplicates product-specific harness instructions")

    for name in ROOT_FILES + (FIRST_CONTRIBUTION,):
        path = root / name
        if not path.is_file():
            continue
        text = read_text(path)
        for pattern, why in BANNED_BLANKET_PATTERNS:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                report.fail(
                    f"{name}: {why}: {match.group(0).strip()[:80]!r}"
                )
