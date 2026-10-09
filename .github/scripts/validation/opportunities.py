"""Research opportunity map schema and honesty checks."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .common import is_calendar_date, is_real_string, read_text

MAP_REL = "docs/agents/research-opportunities.json"
GUIDE_REL = "docs/agents/research-opportunities.md"
REQUIRED_ENTRY_KEYS = {
    "opportunity_id",
    "algorithm_family",
    "title",
    "question",
    "mechanism",
    "primary_source",
    "required_public_capabilities",
    "baseline_comparison",
    "acceptance_evidence",
    "current_status",
    "evidence_refs",
    "next_action",
}
SUPERIORITY_PATTERNS = (
    re.compile(r"\bproves\b[^.\n]{0,60}\b(superior|better|outperform)", re.IGNORECASE),
    re.compile(r"\b(superior to|outperforms|better than)\b", re.IGNORECASE),
    re.compile(r"\bproves superiority\b", re.IGNORECASE),
)
PRIMARY_SOURCE_PREFIXES = (
    "https://arxiv.org/",
    "https://doi.org/",
    "https://dl.acm.org/",
    "https://proceedings.",
    "https://jmlr.org/",
    "https://openreview.net/",
)


def _superiority_hit(text) -> str | None:
    if not isinstance(text, str):
        return None
    for pattern in SUPERIORITY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def check_opportunities(report, root: Path) -> None:
    path = root / MAP_REL
    guide = root / GUIDE_REL
    if not path.is_file():
        report.fail(f"research map: missing {MAP_REL}")
        return
    if not guide.is_file():
        report.fail(f"research map: missing rendered guide {GUIDE_REL}")
    else:
        guide_text = read_text(guide)
        if "research-opportunities.json" not in guide_text:
            report.fail(f"{GUIDE_REL}: rendered guide must point at the authoritative JSON index")

    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.fail(f"{MAP_REL}: invalid JSON: {exc}")
        return

    statuses = data.get("status_values")
    if not isinstance(statuses, list) or not statuses:
        report.fail(f"{MAP_REL}: status_values must be a non-empty list")
        statuses = []
    negative = data.get("negative_results")
    if not isinstance(negative, list):
        report.fail(f"{MAP_REL}: negative_results must be a list (empty when nothing is observed)")
    else:
        for index, item in enumerate(negative):
            where = f"{MAP_REL}: negative_results[{index}]"
            if not isinstance(item, dict):
                report.fail(f"{where}: must be an object with observed evidence")
                continue
            for key in ("opportunity_id", "observed_result", "evidence_artifact"):
                if not is_real_string(item.get(key)):
                    report.fail(f"{where}: missing real {key!r}; an empty list is honest, an unsupported record is not")

    entries = data.get("opportunities")
    if not isinstance(entries, list) or not entries:
        report.fail(f"{MAP_REL}: opportunities must be a non-empty list")
        return
    families = set()
    ids = set()
    for index, entry in enumerate(entries):
        where = f"{MAP_REL}: opportunities[{index}]"
        if not isinstance(entry, dict):
            report.fail(f"{where}: entry must be an object")
            continue
        missing = REQUIRED_ENTRY_KEYS - set(entry)
        if missing:
            report.fail(f"{where}: missing keys {sorted(missing)}")
            continue
        opportunity_id = entry["opportunity_id"]
        if not is_real_string(opportunity_id):
            report.fail(f"{where}: opportunity_id must be a real string")
        if opportunity_id in ids:
            report.fail(f"{where}: duplicate opportunity_id {opportunity_id!r}")
        ids.add(opportunity_id)
        families.add(entry.get("algorithm_family"))

        source = entry["primary_source"]
        if not is_real_string(source) or not source.startswith(PRIMARY_SOURCE_PREFIXES):
            report.fail(
                f"{where}: primary_source must be a primary-source https URL "
                f"(arxiv/doi/proceedings/jmlr/openreview), found {source!r}"
            )
        if entry["current_status"] not in statuses:
            report.fail(f"{where}: current_status {entry['current_status']!r} not in status_values")
        for key in ("required_public_capabilities", "evidence_refs"):
            value = entry.get(key)
            if not isinstance(value, list) or not value:
                report.fail(f"{where}: {key} must be a non-empty list")
        if not is_real_string(entry.get("acceptance_evidence")):
            report.fail(f"{where}: acceptance_evidence must state observable acceptance criteria")
        for key in ("title", "question", "mechanism", "baseline_comparison", "next_action"):
            hit = _superiority_hit(entry.get(key))
            if hit:
                report.fail(f"{where}.{key}: unmeasured superiority language {hit!r}; cite the source as a starting point instead")

    if len(families) < 3:
        report.fail(
            f"{MAP_REL}: opportunity families must be complementary; found only {sorted(f for f in families if f)}"
        )
    updated = data.get("updated")
    if not is_calendar_date(updated):
        report.fail(f"{MAP_REL}: updated must be a real calendar date (YYYY-MM-DD)")
