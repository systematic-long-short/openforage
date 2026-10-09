#!/usr/bin/env python3
"""Validate the public OpenForage library packet.

Modes:
  secrets        secret/leakage scan
  truthfulness   stale onboarding defaults, unsupported API names, unverified-adapter claims
  registry       harnesses.json schema, statuses, routed pages, evidence-gated verified entries
  links          relative Markdown links and internal anchors resolve
  examples       strict template validation for examples/agent-campaign
  records        campaign record schema and state-machine legality (templates or filled)
  state-machine  authoritative docs/agents/references/campaign-state-machine.json
  controls       excluded spend/token control fields on the design surfaces
  guidance       AGENTS.md / CLAUDE.md routing, CONTRIBUTING, issue/PR templates, YAML parse
  first-read     root first-read routing and the first-contribution map
  evals          fresh-agent evaluation tasks, fixtures, and grader regressions
  opportunities  research opportunity map schema and honesty checks
  compatibility  supported-version/API expectations and probe selfcheck
  executables    deterministic lifecycle/next-action selfchecks and challenger smoke
  selftest       negative checks on throwaway repository copies
  all            run every mode above

Uses only the Python standard library for most modes; YAML parsing requires
the isolated docs environment (`python -m venv .venv-docs` and
`python -m pip install --quiet -r requirements-docs.txt`) and is required for
the `guidance` mode.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation import (  # noqa: E402
    anchors as validation_anchors,
    campaign as validation_campaign,
    compatibility as validation_compatibility,
    controls as validation_controls,
    evals as validation_evals,
    executables as validation_executables,
    entrypoints as validation_entrypoints,
    first_read as validation_first_read,
    guidance as validation_guidance,
    opportunities as validation_opportunities,
    registry as validation_registry,
    selftest as validation_selftest,
)
from validation.common import Report, iter_files, rel, read_text  # noqa: E402

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    ".tmp",
    ".venv",
    ".venv-docs",
}
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".yml", ".yaml", ".py", ".toml", ".txt", ".cfg"}

# The validator's own pattern definitions are excluded from the secret scan
# to avoid self-matching.
SECRET_SCAN_EXCLUDE = {
    ".github/scripts/validate_agent_docs.py",
    ".github/scripts/validation/controls.py",
    ".github/scripts/validation/selftest.py",
}

SECRET_PATTERNS: list[tuple[str, str]] = [
    ("pem-private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("hex-private-key-assignment", r"(?:private_key|wallet_key|secret_key)\s*[=:]\s*[\"'][0-9a-fA-F]{64}[\"']"),
    ("github-token", r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ("openai-style-token", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    (
        "bearer-literal",
        r"Authorization[\"']?\s*[:=]\s*[\"']?Bearer\s+(?!\*\*\*|<CODE>|<TOKEN>|\$)[A-Za-z0-9._-]{12,}",
    ),
    ("invite-code-literal", r"--invite-code\s+[\"']?(?!<CODE>|<code>)[A-Za-z0-9][A-Za-z0-9_-]{5,}"),
    ("invite-env-literal", r"OPENFORAGE_INVITE_CODE\s*=\s*[\"']?(?!<)[A-Za-z0-9_-]{8,}"),
    (
        "private-host-path",
        r"/(?:home|Users)/(?!(?:user|username|you|agent|example)(?:/|$))[A-Za-z0-9._-]+/",
    ),
    ("temp-private-path", r"/var/tmp/openforage[-_][A-Za-z0-9._-]+"),
    ("private-repo-name", r"(?:openforage[-_]private|private_openforage|openforage[-_]internal)"),
]

FORBIDDEN_FILES = {"wallet.key", ".env"}
FORBIDDEN_GLOBS = ("*.pem", "*.p12", "*.jks", "id_rsa", "id_ed25519")


def load_yaml(report: Report, path: Path):
    try:
        import yaml  # type: ignore
    except ImportError:
        report.fail(f"PyYAML is required to parse {rel(REPO_DEFAULT, path)}")
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception as exc:  # noqa: BLE001
        report.fail(f"{path.name}: YAML parse error: {exc}")
        return None


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def check_secrets(report: Report, root: Path) -> None:
    for path in iter_files(root):
        relpath = rel(root, path)
        if relpath in SECRET_SCAN_EXCLUDE:
            continue
        if path.name in FORBIDDEN_FILES or any(path.match(glob) for glob in FORBIDDEN_GLOBS):
            report.fail(f"forbidden credential file present: {relpath}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in SECRET_PATTERNS:
                if re.search(pattern, line):
                    report.fail(f"{relpath}:{line_no}: possible secret/leakage ({name})")


# ---------------------------------------------------------------------------
# truthfulness
# ---------------------------------------------------------------------------

CANONICAL_ONBOARDING = (
    "docs/agents/quickstart.md",
    "docs/agents/first-search.md",
)

UNSUPPORTED_API_PATTERNS = [
    ("openforage.status(", "openforage.status() is not an API; use openforage.search_status()"),
    (
        "openforage.get_available_universes(",
        "use openforage.get_available_named_universes()",
    ),
    (
        "openforage.templates.hill_climb",
        "the installed surface exposes random_weighted and genetic only; forking uses the Python API",
    ),
]

N_JOBS_PATTERN = re.compile(r"n_jobs\s*=\s*(-?\d+)")
ALLOWED_TEMPLATE_ATTRS = {"random_weighted", "genetic", "analyze_random_weighted"}
TEMPLATE_PATTERN = re.compile(r"openforage\.templates\.([A-Za-z_][A-Za-z0-9_]*)")
ALGORITHM_PATTERN = re.compile(r"--algorithm\s+([A-Za-z0-9_-]+)")
ALLOWED_ALGORITHM_CHOICES = {"random_weighted", "genetic"}

GA_MISLABEL_PATTERNS = [
    re.compile(r"random_weighted[^\n]{0,60}\bis (?:the )?GA\b", re.IGNORECASE),
    re.compile(r"random_weighted[^\n]{0,60}GA (?:baseline|algorithm|implementation)", re.IGNORECASE),
    re.compile(r"\b(?:is|as) the GA (?:baseline|algorithm|implementation)[^\n]{0,40}random_weighted", re.IGNORECASE),
]
GA_UNMEASURED_PATTERNS = [
    re.compile(r"\b(?:GA|genetic)\b[^\n]{0,40}\b(?:outperforms|beats|proves superior|is superior|verified benchmark|benchmark completed)\b", re.IGNORECASE),
]
ALGORITHM_STOPWORDS = {"on", "in", "for", "to", "is", "accepts", "and", "or", "please", "does", "may"}


def check_truthfulness(report: Report, root: Path) -> None:
    scan_targets = []
    for relpath in (
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
    ):
        path = root / relpath
        if path.is_file():
            scan_targets.append(path)
    for directory in ("docs", "src", "examples"):
        base = root / directory
        if base.is_dir():
            scan_targets.extend(p for p in iter_files(base, {".md", ".json", ".jsonl"}))

    validation_entrypoints.check_algorithm_exclusivity(
        report,
        {rel(root, path): read_text(path) for path in scan_targets},
    )

    for path in scan_targets:
        text = read_text(path)
        relpath = rel(root, path)

        for needle, why in UNSUPPORTED_API_PATTERNS:
            if needle in text:
                report.fail(f"{relpath}: unsupported API reference {needle!r}: {why}")

        for match in N_JOBS_PATTERN.finditer(text):
            if match.group(1) != "1":
                report.fail(
                    f"{relpath}: n_jobs={match.group(1)} found; closed-beta canonical "
                    "examples must keep n_jobs=1 (random_weighted comparison/fork "
                    "examples included)"
                )

        for match in TEMPLATE_PATTERN.finditer(text):
            if match.group(1) in ALLOWED_TEMPLATE_ATTRS:
                continue
            report.fail(
                f"{relpath}: openforage.templates.{match.group(1)} is not an observed template; "
                "0.2.36 exposes random_weighted, genetic, and analyze_random_weighted; "
                "other names need a documented contract and a Python API fork"
            )

        for match in ALGORITHM_PATTERN.finditer(text):
            value = match.group(1)
            remainder = text[match.end():match.end() + 24].strip().split(" ")[0].strip(".,;:()")
            if value in ALGORITHM_STOPWORDS or remainder in ALGORITHM_STOPWORDS:
                continue
            if value not in ALLOWED_ALGORITHM_CHOICES:
                report.fail(
                    f"{relpath}: --algorithm {value} is not an observed CLI choice; "
                    "0.2.36 accepts random_weighted and genetic only"
                )

        for pattern in GA_MISLABEL_PATTERNS:
            match = pattern.search(text)
            if match:
                report.fail(
                    f"{relpath}: random_weighted must not be labeled as GA; the genetic "
                    f"template is installed but unbenchmarked in this packet: {match.group(0)!r}"
                )
                break

        for pattern in GA_UNMEASURED_PATTERNS:
            match = pattern.search(text)
            if match:
                report.fail(
                    f"{relpath}: no full GA benchmark has run in this packet; remove the "
                    f"unmeasured genetic/GA result claim: {match.group(0)!r}"
                )
                break

    for relpath in CANONICAL_ONBOARDING:
        path = root / relpath
        if not path.is_file():
            report.fail(f"canonical onboarding page missing: {relpath}")
            continue
        text = read_text(path)
        if "invite_code=" not in text:
            report.fail(
                f"{relpath}: canonical onboarding no longer shows "
                "register(invite_code=...); a stale bare-register default is not allowed"
            )
        if "n_jobs=1" not in text:
            report.fail(
                f"{relpath}: canonical onboarding must pin n_jobs=1 for the closed beta"
            )
        if re.search(r"openforage\.register\(\s*\)", text):
            report.fail(
                f"{relpath}: bare openforage.register() is a stale default; use "
                'register(invite_code="<CODE>") or the documented environment path'
            )


# ---------------------------------------------------------------------------
# guidance
# ---------------------------------------------------------------------------

GUIDANCE_TARGETS = {
    "AGENTS.md": (
        "README.md",
        "docs/agents/start-here.md",
        "docs/agents/first-contribution.md",
        "docs/agents/research-campaign.md",
        "docs/agents/references/campaign-state-machine.json",
        "docs/agents/research-security.md",
        "docs/agents/background-agent.md",
        "docs/agents/harness-verification.md",
        "CONTRIBUTING.md",
        "harnesses.json",
    ),
    "CLAUDE.md": (
        "AGENTS.md",
        "README.md",
        "docs/agents/start-here.md",
        "docs/agents/first-contribution.md",
        "docs/agents/harness-verification.md",
    ),
}

REQUIRED_GUIDANCE_FILES = (
    "CONTRIBUTING.md",
    "docs/agents/contributing-research.md",
    "docs/agents/first-contribution.md",
    "docs/agents/start-here.md",
    "docs/agents/references/onboarding.json",
    "docs/agents/research-security.md",
    "docs/agents/references/campaign-state-machine.json",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/algorithm-research.yml",
    ".github/ISSUE_TEMPLATE/harness-adapter.yml",
    ".github/ISSUE_TEMPLATE/benchmark-reproducibility.yml",
    ".github/ISSUE_TEMPLATE/bug-report.yml",
    ".github/workflows/security.yml",
    ".github/workflows/agent-guidance.yml",
)


def check_guidance(report: Report, root: Path) -> None:
    for relpath, needles in GUIDANCE_TARGETS.items():
        path = root / relpath
        if not path.is_file():
            report.fail(f"routing file missing: {relpath}")
            continue
        text = read_text(path)
        for needle in needles:
            if needle not in text:
                report.fail(f"{relpath}: missing route to {needle!r}")

    for relpath in REQUIRED_GUIDANCE_FILES:
        if not (root / relpath).is_file():
            report.fail(f"required guidance file missing: {relpath}")

    pr_template = root / ".github/PULL_REQUEST_TEMPLATE.md"
    if pr_template.is_file():
        text = read_text(pr_template)
        for needle in (
            "Truthfulness checklist",
            "depth",
            "wallet.key",
            "validate_agent_docs.py",
            "campaign-state-machine",
        ):
            if needle not in text:
                report.fail(f"PULL_REQUEST_TEMPLATE.md: missing checklist item {needle!r}")

    issue_dir = root / ".github/ISSUE_TEMPLATE"
    if issue_dir.is_dir():
        for path in sorted(issue_dir.glob("*.yml")):
            data = load_yaml(report, path)
            if data is None:
                continue
            relpath = rel(root, path)
            if path.name == "config.yml":
                if not isinstance(data, dict) or "blank_issues_enabled" not in data:
                    report.fail(f"{relpath}: config.yml must set blank_issues_enabled")
                continue
            for key in ("name", "description", "body"):
                if key not in data:
                    report.fail(f"{relpath}: issue form missing top-level {key!r}")
            body = data.get("body")
            if not isinstance(body, list) or not body:
                report.fail(f"{relpath}: issue form body must be a non-empty list")
                continue
            for index, element in enumerate(body):
                where = f"{relpath}: body[{index}]"
                if not isinstance(element, dict) or "type" not in element:
                    report.fail(f"{where}: element must be an object with a type")
                    continue
                attributes = element.get("attributes")
                if not isinstance(attributes, dict) or not (
                    "label" in attributes or "value" in attributes
                ):
                    report.fail(f"{where}: element must have attributes.label or attributes.value")
                if element.get("type") == "checkboxes":
                    options = (attributes or {}).get("options")
                    if not isinstance(options, list) or not options:
                        report.fail(f"{where}: checkboxes must nest non-empty attributes.options")

    workflows_dir = root / ".github/workflows"
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.yml")):
            data = load_yaml(report, path)
            if not isinstance(data, dict) or "jobs" not in data:
                report.fail(f"{rel(root, path)}: workflow must define jobs")


def check_guidance_all(report: Report, root: Path) -> None:
    check_guidance(report, root)
    validation_guidance.check_onboarding(report, root)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

MODES = {
    "secrets": lambda report, root, args: check_secrets(report, root),
    "truthfulness": lambda report, root, args: check_truthfulness(report, root),
    "registry": lambda report, root, args: validation_registry.check_registry(report, root),
    "links": lambda report, root, args: validation_anchors.check_links(report, root),
    "examples": lambda report, root, args: validation_campaign.check_examples(report, root),
    "records": lambda report, root, args: validation_campaign.check_records(
        report, root, args.record_dir
    ),
    "state-machine": lambda report, root, args: validation_campaign.check_state_machine_definition(
        report, root
    ),
    "controls": lambda report, root, args: validation_controls.check_controls(report, root),
    "guidance": lambda report, root, args: check_guidance_all(report, root),
    "first-read": lambda report, root, args: validation_first_read.check_first_read(report, root),
    "evals": lambda report, root, args: validation_evals.check_evals(report, root),
    "opportunities": lambda report, root, args: validation_opportunities.check_opportunities(
        report, root
    ),
    "compatibility": lambda report, root, args: validation_compatibility.check_compatibility(
        report, root
    ),
    "executables": lambda report, root, args: validation_executables.check_executables(report, root),
    "selftest": lambda report, root, args: validation_selftest.run_selftests(report, root),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "modes",
        nargs="+",
        choices=sorted(MODES) + ["all"],
        help="one or more modes, or 'all'",
    )
    parser.add_argument("--root", default=str(REPO_DEFAULT), help="repository root to validate")
    parser.add_argument(
        "--record-dir",
        default=None,
        help="campaign record directory relative to --root (default: examples/agent-campaign)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    modes = sorted(MODES) if "all" in args.modes else args.modes
    report = Report()
    for mode in modes:
        print(f"== {mode} ==")
        MODES[mode](report, root, args)
    return report.dump()


if __name__ == "__main__":
    sys.exit(main())
