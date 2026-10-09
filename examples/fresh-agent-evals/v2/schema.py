"""Strict, dependency-free checks for the disclosed v2 completion schema."""

from __future__ import annotations

import json
from pathlib import Path

CASE_IDS = {
    "first-finished-contribution-primary",
    "first-finished-contribution-heldout",
}
TASK_IDS = {"research", "contribute", "maintenance", "diagnose", "adapter", "campaign"}
SOURCE_IDS = {
    "README.md", "AGENTS.md", "CLAUDE.md", "docs/agents/first-contribution.md",
    "docs/agents/start-here.md", "docs/agents/references/onboarding.json",
}
VALIDATOR_MODES = {
    "secrets", "truthfulness", "registry", "links", "examples", "records",
    "state-machine", "controls", "guidance", "first-read", "evals",
    "opportunities", "compatibility", "executables", "selftest", "all",
}
ENUMS = {
    "offline_contribution_access": {"not_required", "required", "unknown"},
    "live_campaign_authorization": {
        "explicit_user_authorization_required", "not_required", "unknown"
    },
    "contribution_kind": {"documentation_correction", "documentation_addition", "other"},
    "validation_result": {"passed", "failed", "unknown"},
}


def loads_strict(text: str):
    """Reject duplicate object keys instead of silently accepting the last."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_pairs)


def read_json(path: Path):
    return loads_strict(path.read_text(encoding="utf-8"))


def _object(value, required: set[str], where: str, problems: list[str]) -> bool:
    if not isinstance(value, dict):
        problems.append(f"{where} must be an object")
        return False
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        problems.append(f"{where} missing keys: {sorted(missing)}")
    if extra:
        problems.append(f"{where} has additional keys: {sorted(extra)}")
    return not missing and not extra


def _enum(value, options: set[str], where: str, problems: list[str]) -> None:
    if not isinstance(value, str) or value not in options:
        problems.append(f"{where} must be one of {sorted(options)}")


def _unique_strings(value, where: str, problems: list[str], options=None) -> None:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) for x in value):
        problems.append(f"{where} must be a non-empty string array")
        return
    if len(set(value)) != len(value):
        problems.append(f"{where} must not contain duplicates")
    if options is not None and any(item not in options for item in value):
        problems.append(f"{where} contains an unsupported identifier")


def validate_completion(data, case_id: str) -> list[str]:
    """Validate JSON types/enums; narrative fields are never quality-graded."""
    problems: list[str] = []
    if not _object(data, {"schema_version", "case_id", "route", "contribution", "validation", "human_review"}, "completion", problems):
        return problems
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        problems.append("schema_version must be integer 2")
    if data.get("case_id") != case_id or case_id not in CASE_IDS:
        problems.append("case_id does not match the selected case")

    route = data.get("route")
    if _object(route, {"selected_task", "offline_contribution_access", "live_campaign_authorization", "authoritative_sources"}, "route", problems):
        _enum(route["selected_task"], TASK_IDS, "route.selected_task", problems)
        _enum(route["offline_contribution_access"], ENUMS["offline_contribution_access"], "route.offline_contribution_access", problems)
        _enum(route["live_campaign_authorization"], ENUMS["live_campaign_authorization"], "route.live_campaign_authorization", problems)
        _unique_strings(route["authoritative_sources"], "route.authoritative_sources", problems, SOURCE_IDS)

    contribution = data.get("contribution")
    if _object(contribution, {"kind", "changed_paths"}, "contribution", problems):
        _enum(contribution["kind"], ENUMS["contribution_kind"], "contribution.kind", problems)
        paths = contribution["changed_paths"]
        _unique_strings(paths, "contribution.changed_paths", problems)
        if isinstance(paths, list) and all(isinstance(item, str) for item in paths):
            for item in paths:
                if item.startswith("/") or "\\" in item or any(part in {"", ".", ".."} for part in item.split("/")):
                    problems.append("contribution.changed_paths must contain safe workspace-relative paths")

    validation = data.get("validation")
    if _object(validation, {"baseline", "final"}, "validation", problems):
        for label in ("baseline", "final"):
            item = validation[label]
            if not _object(item, {"command", "modes", "exit_code", "result"}, f"validation.{label}", problems):
                continue
            if not isinstance(item["command"], str) or not item["command"].strip():
                problems.append(f"validation.{label}.command must be a non-empty string")
            _unique_strings(item["modes"], f"validation.{label}.modes", problems, VALIDATOR_MODES)
            if type(item["exit_code"]) is not int:
                problems.append(f"validation.{label}.exit_code must be an integer, not a boolean")
            _enum(item["result"], ENUMS["validation_result"], f"validation.{label}.result", problems)

    review = data.get("human_review")
    if _object(review, {"summary", "rationale"}, "human_review", problems):
        for key in ("summary", "rationale"):
            if not isinstance(review[key], str) or not review[key].strip():
                problems.append(f"human_review.{key} must be a non-empty string")
    return problems
