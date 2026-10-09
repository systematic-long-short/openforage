"""Consistency checks for direct-arrival and portable onboarding pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .common import read_text, rel

FIRST_USE_FILES = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agents/quickstart.md",
    "docs/agents/first-search.md",
    "docs/agents/install-authenticate.md",
    "docs/agents/research-opportunities.md",
    "docs/agents/start-here.md",
    "docs/agents/first-contribution.md",
    "docs/start-here/onboard-your-agent.md",
)
GENERATED_FRAGMENT_FILES = (
    "docs/agents/quickstart.md",
    "docs/agents/first-search.md",
    "docs/agents/install-authenticate.md",
    "docs/agents/research-opportunities.md",
    "docs/agents/first-contribution.md",
    "docs/start-here/onboard-your-agent.md",
)
BEGIN = "<!-- BEGIN GENERATED ENTRY GUIDANCE -->"
END = "<!-- END GENERATED ENTRY GUIDANCE -->"

STALE_ALGORITHM_EXCLUSIVITY = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:random[_ ]weighted)[^\n]{0,140}\b(?:only|sole)\b[^\n]{0,100}"
    r"\b(?:stable|documented|supported)\b"
    r"|"
    r"\b(?:only|sole)\b[^\n]{0,100}\b(?:stable|documented|supported)\b"
    r"[^\n]{0,140}(?:random[_ ]weighted)"
    r")"
)
REGISTRATION_GATES_OFFLINE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\b(?:register(?:ing)?|registration|invite(?: code)?)\b[^\n]{0,100}"
    r"\b(?:before|prior to|to begin|is required for|must have)\b[^\n]{0,80}"
    r"\b(?:offline|research|contribut\w*|documentation|reading)\b"
    r"|"
    r"\b(?:offline|research|contribut\w*|documentation|reading)\b[^\n]{0,100}"
    r"\b(?:requires?|needs?)\b[^\n]{0,80}\b(?:registration|invite code)\b"
    r")"
)


def check_algorithm_exclusivity(report, texts: dict[str, str]) -> None:
    """Reject stale claims that exclude an algorithm in the observed surface."""
    for relpath, text in texts.items():
        match = STALE_ALGORITHM_EXCLUSIVITY.search(text)
        if match:
            report.fail(
                f"{relpath}: stale algorithm exclusivity claim; compatibility records both genetic and random_weighted"
            )


def check_entry_consistency(report, root: Path) -> None:
    texts: dict[str, str] = {}
    for relpath in FIRST_USE_FILES:
        path = root / relpath
        if not path.is_file():
            report.fail(f"entrypoints: missing first-use page {relpath}")
            continue
        texts[relpath] = read_text(path)

    for relpath in GENERATED_FRAGMENT_FILES:
        text = texts.get(relpath)
        if text is None:
            continue
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            report.fail(f"{relpath}: must contain one generated portable-entry fragment")
            continue
        start = text.index(BEGIN)
        end = text.index(END)
        if end < start:
            report.fail(f"{relpath}: generated entry-fragment markers are reversed")
            continue
        fragment = text[start:end]
        fragment_lower = fragment.casefold()
        if "offline first" not in fragment_lower or "no invite" not in fragment_lower:
            report.fail(f"{relpath}: first screen must offer an offline action without registration or install gates")
        if not all(token in fragment_lower for token in (
            "authorized live path",
            "independently started",
            "same harness",
            "distinct session identity",
        )):
            report.fail(f"{relpath}: live search must name an independent same-harness CLI session and distinct identity")
        algorithm_facts_present = all(token in fragment_lower for token in (
            "genetic", "random_weighted", "100,000", "target only"
        )) and bool(re.search(
            r"\bno\b[^.\n]{0,45}\bfull ga benchmark\b[^.\n]{0,35}\b(?:run|measured)\b",
            fragment_lower,
        ))
        if not algorithm_facts_present:
            report.fail(f"{relpath}: generated algorithm facts must distinguish callable choices, the GA target, and missing benchmark results")

    check_algorithm_exclusivity(report, texts)

    for relpath, text in texts.items():
        match = REGISTRATION_GATES_OFFLINE.search(text)
        is_negated_requirement = bool(
            match
            and re.search(r"\b(?:do|does|did)\s+not\b|\b(?:never|without)\b", match.group(0), re.I)
        )
        if match and not is_negated_requirement:
            report.fail(
                f"{relpath}: invite or registration must not gate offline research, contribution, documentation, or reading"
            )

    packet = texts.get("docs/agents/start-here.md", "")
    action_at = packet.find("## Immediate action")
    context_at = packet.find("## Essential context")
    if action_at < 0 or context_at < 0 or action_at > context_at:
        report.fail("docs/agents/start-here.md: immediate offline action must precede supporting context")

    onboarding_path = root / "docs/agents/references/onboarding.json"
    try:
        onboarding = json.loads(read_text(onboarding_path))
    except (OSError, json.JSONDecodeError):
        report.fail("entrypoints: canonical onboarding JSON is unavailable for fixture-route consistency")
        onboarding = {}
    tasks = onboarding.get("tasks", {}) if isinstance(onboarding, dict) else {}
    research_fixtures = tasks.get("research", {}).get("fixture_contracts", {}) if isinstance(tasks, dict) else {}
    contribution_fixtures = tasks.get("contribute", {}).get("fixture_contracts", {}) if isinstance(tasks, dict) else {}
    if not isinstance(research_fixtures, dict) or not research_fixtures:
        report.fail("entrypoints: canonical research fixture contract is missing")
    else:
        for fixture_id in research_fixtures:
            selector = f"--task research --fixture {fixture_id}"
            if selector not in packet:
                report.fail(f"docs/agents/start-here.md: generated packet omits canonical opt-in route {fixture_id!r}")
    contribution = texts.get("docs/agents/first-contribution.md", "")
    if not isinstance(contribution_fixtures, dict) or not contribution_fixtures:
        report.fail("entrypoints: canonical contribution fixture contract is missing")
    elif not all(token in contribution for token in (
        "Optional fixture completion route", "--fixture", "--check-completion",
        "optional scratch path", "manual review",
    )):
        report.fail("docs/agents/first-contribution.md: optional fixture boundary and final read-only check are not disclosed")

    for relpath in ("README.md", "AGENTS.md", "CLAUDE.md"):
        text = texts.get(relpath, "")
        start = text.find("<!-- BEGIN GENERATED START-HERE ROUTE -->")
        end = text.find("<!-- END GENERATED START-HERE ROUTE -->")
        if start < 0 or end < start:
            continue
        route = text[start:end].casefold()
        if not all(token in route for token in (
            "offline", "independently started", "same harness", "distinct session identity"
        )):
            report.fail(f"{relpath}: generated route is missing the offline/live-session boundary")
