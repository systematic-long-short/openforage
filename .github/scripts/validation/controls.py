"""Guardrail scan for excluded spend/token controls in the design surfaces.

The public design must not add financial, monetary, model-token, or spend
controls, fields, approval rules, or examples. Authentication-token safety is
unrelated secrecy and must not be flagged: `wallet.key`, invite codes, JWTs,
bearer tokens, and credential handling stay allowed.

The scan is silent about its own rationale in the public prose and covers the
design surfaces only. Protocol earnings documentation outside the design
(e.g. ``docs/agents/compensation.md``) is explicitly exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

from .common import iter_files, read_text, rel

SCAN_ROOTS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "docs/agents",
    "src/openforage/skills",
    "examples",
    ".github",
)

EXEMPT_FILES = {
    "docs/agents/compensation.md",
}

EXEMPT_PREFIXES = (
    ".github/scripts/",
)

SUFFIXES = {".md", ".json", ".jsonl", ".yml", ".yaml"}

BANNED_PATTERNS: list[tuple[str, str]] = [
    ("dollar-amount", r"(?:\bUSD\s*\d|\$\s?\d[\d,.]*)"),
    (
        "spend-control",
        r"\b(?:max(?:imum)?[ _-]?(?:spend|cost|payment|fee|usd|dollar)s?"
        r"|spend(?:ing)?[ _-]?(?:limit|cap|ceiling|budget|approval|control|threshold)s?"
        r"|cost[ _-]?(?:limit|cap|ceiling|budget|approval|control)s?"
        r"|fee[ _-]?(?:limit|cap|ceiling|budget|approval)s?"
        r"|payment[ _-]?(?:limit|cap|ceiling|budget|approval|control)s?)\b",
    ),
    (
        "token-spend-control",
        r"\b(?:model[ _-]?token|token)[ _-]?(?:budget|limit|cap|ceiling|allowance|spend|quota)s?\b",
    ),
    (
        "budget-control-suffix",
        r"\b(?:budget|limit|cap|ceiling)[ _-]?(?:usd|dollar|spend|cost|payment|fee)s?\b",
    ),
    (
        "financial-approval",
        r"\bfinancial[ _-]?(?:approval|authorization|control|limit|threshold)s?\b",
    ),
    ("spend-approval", r"\b(?:spend(?:ing)?|payment)[ _-]?approval\b"),
    (
        "control-identifier",
        r"\b[a-z0-9_]*(?:spend|cost|fee|payment|dollar|usd)[ _-]?"
        r"(?:limit|cap|budget|ceiling|allowance|quota|approval|control)[a-z0-9_]*\b"
        r"|\b[a-z0-9_]*(?:limit|cap|budget|ceiling|allowance|quota)[ _-]?"
        r"(?:usd|dollar|spend|cost|fee|payment|token)[a-z0-9_]*\b"
        r"|\bmax[ _-]?(?:spend|usd|dollar|cost|fee|payment|token)[a-z0-9_]*\b",
    ),
]

AUTH_SAFETY_SAMPLES = (
    "Never paste the JWT or wallet.key; keep the bearer token in the runtime secret store.",
    "Registration state belongs under your --data-dir with mode 0600; never commit invite codes.",
    "Callback events must not include wallet.key, invite codes, JWTs, or bearer tokens.",
    "Use campaign-scoped credentials and keep them outside artifacts and exports.",
)


def _scan_targets(root: Path):
    for name in SCAN_ROOTS:
        target = root / name
        if target.is_file():
            yield target
        elif target.is_dir():
            for path in iter_files(target, SUFFIXES):
                relpath = rel(root, path)
                if relpath in EXEMPT_FILES:
                    continue
                if relpath.startswith(EXEMPT_PREFIXES):
                    continue
                yield path


def _pattern_hits(text: str):
    for name, pattern in BANNED_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            yield name, match.group(0)


def check_controls(report, root: Path) -> None:
    for path in _scan_targets(root):
        relpath = rel(root, path)
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, sample in _pattern_hits(line):
                report.fail(f"{relpath}:{line_no}: excluded control field ({name}): {sample!r}")

    for sample in AUTH_SAFETY_SAMPLES:
        for name, _ in _pattern_hits(sample):
            report.fail(
                "authentication-token safety phrase was wrongly flagged by "
                f"{name}: {sample!r}"
            )
