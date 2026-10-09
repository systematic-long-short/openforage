"""Pure, deterministic selection logic for :mod:`research_next`.

All user-supplied files are read as evidence. This module never writes them,
starts a process, contacts a service, or treats unknown values as zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

OPPORTUNITIES_REL = "docs/agents/research-opportunities.json"
STATE_MACHINE_REL = "docs/agents/references/campaign-state-machine.json"
CAMPAIGN_TEMPLATE_REL = "examples/agent-campaign/campaign.json"
TARGET_COMPLETIONS = 100_000
TERMINAL_STATES = {"exhausted", "stopped", "completed"}
PLACEHOLDER_RE = re.compile(r"<[^>]*>")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$"
)


class NextActionError(ValueError):
    """An input file is unreadable or violates the helper's record contract."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NextActionError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _parse_json(text: str, label: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, NextActionError) as exc:
        raise NextActionError(f"{label}: invalid JSON ({exc})") from exc


def _read_bytes(path: Path, label: str) -> tuple[bytes, str]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise NextActionError(f"{label}: cannot read input ({type(exc).__name__})") from exc
    return content, hashlib.sha256(content).hexdigest()


def _read_json(path: Path, label: str) -> tuple[Any, str]:
    content, digest = _read_bytes(path, label)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NextActionError(f"{label}: input is not UTF-8 text") from exc
    return _parse_json(text, label), digest


def _read_jsonl(path: Path, label: str) -> tuple[list[dict[str, Any]], str]:
    content, digest = _read_bytes(path, label)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NextActionError(f"{label}: input is not UTF-8 text") from exc
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        value = _parse_json(line, f"{label} line {line_no}")
        if not isinstance(value, dict):
            raise NextActionError(f"{label} line {line_no}: expected a JSON object")
        rows.append(value)
    return rows, digest


def _real_string(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if PLACEHOLDER_RE.search(text):
        return False
    return text.casefold() not in {"unknown", "missing", "not_recorded", "n/a", "null", "unavailable"}


def _actual_int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _actual_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _is_iso(value: Any) -> bool:
    if not isinstance(value, str) or not ISO_RE.fullmatch(value.strip()):
        return False
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_authorities(root: Path) -> tuple[dict, dict, dict, dict[str, str]]:
    opportunity_data, opportunity_digest = _read_json(root / OPPORTUNITIES_REL, "research opportunity map")
    state_data, state_digest = _read_json(root / STATE_MACHINE_REL, "campaign state machine")
    template, template_digest = _read_json(root / CAMPAIGN_TEMPLATE_REL, "campaign template")
    if not isinstance(opportunity_data, dict) or not isinstance(opportunity_data.get("opportunities"), list):
        raise NextActionError(f"{OPPORTUNITIES_REL}: expected an object with an opportunities array")
    if not isinstance(opportunity_data.get("negative_results", []), list):
        raise NextActionError(f"{OPPORTUNITIES_REL}: negative_results must be an array")
    if not isinstance(state_data, dict) or not isinstance(state_data.get("states"), list):
        raise NextActionError(f"{STATE_MACHINE_REL}: expected an object with a states array")
    if not isinstance(template, dict) or not isinstance(template.get("frozen_baseline"), dict):
        raise NextActionError(f"{CAMPAIGN_TEMPLATE_REL}: missing frozen_baseline target")
    target = template["frozen_baseline"].get("target_completed_simulations")
    if type(target) is not int or target != TARGET_COMPLETIONS:
        raise NextActionError(f"{CAMPAIGN_TEMPLATE_REL}: target must be {TARGET_COMPLETIONS} completed simulations")

    identifiers: set[str] = set()
    for index, opportunity in enumerate(opportunity_data["opportunities"]):
        if not isinstance(opportunity, dict):
            raise NextActionError(f"{OPPORTUNITIES_REL}: opportunities[{index}] must be an object")
        for key in ("opportunity_id", "title", "question", "primary_source", "current_status"):
            if not _real_string(opportunity.get(key)):
                raise NextActionError(f"{OPPORTUNITIES_REL}: opportunities[{index}].{key} must be a real string")
        identifier = opportunity["opportunity_id"]
        if identifier in identifiers:
            raise NextActionError(f"{OPPORTUNITIES_REL}: duplicate opportunity_id {identifier!r}")
        identifiers.add(identifier)

    states: set[str] = set()
    terminal: set[str] = set()
    for item in state_data["states"]:
        if not isinstance(item, dict) or not _real_string(item.get("id")):
            raise NextActionError(f"{STATE_MACHINE_REL}: every state needs a real id")
        state_id = item["id"]
        if state_id in states or type(item.get("terminal")) is not bool:
            raise NextActionError(f"{STATE_MACHINE_REL}: duplicate state or invalid terminal flag for {state_id!r}")
        states.add(state_id)
        if item["terminal"]:
            terminal.add(state_id)
    resume_targets = state_data.get("transitions", [])
    resume_edge = next(
        (edge for edge in resume_targets if isinstance(edge, dict) and edge.get("id") == "T-resume"), None
    ) if isinstance(resume_targets, list) else None
    allowed_resume = set(resume_edge.get("resolves_to", [])) if isinstance(resume_edge, dict) else set()
    legal_edges: set[tuple[str, str]] = set()
    for edge in resume_targets if isinstance(resume_targets, list) else []:
        if not isinstance(edge, dict) or not isinstance(edge.get("to"), str):
            continue
        sources = edge.get("from")
        sources = sources if isinstance(sources, list) else [sources]
        destinations = edge.get("resolves_to", []) if edge.get("to") == "resume_target" else [edge["to"]]
        for source in sources:
            for destination in destinations if isinstance(destinations, list) else []:
                if isinstance(source, str) and source in states and isinstance(destination, str) and destination in states:
                    legal_edges.add((source, destination))
    authorities = {
        "opportunities": OPPORTUNITIES_REL,
        "state_machine": STATE_MACHINE_REL,
        "campaign_template": CAMPAIGN_TEMPLATE_REL,
    }
    digests = {
        "opportunities_map": opportunity_digest,
        "state_machine": state_digest,
        "campaign_template": template_digest,
    }
    state_data["_helper_valid_states"] = states
    state_data["_helper_terminal_states"] = terminal
    state_data["_helper_resume_targets"] = allowed_resume
    state_data["_helper_legal_edges"] = legal_edges
    state_data["_helper_source_refs"] = authorities
    return opportunity_data, state_data, template, digests


def _validate_campaign(data: Any, state_data: dict) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise NextActionError("--campaign: expected a JSON object")
    if "_template" in data and type(data["_template"]) is not bool:
        raise NextActionError("--campaign: _template must be a JSON boolean when present")
    if data.get("_template") is True:
        return {"kind": "template", "data": data, "state": None, "scope": None}

    required_objects = ("state_machine", "frozen_baseline", "compatible_baseline", "preregistration", "pipeline_stages", "provenance", "resources")
    for key in ("campaign_id", "transitions", *required_objects):
        if key not in data:
            raise NextActionError(f"--campaign: real manifest is missing required field {key!r}")
    if not _real_string(data.get("campaign_id")):
        raise NextActionError("--campaign: real manifest needs a non-placeholder campaign_id")
    if not isinstance(data.get("transitions"), list):
        raise NextActionError("--campaign: transitions must be an array")
    for key in required_objects:
        if not isinstance(data.get(key), dict):
            raise NextActionError(f"--campaign: {key} must be an object")

    machine = data["state_machine"]
    state = machine.get("state")
    if not isinstance(state, str) or state not in state_data["_helper_valid_states"]:
        raise NextActionError("--campaign: state_machine.state must be a state in the canonical state machine")
    resume_target = machine.get("resume_target")
    if state in {"paused", "needs-input"} and (
        not isinstance(resume_target, str) or resume_target not in state_data["_helper_resume_targets"]
    ):
        raise NextActionError(f"--campaign: {state} requires a canonical resume_target")
    if not _actual_int(machine.get("revision"), 0) or not isinstance(machine.get("ownership"), dict):
        raise NextActionError("--campaign: state_machine needs a non-negative revision and ownership object")
    owner = machine["ownership"]
    if not _actual_int(owner.get("owner_revision"), 0):
        raise NextActionError("--campaign: state_machine.ownership.owner_revision must be a non-negative integer")
    if owner.get("claim_verified") is not None and type(owner.get("claim_verified")) is not bool:
        raise NextActionError("--campaign: state_machine.ownership.claim_verified must be boolean or null")
    if owner.get("fencing_token") is not None and type(owner.get("fencing_token")) not in (str, int):
        raise NextActionError("--campaign: state_machine.ownership.fencing_token must be string, integer, or null")
    if not all(isinstance(owner.get(key), str) for key in ("owner_session_identity", "claim_mechanism", "claimed_at")):
        raise NextActionError("--campaign: state_machine.ownership identity, claim mechanism, and claimed_at must be strings")
    if any(not isinstance(item, dict) for item in data["transitions"]):
        raise NextActionError("--campaign: every transition must be an object")
    initial_state = state_data.get("initial_state")
    if state == initial_state:
        if data["transitions"]:
            raise NextActionError("--campaign: the initial state must have empty transition history")
    else:
        last = data["transitions"][-1] if data["transitions"] else None
        if not isinstance(last, dict):
            raise NextActionError("--campaign: non-initial state requires its append-only transition record")
        source = last.get("from")
        if (
            not _real_string(last.get("transition_id"))
            or not isinstance(source, str)
            or last.get("to") != state
            or (source, state) not in state_data["_helper_legal_edges"]
            or not _is_iso(last.get("timestamp"))
            or not _real_string(last.get("reason"))
            or not _real_string(last.get("owner_session_identity"))
            or last.get("guard_passed") is not True
            or not _actual_int(last.get("owner_revision"), 1)
            or last.get("owner_revision") != machine.get("revision")
            or last.get("owner_session_identity") != owner.get("owner_session_identity")
            or owner.get("owner_revision") != machine.get("revision")
        ):
            raise NextActionError("--campaign: latest transition must match the current state, canonical edge, guard, owner, and revision")

    frozen = data["frozen_baseline"]
    for key in ("availability", "id", "hash", "observed_completed_simulations", "frozen", "algorithm_identity"):
        if key not in frozen:
            raise NextActionError(f"--campaign: frozen_baseline is missing required field {key!r}")
    availability = frozen.get("availability")
    if not isinstance(availability, str) or availability not in {"available", "unavailable", "pending", "unknown"}:
        raise NextActionError("--campaign: frozen_baseline.availability must be available/unavailable/pending/unknown")
    if frozen.get("id") is not None and not isinstance(frozen.get("id"), str):
        raise NextActionError("--campaign: frozen_baseline.id must be a string or null")
    if frozen.get("hash") is not None and not isinstance(frozen.get("hash"), str):
        raise NextActionError("--campaign: frozen_baseline.hash must be a string or null")
    if not isinstance(frozen.get("algorithm_identity"), (str, dict)):
        raise NextActionError("--campaign: frozen_baseline.algorithm_identity must be a string or object")
    target = frozen.get("target_completed_simulations")
    if type(target) is not int or target != TARGET_COMPLETIONS:
        raise NextActionError(f"--campaign: frozen_baseline.target_completed_simulations must be {TARGET_COMPLETIONS}")
    observed = frozen.get("observed_completed_simulations")
    if observed is not None and not _actual_int(observed):
        raise NextActionError("--campaign: observed_completed_simulations must be null or a non-negative integer; bool is not a count")
    if frozen.get("frozen") is not None and type(frozen.get("frozen")) is not bool:
        raise NextActionError("--campaign: frozen_baseline.frozen must be boolean or null")

    compatible = data["compatible_baseline"]
    for key in ("status", "baseline_id", "lineage", "config_frozen_before_run", "evidence_frozen_after_run"):
        if key not in compatible:
            raise NextActionError(f"--campaign: compatible_baseline is missing required field {key!r}")
    compatible_status = compatible.get("status")
    if not isinstance(compatible_status, str) or compatible_status not in {"found", "created", "incomplete", "missing"}:
        raise NextActionError("--campaign: compatible_baseline.status must be found/created/incomplete/missing")
    if compatible.get("baseline_id") is not None and not isinstance(compatible.get("baseline_id"), str):
        raise NextActionError("--campaign: compatible_baseline.baseline_id must be a string or null")
    if not isinstance(compatible.get("lineage"), list):
        raise NextActionError("--campaign: compatible_baseline.lineage must be an array")
    for key in ("config_frozen_before_run", "evidence_frozen_after_run"):
        if compatible.get(key) is not None and type(compatible.get(key)) is not bool:
            raise NextActionError(f"--campaign: compatible_baseline.{key} must be boolean or null")
    for parent, key in ((data["preregistration"], "data_scope"), (data["provenance"], "data_scope")):
        if not isinstance(parent.get(key), dict):
            raise NextActionError(f"--campaign: {key} must be an object in a real manifest")

    return {
        "kind": "real", "data": data, "state": state,
        "scope": _campaign_scope(data),
        "terminal_states": state_data["_helper_terminal_states"],
        "legal_edges": state_data["_helper_legal_edges"],
    }


def _campaign_scope(data: dict[str, Any]) -> dict[str, Any] | None:
    records = []
    prereg = data.get("preregistration")
    provenance = data.get("provenance")
    if isinstance(prereg, dict) and isinstance(prereg.get("data_scope"), dict):
        records.append(prereg["data_scope"])
    if isinstance(provenance, dict) and isinstance(provenance.get("data_scope"), dict):
        records.append(provenance["data_scope"])
    if not records:
        return None

    result: dict[str, Any] = {}
    for key in ("era_id", "config_hash", "loaded_vocabulary_hash"):
        observed = {record[key].strip() for record in records if _real_string(record.get(key))}
        if len(observed) > 1:
            return {"conflict": True, "field": key}
        result[key] = next(iter(observed)) if observed else None
    if not _real_string(result.get("era_id")) or not _real_string(result.get("config_hash")):
        return None
    result["conflict"] = False
    return result


def _validate_context(data: Any, opportunity_ids: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise NextActionError("--context: expected a JSON object")
    if "_template" in data and type(data["_template"]) is not bool:
        raise NextActionError("--context: _template must be a JSON boolean when present")
    if data.get("_template") is True:
        return {"kind": "template", "data": data, "progress": {}}
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise NextActionError("--context: schema_version must be integer 1")

    progress = data.get("research_progress", {})
    if not isinstance(progress, dict):
        raise NextActionError("--context: research_progress must be an object")
    for key in ("investigated_opportunity_ids", "negative_approach_ids", "analyzed_experiment_ids"):
        values = progress.get(key, [])
        if not isinstance(values, list) or any(not _real_string(value) for value in values):
            raise NextActionError(f"--context: research_progress.{key} must be an array of real strings")
        if key != "analyzed_experiment_ids" and any(value not in opportunity_ids for value in values):
            raise NextActionError(f"--context: research_progress.{key} contains an unknown opportunity_id")
    active = progress.get("active_opportunity_id")
    if active is not None and (not _real_string(active) or active not in opportunity_ids):
        raise NextActionError("--context: active_opportunity_id must be null or a known opportunity_id")
    pending_ablation = progress.get("pending_ablation")
    if pending_ablation is not None:
        if not isinstance(pending_ablation, dict) or any(
            not _real_string(pending_ablation.get(key)) for key in ("opportunity_id", "basis_ref", "component")
        ):
            raise NextActionError("--context: pending_ablation needs opportunity_id, basis_ref, and component")
        if pending_ablation["opportunity_id"] not in opportunity_ids:
            raise NextActionError("--context: pending_ablation names an unknown opportunity_id")
    return {"kind": "real", "data": data, "progress": progress}


def _live_gate(
    context: dict[str, Any], scope: dict[str, Any] | None, campaign: dict[str, Any] | None = None
) -> tuple[bool, list[str]]:
    data = context.get("data", {}) if context.get("kind") == "real" else {}
    authorization = data.get("authorization") if isinstance(data, dict) else None
    observations = data.get("observations") if isinstance(data, dict) else None
    scope_observation = data.get("scope_observation") if isinstance(data, dict) else None
    missing: list[str] = []
    if not isinstance(authorization, dict) or authorization.get("explicit_live_authorization") is not True:
        missing.append("explicit user authorization for this live campaign")
    if not isinstance(authorization, dict) or not _real_string(authorization.get("evidence_ref")):
        missing.append("non-secret authorization evidence reference")
    required_observations = (
        "live_access_available",
        "package_installed",
        "genetic_callable_observed",
        "separate_same_harness_cli_observed",
        "distinct_session_identity_observed",
    )
    if not isinstance(observations, dict):
        missing.extend(f"observed {key.replace('_', ' ')}" for key in required_observations)
        missing.append("source and timestamp for live-prerequisite observations")
    else:
        for key in required_observations:
            if observations.get(key) is not True:
                missing.append(f"observed {key.replace('_', ' ')}")
        if not _real_string(observations.get("source_ref")) or not _is_iso(observations.get("observed_at")):
            missing.append("source and timestamp for live-prerequisite observations")
    if scope is None or scope.get("conflict"):
        missing.append("one consistent observed campaign era/config scope")
    if isinstance(scope, dict) and not _real_string(scope.get("loaded_vocabulary_hash")):
        missing.append("campaign loaded-vocabulary hash")
    if not isinstance(scope_observation, dict):
        missing.append("matching scope observation with source and timestamp")
    else:
        for key in ("era_id", "config_hash", "loaded_vocabulary_hash"):
            if not _real_string(scope_observation.get(key)) or not isinstance(scope, dict) or scope.get(key) != scope_observation.get(key):
                missing.append(f"scope observation matching {key}")
        if not _real_string(scope_observation.get("source_ref")) or not _is_iso(scope_observation.get("observed_at")):
            missing.append("matching scope observation with source and timestamp")
    if not isinstance(campaign, dict) or campaign.get("kind") != "real":
        missing.append("a real campaign manifest and verified ownership claim")
    else:
        machine = campaign["data"]["state_machine"]
        owner = machine.get("ownership", {})
        if (
            not _real_string(owner.get("owner_session_identity"))
            or owner.get("claim_verified") is not True
            or not _real_string(owner.get("claim_mechanism"))
            or not _is_iso(owner.get("claimed_at"))
            or not _actual_int(owner.get("owner_revision"), 1)
            or owner.get("owner_revision") != machine.get("revision")
            or not (_real_string(owner.get("fencing_token")) or _actual_int(owner.get("fencing_token"), 1))
        ):
            missing.append("manifest ownership identity, verified claim, and fencing evidence")
    return not missing, list(dict.fromkeys(missing))


def _count(value: int | None, status: str, note: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"value": value, "status": status}
    if note:
        result["note"] = note
    return result


def _coverage_analysis(
    rows: list[dict[str, Any]] | None, scope: dict[str, Any] | None
) -> dict[str, Any]:
    unknown = _count(None, "unknown", "No compatible observed coverage count is available.")
    if rows is None:
        return {
            "provided": False,
            "counts": {
                "pair_rows_in_file": _count(None, "unknown", "No coverage file supplied."),
                "matching_scope_rows": unknown,
                "eligible_loaded_valid_attributed_pairs": unknown,
                "known_evaluation_count_pairs": unknown,
                "minimum_known_count_pairs": unknown,
                "unknown_evaluation_count_pairs": unknown,
                "conflicting_pair_keys": _count(None, "unknown", "No coverage file supplied."),
                "duplicate_rows": _count(None, "unknown", "No coverage file supplied."),
                "eligible_pair_universe": _count(None, "unknown", "Ledger completeness and the eligible denominator are not established."),
            },
            "candidates": [],
            "diagnostics": [],
        }

    templates = 0
    pair_rows = 0
    matching_rows = 0
    out_of_scope = 0
    unknown_scope_rows = 0
    duplicate_rows = 0
    invalid_identity_rows = 0
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if "_template" in row and type(row["_template"]) is not bool:
            raise NextActionError("--coverage: _template must be a JSON boolean when present")
        if row.get("_template") is True:
            templates += 1
            continue
        if row.get("record_type") != "feature_rotation_pair":
            continue
        pair_rows += 1
        era = row.get("era_id")
        config = row.get("config_hash")
        feature = row.get("feature")
        rotation = row.get("rotation")
        if not all(_real_string(value) for value in (era, config, feature, rotation)):
            invalid_identity_rows += 1
            continue
        if scope is None or scope.get("conflict") or not _real_string(scope.get("era_id")) or not _real_string(scope.get("config_hash")):
            unknown_scope_rows += 1
            continue
        if era != scope["era_id"] or config != scope["config_hash"]:
            out_of_scope += 1
            continue
        matching_rows += 1
        key = (era, config, feature, rotation)
        grouped.setdefault(key, []).append(row)

    resolved: list[tuple[tuple[str, str, str, str], dict[str, Any]]] = []
    conflicts: list[tuple[str, str]] = []
    for key, copies in grouped.items():
        unique: dict[str, dict[str, Any]] = {}
        for row in copies:
            unique.setdefault(_json_digest(row), row)
        duplicate_rows += len(copies) - len(unique)
        if len(unique) != 1:
            conflicts.append((key[2], key[3]))
            continue
        resolved.append((key, next(iter(unique.values()))))

    eligible: list[dict[str, Any]] = []
    unknown_evaluation = 0
    ineligible_attribution = 0
    qualified_pairs: list[dict[str, Any]] = []
    for key, row in resolved:
        if row.get("feature_set") != "loaded" or row.get("valid_pair") is not True:
            continue
        if (
            row.get("filler_or_transformed_duplicate") is not False
            or "duplicate_of" not in row
            or row.get("duplicate_of") is not None
        ):
            continue
        depth = row.get("depth_cap")
        if not _actual_int(depth, 1) or depth > 6:
            continue
        attribution = row.get("pair_attribution")
        if not isinstance(attribution, str) or attribution not in {"analytics", "candidate_bookkeeping"} or not _real_string(row.get("attribution_provenance")):
            ineligible_attribution += 1
            continue
        qualified = {
            "era_id": key[0],
            "config_hash": key[1],
            "feature": key[2],
            "rotation": key[3],
            "pair_attribution": row["pair_attribution"],
            "attribution_provenance": row["attribution_provenance"],
        }
        qualified_pairs.append(qualified)
        count = row.get("evaluation_count")
        evaluated = row.get("evaluated")
        if not _actual_int(count):
            unknown_evaluation += 1
            continue
        if type(evaluated) is bool and ((evaluated and count == 0) or (not evaluated and count > 0)):
            unknown_evaluation += 1
            continue
        eligible.append({
            "era_id": key[0],
            "config_hash": key[1],
            "feature": key[2],
            "rotation": key[3],
            "evaluation_count": count,
            "evaluated": evaluated if type(evaluated) is bool else None,
            "pair_attribution": row["pair_attribution"],
            "attribution_provenance": row["attribution_provenance"],
        })

    eligible.sort(key=lambda item: (item["evaluation_count"], item["feature"], item["rotation"]))
    minimum = eligible[0]["evaluation_count"] if eligible else None
    minimum_pairs = [item for item in eligible if item["evaluation_count"] == minimum] if minimum is not None else []
    scoped = scope is not None and not scope.get("conflict")
    diagnostics = []
    if templates:
        diagnostics.append(f"{templates} template coverage row(s) ignored as non-observations")
    if not scoped:
        diagnostics.append("campaign era/config scope is missing or conflicting; coverage rows were not pooled")
    if conflicts:
        diagnostics.append(f"{len(conflicts)} conflicting feature/rotation key(s) excluded as unknown")
    if duplicate_rows:
        diagnostics.append(f"{duplicate_rows} exact duplicate coverage row(s) deduplicated")
    if unknown_evaluation:
        diagnostics.append(f"{unknown_evaluation} eligible row(s) have unknown or contradictory evaluation counts")
    if ineligible_attribution:
        diagnostics.append(f"{ineligible_attribution} otherwise valid row(s) lack explicit attribution provenance")
    counts = {
        "pair_rows_in_file": _count(pair_rows, "observed_input_rows"),
        "matching_scope_rows": _count(matching_rows, "observed_input_rows") if scoped else unknown,
        "eligible_loaded_valid_attributed_pairs": _count(len(qualified_pairs), "observed_ledger_rows_only") if scoped else unknown,
        "known_evaluation_count_pairs": _count(len(eligible), "observed_ledger_rows_only") if scoped else unknown,
        "minimum_known_count_pairs": _count(len(minimum_pairs), "observed_ledger_rows_only") if scoped else unknown,
        "unknown_evaluation_count_pairs": _count(unknown_evaluation, "observed_ledger_rows_only") if scoped else unknown,
        "conflicting_pair_keys": _count(len(conflicts), "observed_conflicts") if scoped else unknown,
        "duplicate_rows": _count(duplicate_rows, "observed_input_rows"),
        "eligible_pair_universe": _count(None, "unknown", "Observed ledger rows do not establish the complete eligible denominator."),
        "out_of_scope_rows": _count(out_of_scope, "observed_input_rows") if scoped else unknown,
        "unknown_scope_rows": _count(unknown_scope_rows, "observed_input_rows"),
        "template_rows_ignored": _count(templates, "ignored_template_rows"),
        "invalid_identity_rows_ignored": _count(invalid_identity_rows, "ignored_incomplete_rows"),
    }
    return {
        "provided": True,
        "counts": counts,
        "candidates": eligible,
        "minimum_candidates": minimum_pairs,
        "diagnostics": diagnostics,
        "conflicting_keys": conflicts,
    }


def _read_experiment_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    if path.suffix.lower() == ".jsonl":
        return _read_jsonl(path, "--experiments")
    value, digest = _read_json(path, "--experiments")
    if isinstance(value, dict):
        return [value], digest
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value, digest
    raise NextActionError("--experiments: expected one experiment object, an array of objects, or a .jsonl inventory")


def _experiment_records(records: list[dict[str, Any]] | None) -> dict[str, Any]:
    if records is None:
        return {
            "provided": False,
            "records": [],
            "counts": {
                "records_in_file": _count(None, "unknown", "No experiment evidence supplied."),
                "non_template_records": _count(None, "unknown", "No experiment evidence supplied."),
                "independent_paired_runs": _count(None, "unknown", "No experiment evidence supplied."),
            },
            "templates": 0,
            "duplicates": 0,
            "conflicts": [],
            "diagnostics": [],
        }
    templates = 0
    duplicates = 0
    diagnostics: list[str] = []
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []
    conflicts: set[tuple[str, str]] = set()
    for row in records:
        if "_template" in row and type(row["_template"]) is not bool:
            raise NextActionError("--experiments: _template must be a JSON boolean when present")
        if row.get("_template") is True:
            templates += 1
            continue
        record_type = row.get("record_type")
        if record_type == "experiment_trial":
            key_value = row.get("trial_id")
            key_type = "trial"
        elif record_type == "experiment_record":
            key_value = row.get("experiment_id")
            key_type = "aggregate"
        else:
            unkeyed.append(row)
            diagnostics.append("an unrecognized non-template experiment record was ignored")
            continue
        if not _real_string(key_value):
            unkeyed.append(row)
            diagnostics.append("an experiment record without a real trial/experiment id was ignored")
            continue
        key = (key_type, key_value)
        previous = keyed.get(key)
        if previous is None:
            keyed[key] = row
        elif _json_digest(previous) == _json_digest(row):
            duplicates += 1
        else:
            conflicts.add(key)
    usable = [row for key, row in keyed.items() if key not in conflicts]
    if conflicts:
        diagnostics.append(f"{len(conflicts)} conflicting experiment id(s) excluded as unknown")
    if templates:
        diagnostics.append(f"{templates} template experiment record(s) ignored as non-observations")
    usable.sort(key=lambda row: (
        str(row.get("experiment_id", "")),
        str(row.get("trial_id", "")),
        str(row.get("record_type", "")),
    ))
    return {
        "provided": True,
        "records": usable,
        "counts": {
            "records_in_file": _count(len(records), "observed_input_records"),
            "non_template_records": _count(len(usable), "observed_non_template_records"),
            "independent_paired_runs": _count(None, "unknown", "Only explicit independent-run records count; candidate evaluations do not."),
        },
        "templates": templates,
        "duplicates": duplicates,
        "conflicts": sorted(f"{kind}:{identifier}" for kind, identifier in conflicts),
        "diagnostics": diagnostics,
    }


def _paper_source(record: dict[str, Any]) -> str | None:
    paper = record.get("paper")
    source = paper.get("source") if isinstance(paper, dict) else None
    return source if _real_string(source) else None


def _has_negative_finding(record: dict[str, Any]) -> bool:
    findings = record.get("negative_findings")
    if not isinstance(findings, list):
        return False
    return any(
        isinstance(item, dict) and _real_string(item.get("finding"))
        for item in findings
    )


def _research_progress(
    context: dict[str, Any],
    experiment_data: dict[str, Any],
    campaign: dict[str, Any],
    opportunities: list[dict[str, Any]],
    negative_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    progress = context.get("progress", {})
    investigated = set(progress.get("investigated_opportunity_ids", []))
    negative = set(progress.get("negative_approach_ids", []))
    analyzed = set(progress.get("analyzed_experiment_ids", []))
    seen_sources: set[str] = set()
    source_ids: dict[str, list[str]] = {}
    for opportunity in opportunities:
        source_ids.setdefault(opportunity["primary_source"], []).append(opportunity["opportunity_id"])
    for record in experiment_data.get("records", []):
        opportunity_id = record.get("opportunity_id", record.get("research_opportunity_id"))
        source = _paper_source(record)
        if _real_string(opportunity_id) and any(item["opportunity_id"] == opportunity_id for item in opportunities):
            investigated.add(opportunity_id)
            if _has_negative_finding(record):
                negative.add(opportunity_id)
        elif source in source_ids:
            seen_sources.add(source)
            matches = source_ids[source]
            if len(matches) == 1:
                investigated.add(matches[0])
                if _has_negative_finding(record):
                    negative.add(matches[0])
        status = record.get("status")
        if status == "negative" and _real_string(opportunity_id):
            negative.add(opportunity_id)

    campaign_data = campaign.get("data", {}) if campaign.get("kind") == "real" else {}
    campaign_findings = campaign_data.get("negative_findings", []) if isinstance(campaign_data, dict) else []
    if isinstance(campaign_findings, list):
        for finding in campaign_findings:
            if (
                isinstance(finding, dict)
                and _real_string(finding.get("opportunity_id"))
                and any(item["opportunity_id"] == finding["opportunity_id"] for item in opportunities)
                and _real_string(finding.get("finding"))
            ):
                opportunity_id = finding["opportunity_id"]
                investigated.add(opportunity_id)
                negative.add(opportunity_id)
    paper_stage = campaign_data.get("pipeline_stages", {}).get("paper", {}) if isinstance(campaign_data.get("pipeline_stages"), dict) else {}
    paper_source = paper_stage.get("source") if isinstance(paper_stage, dict) else None
    if _real_string(paper_source) and paper_source in source_ids:
        seen_sources.add(paper_source)
        matches = source_ids[paper_source]
        if len(matches) == 1 and _real_string(paper_stage.get("claim")):
            investigated.add(matches[0])

    for item in opportunities:
        if item.get("current_status") == "negative":
            negative.add(item["opportunity_id"])
            investigated.add(item["opportunity_id"])
    for result in negative_results or []:
        opportunity_id = result.get("opportunity_id") if isinstance(result, dict) else None
        if (
            _real_string(opportunity_id)
            and any(item["opportunity_id"] == opportunity_id for item in opportunities)
            and _real_string(result.get("observed_result"))
            and _real_string(result.get("evidence_artifact"))
        ):
            investigated.add(opportunity_id)
            negative.add(opportunity_id)
    return {
        "investigated": investigated,
        "negative": negative,
        "analyzed": analyzed,
        "seen_sources": seen_sources,
        "active": progress.get("active_opportunity_id"),
        "pending_ablation": progress.get("pending_ablation"),
    }


def _baseline_state(campaign: dict[str, Any]) -> dict[str, Any]:
    if campaign.get("kind") != "real":
        return {"ready": False, "observed": None, "status": "unknown"}
    data = campaign["data"]
    frozen = data["frozen_baseline"]
    compatible = data["compatible_baseline"]
    observed = frozen.get("observed_completed_simulations")
    identity = frozen.get("algorithm_identity")
    if isinstance(identity, dict):
        identity = identity.get("recorded_algorithm")
    ga_identity = isinstance(identity, str) and identity.strip().casefold() == "genetic"
    compatible_status = compatible.get("status") in {"found", "created"}
    compatible_id = compatible.get("baseline_id")
    linked_to_ga = (
        _real_string(compatible_id)
        and (
            compatible_id == frozen.get("id")
            or isinstance(compatible.get("lineage"), list) and frozen.get("id") in compatible["lineage"]
        )
    )
    complete = (
        frozen.get("availability") == "available"
        and frozen.get("frozen") is True
        and ga_identity
        and _actual_int(observed, TARGET_COMPLETIONS)
        and _real_string(frozen.get("id"))
        and isinstance(frozen.get("hash"), str)
        and SHA256_RE.fullmatch(frozen["hash"].strip()) is not None
        and compatible_status
        and linked_to_ga
        and compatible.get("config_frozen_before_run") is True
        and compatible.get("evidence_frozen_after_run") is True
        and campaign.get("scope") is not None
        and not campaign["scope"].get("conflict")
    )
    if complete:
        status = "complete"
    elif _actual_int(observed) or compatible.get("status") in {"found", "created", "incomplete"}:
        status = "partial_or_incompatible"
    else:
        status = "missing_or_unknown"
    return {"ready": complete, "observed": observed if _actual_int(observed) else None, "status": status}


def _pipeline_stages(campaign: dict[str, Any]) -> dict[str, bool]:
    if campaign.get("kind") != "real":
        return {key: False for key in ("paper", "mechanism", "hypothesis", "custom_algorithm")}
    stages = campaign["data"].get("pipeline_stages", {})
    paper = stages.get("paper", {}) if isinstance(stages, dict) else {}
    mechanism = stages.get("mechanism", {}) if isinstance(stages, dict) else {}
    hypothesis = stages.get("hypothesis", {}) if isinstance(stages, dict) else {}
    custom = stages.get("custom_algorithm", {}) if isinstance(stages, dict) else {}
    return {
        "paper": isinstance(paper, dict) and _real_string(paper.get("source")) and _real_string(paper.get("claim")),
        "mechanism": isinstance(mechanism, dict) and _real_string(mechanism.get("statement")) and _real_string(mechanism.get("falsifier")),
        "hypothesis": isinstance(hypothesis, dict) and all(
            _real_string(hypothesis.get(key)) for key in ("statement", "comparison", "threshold")
        ),
        "custom_algorithm": (
            isinstance(custom, dict)
            and _real_string(custom.get("name"))
            and _actual_int(custom.get("depth_cap"), 1)
            and custom.get("depth_cap") <= 6
            and custom.get("self_rejects_depth_gt_6") is True
            and custom.get("filler_nodes_added") is False
            and custom.get("acceptance_thresholds_unchanged") is True
        ),
    }


def _preregistration_ready(campaign: dict[str, Any]) -> bool:
    if campaign.get("kind") != "real":
        return False
    prereg = campaign["data"].get("preregistration", {})
    if not isinstance(prereg, dict):
        return False
    seeds = prereg.get("seeds_repetitions")
    allocations = prereg.get("evaluation_allocations")
    scope = prereg.get("data_scope")
    schedule = prereg.get("look_schedule")
    minimum = prereg.get("minimum_meaningful_effect")
    minimum_real = _actual_number(minimum) or _real_string(minimum)
    if not isinstance(seeds, dict) or not isinstance(allocations, dict) or not isinstance(scope, dict) or not isinstance(schedule, dict):
        return False
    required_strings = (
        "protocol_id", "protocol_hash", "hypothesis", "mechanism", "primary_metric",
        "comparator", "decision_criteria", "stopping_criteria", "holdout_handling",
    )
    if any(not _real_string(prereg.get(key)) for key in required_strings):
        return False
    if SHA256_RE.fullmatch(prereg["protocol_hash"].strip()) is None:
        return False
    if not _is_iso(prereg.get("recorded_at")) or prereg.get("recorded_before_results") is not True:
        return False
    direction = prereg.get("direction")
    if not isinstance(direction, str) or direction not in {"increase", "decrease", "two-sided"} or not minimum_real:
        return False
    if not _real_string(seeds.get("seed_set_id")) or not _actual_int(seeds.get("search_runs_per_arm"), 1) or seeds.get("predeclared") is not True:
        return False
    if not all(_actual_int(allocations.get(key), 1) for key in ("baseline", "variant")):
        return False
    if allocations.get("pilots_counted_separately") is not True:
        return False
    if not all(_real_string(scope.get(key)) for key in ("era_id", "config_hash", "history_window", "loaded_vocabulary_hash")):
        return False
    campaign_scope = campaign.get("scope")
    if campaign_scope is not None and not campaign_scope.get("conflict"):
        if any(scope.get(key) != campaign_scope.get(key) for key in ("era_id", "config_hash")):
            return False
    if not isinstance(prereg.get("exclusions"), list) or not _real_string(prereg.get("decision_criteria")):
        return False
    if schedule.get("kind") == "fixed_horizon":
        return _actual_int(schedule.get("looks"), 1)
    if schedule.get("kind") == "sequential":
        return _actual_int(schedule.get("looks"), 1) and _real_string(schedule.get("sequential_method")) and _real_string(schedule.get("sequential_justification"))
    return False


def _uncertainty_present(value: Any) -> bool:
    if _actual_number(value) or _real_string(value):
        return True
    if isinstance(value, dict):
        return any(_actual_number(item) for item in value.values())
    return False


def _scope_matches(scope: dict[str, Any] | None, era: Any, config: Any) -> bool:
    if not _real_string(era) or not _real_string(config):
        return False
    if scope is None:
        return True
    if scope.get("conflict"):
        return False
    return era == scope.get("era_id") and config == scope.get("config_hash")


def _aggregate_comparison(
    record: dict[str, Any], scope: dict[str, Any] | None, campaign_id: str | None
) -> dict[str, Any] | None:
    if record.get("record_type") != "experiment_record" or record.get("pipeline_stage") != "measured_comparison":
        return None
    experiment_id = record.get("experiment_id")
    prereg_ref = record.get("preregistration_ref")
    comparison = record.get("comparison")
    statistics = record.get("statistics")
    provenance = record.get("provenance")
    data_scope = provenance.get("data_scope") if isinstance(provenance, dict) else None
    if not _real_string(experiment_id) or not isinstance(prereg_ref, dict) or not isinstance(comparison, dict) or not isinstance(statistics, dict) or not isinstance(data_scope, dict):
        return None
    if campaign_id is not None and record.get("campaign_id") != campaign_id:
        return None
    if (
        prereg_ref.get("recorded_before_results") is not True
        or not isinstance(prereg_ref.get("protocol_hash"), str)
        or not SHA256_RE.fullmatch(prereg_ref["protocol_hash"].strip())
    ):
        return None
    baseline = comparison.get("baseline")
    variant = comparison.get("variant")
    if not isinstance(baseline, dict) or not isinstance(variant, dict):
        return None
    baseline_budget = baseline.get("budget_evaluations")
    variant_budget = variant.get("budget_evaluations")
    baseline_completed = baseline.get("completed_evaluator_simulations")
    variant_completed = variant.get("completed_evaluator_simulations")
    run_base = statistics.get("search_runs_baseline")
    run_variant = statistics.get("search_runs_variant")
    if (
        comparison.get("budgets_matched") is not True
        or comparison.get("seeds_matched") is not True
        or not _actual_int(baseline_budget, 1)
        or not _actual_int(variant_budget, 1)
        or baseline_budget != variant_budget
        or not _actual_int(baseline_completed, 1)
        or not _actual_int(variant_completed, 1)
        or baseline_completed != variant_completed
        or not _real_string(baseline.get("seed_set_id"))
        or baseline.get("seed_set_id") != variant.get("seed_set_id")
        or baseline.get("config_hash") != variant.get("config_hash")
        or not _real_string(baseline.get("algorithm_identity"))
        or not _real_string(variant.get("algorithm"))
    ):
        return None
    if (
        statistics.get("unit_of_analysis") != "independent_paired_search_runs"
        or not _actual_int(run_base, 2)
        or not _actual_int(run_variant, 2)
        or run_base != run_variant
    ):
        return None
    era = data_scope.get("era_id")
    config = data_scope.get("config_hash")
    if config != baseline.get("config_hash") or not _scope_matches(scope, era, config):
        return None
    effect = comparison.get("effect_size")
    uncertainty = comparison.get("uncertainty")
    if not _actual_number(effect) or not _uncertainty_present(uncertainty):
        return None
    return {
        "experiment_id": experiment_id,
        "paired_runs": run_base,
        "completed_evaluator_simulations_baseline": baseline_completed,
        "completed_evaluator_simulations_variant": variant_completed,
        "era_id": era,
        "config_hash": config,
        "effect_size": effect,
        "uncertainty": uncertainty,
        "record_type": "experiment_record",
    }


def _valid_trial(
    record: dict[str, Any], scope: dict[str, Any] | None, campaign_id: str | None
) -> dict[str, Any] | None:
    if record.get("record_type") != "experiment_trial" or record.get("status") != "completed":
        return None
    experiment_id = record.get("experiment_id")
    run_id = record.get("search_run_id")
    baseline = record.get("baseline")
    variant = record.get("variant")
    result = record.get("result")
    if not all((_real_string(experiment_id), _real_string(run_id), isinstance(baseline, dict), isinstance(variant, dict), isinstance(result, dict))):
        return None
    if campaign_id is not None and record.get("campaign_id") != campaign_id:
        return None
    if record.get("unit_of_analysis") != "independent_paired_search_runs":
        return None
    if not _real_string(record.get("preregistration_protocol_id")):
        return None
    if record.get("budgets_matched") is not True or record.get("seeds_matched") is not True:
        return None
    base_budget = baseline.get("budget_evaluations")
    variant_budget = variant.get("budget_evaluations")
    base_completed = baseline.get("completed_evaluator_simulations")
    variant_completed = variant.get("completed_evaluator_simulations")
    if (
        not _actual_int(base_budget, 1) or not _actual_int(variant_budget, 1) or base_budget != variant_budget
        or not _actual_int(base_completed, 1) or not _actual_int(variant_completed, 1)
        or base_completed != variant_completed
        or not _real_string(baseline.get("algorithm_identity"))
        or not _real_string(variant.get("algorithm"))
    ):
        return None
    if not _real_string(baseline.get("seed_set_id")) or baseline.get("seed_set_id") != variant.get("seed_set_id"):
        return None
    config = baseline.get("config_hash")
    if config != variant.get("config_hash"):
        return None
    era = record.get("era_segment")
    config_segment = record.get("config_segment")
    if config_segment != config or not _scope_matches(scope, era, config):
        return None
    if not _actual_number(result.get("effect_size")) or not _uncertainty_present(result.get("uncertainty")):
        return None
    return {
        "experiment_id": experiment_id,
        "protocol_id": record["preregistration_protocol_id"],
        "run_id": run_id,
        "completed_evaluator_simulations_baseline": base_completed,
        "completed_evaluator_simulations_variant": variant_completed,
        "era_id": era,
        "config_hash": config,
        "effect_size": result["effect_size"],
        "uncertainty": result["uncertainty"],
        "record_type": "experiment_trial",
    }


def _comparison_analysis(
    experiment_data: dict[str, Any], scope: dict[str, Any] | None, analyzed_ids: set[str], campaign_id: str | None
) -> dict[str, Any]:
    if not experiment_data.get("provided"):
        return {
            "ready": [],
            "unprocessed": [],
            "counts": {
                "independent_paired_runs": _count(None, "unknown", "No experiment evidence supplied."),
                "comparison_records_ready": _count(None, "unknown", "No experiment evidence supplied."),
                "completed_evaluator_simulations_per_arm": {
                    "baseline": _count(None, "unknown", "No experiment evidence supplied."),
                    "variant": _count(None, "unknown", "No experiment evidence supplied."),
                },
            },
            "diagnostics": [],
        }
    records = experiment_data.get("records", [])
    ready: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for record in records:
        aggregate = _aggregate_comparison(record, scope, campaign_id)
        if aggregate is not None:
            key = (aggregate["experiment_id"], aggregate["era_id"], aggregate["config_hash"], "aggregate")
            ready[key] = aggregate

    trial_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    trial_run_payloads: dict[tuple[str, str, str, str, str], set[str]] = {}
    protocols_by_segment: dict[tuple[str, str, str], set[str]] = {}
    for record in records:
        valid = _valid_trial(record, scope, campaign_id)
        if valid is None:
            continue
        key = (valid["experiment_id"], valid["era_id"], valid["config_hash"], valid["protocol_id"])
        protocols_by_segment.setdefault(key[:3], set()).add(key[3])
        run_key = (*key, valid["run_id"])
        payload_digest = _json_digest(valid)
        payloads = trial_run_payloads.setdefault(run_key, set())
        payloads.add(payload_digest)
        trial_groups.setdefault(key, []).append(valid)

    conflicting_runs = {key for key, payloads in trial_run_payloads.items() if len(payloads) > 1}
    protocol_conflicts = {key for key, values in protocols_by_segment.items() if len(values) > 1}
    for key, runs in trial_groups.items():
        if key[:3] in protocol_conflicts:
            continue
        unique_runs: dict[str, dict[str, Any]] = {}
        conflicting = False
        for run in runs:
            run_key = (*key, run["run_id"])
            if run_key in conflicting_runs:
                conflicting = True
                continue
            unique_runs.setdefault(run["run_id"], run)
        if not conflicting and len(unique_runs) >= 2:
            ready[(*key, "trial_inventory")] = {
                "experiment_id": key[0],
                "protocol_id": key[3],
                "paired_runs": len(unique_runs),
                "completed_evaluator_simulations_baseline": sum(run["completed_evaluator_simulations_baseline"] for run in unique_runs.values()),
                "completed_evaluator_simulations_variant": sum(run["completed_evaluator_simulations_variant"] for run in unique_runs.values()),
                "era_id": key[1],
                "config_hash": key[2],
                "effect_size": None,
                "uncertainty": None,
                "record_type": "experiment_trial_inventory",
            }

    by_experiment_segment: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in ready.values():
        key = (item["experiment_id"], item["era_id"], item["config_hash"])
        by_experiment_segment.setdefault(key, []).append(item)
    deduplicated: list[dict[str, Any]] = []
    conflicting_comparisons = 0
    for group in by_experiment_segment.values():
        if len(group) > 1:
            signatures = {
                (item["paired_runs"], item["completed_evaluator_simulations_baseline"], item["completed_evaluator_simulations_variant"])
                for item in group
            }
            if len(signatures) != 1:
                conflicting_comparisons += 1
                continue
            aggregate = next((item for item in group if item["record_type"] == "experiment_record"), None)
            deduplicated.append(aggregate or group[0])
        else:
            deduplicated.append(group[0])
    ordered = sorted(deduplicated, key=lambda item: (item["experiment_id"], item["era_id"], item["config_hash"], item.get("protocol_id", ""), item["record_type"]))
    unprocessed = [item for item in ordered if item["experiment_id"] not in analyzed_ids]
    all_run_counts = [item["paired_runs"] for item in ordered]
    partial_aggregates: dict[tuple[str, str, str, str], int] = {}
    partial_trials: dict[tuple[str, str, str, str], set[str]] = {}
    for record in records:
        stats = record.get("statistics")
        if record.get("record_type") == "experiment_record" and isinstance(stats, dict):
            base = stats.get("search_runs_baseline")
            variant = stats.get("search_runs_variant")
            data_scope = record.get("provenance", {}).get("data_scope") if isinstance(record.get("provenance"), dict) else None
            protocol = record.get("preregistration_ref", {}).get("protocol_id") if isinstance(record.get("preregistration_ref"), dict) else None
            record_campaign = record.get("campaign_id")
            if (
                (campaign_id is None or record_campaign == campaign_id)
                and isinstance(data_scope, dict)
                and _scope_matches(scope, data_scope.get("era_id"), data_scope.get("config_hash"))
                and _real_string(protocol)
                and stats.get("unit_of_analysis") == "independent_paired_search_runs"
                and _actual_int(base, 1)
                and _actual_int(variant, 1)
                and base == variant
            ):
                partial_key = (str(record["experiment_id"]), data_scope["era_id"], data_scope["config_hash"], f"aggregate:{protocol}")
                partial_aggregates[partial_key] = base
        elif (
            record.get("record_type") == "experiment_trial"
            and record.get("status") == "completed"
            and record.get("unit_of_analysis") == "independent_paired_search_runs"
            and _real_string(record.get("trial_id"))
            and _real_string(record.get("search_run_id"))
            and _real_string(record.get("experiment_id"))
            and _real_string(record.get("preregistration_protocol_id"))
            and (campaign_id is None or record.get("campaign_id") == campaign_id)
            and _scope_matches(scope, record.get("era_segment"), record.get("config_segment"))
        ):
            partial_key = (
                record["experiment_id"], record["era_segment"], record["config_segment"],
                f"trials:{record['preregistration_protocol_id']}",
            )
            partial_trials.setdefault(partial_key, set()).add(record["search_run_id"])
    partial_counts = list(partial_aggregates.values()) + [len(run_ids) for run_ids in partial_trials.values()]
    if all_run_counts:
        selected_runs: int | None = unprocessed[0]["paired_runs"] if unprocessed else ordered[0]["paired_runs"]
        run_count = _count(selected_runs, "observed_independent_pairs", "Counts belong to one experiment and one era/config segment; no cross-experiment pooling.")
        selected = unprocessed[0] if unprocessed else ordered[0]
        completed_counts = {
            "baseline": _count(selected["completed_evaluator_simulations_baseline"], "observed_completed_evaluator_simulations"),
            "variant": _count(selected["completed_evaluator_simulations_variant"], "observed_completed_evaluator_simulations"),
        }
    elif len(partial_counts) == 1:
        run_count = _count(partial_counts[0], "observed_insufficient_or_unmatched", "This is one experiment/protocol/era/config group; other comparison gates remain incomplete.")
        completed_counts = {
            "baseline": _count(None, "unknown", "No complete compatible comparison establishes completed evaluator counts."),
            "variant": _count(None, "unknown", "No complete compatible comparison establishes completed evaluator counts."),
        }
    elif len(partial_counts) > 1:
        run_count = _count(None, "unknown", "Independent-run counts span multiple experiments/protocols/era/config segments and are not pooled.")
        completed_counts = {
            "baseline": _count(None, "unknown", "No complete compatible comparison establishes completed evaluator counts."),
            "variant": _count(None, "unknown", "No complete compatible comparison establishes completed evaluator counts."),
        }
    else:
        run_count = _count(None, "unknown", "No explicit independent paired search-run count was observed.")
        completed_counts = {
            "baseline": _count(None, "unknown", "No compatible completed evaluator count was observed."),
            "variant": _count(None, "unknown", "No compatible completed evaluator count was observed."),
        }
    diagnostics = list(experiment_data.get("diagnostics", []))
    if conflicting_runs:
        diagnostics.append(f"{len(conflicting_runs)} conflicting search_run_id(s) excluded as unknown")
    if protocol_conflicts:
        diagnostics.append(f"{len(protocol_conflicts)} experiment/era/config group(s) span multiple protocols and were not pooled")
    if conflicting_comparisons:
        diagnostics.append(f"{conflicting_comparisons} aggregate/trial comparison group(s) conflict on counts and were excluded")
    if records and not ordered:
        diagnostics.append("no record met the independent-run, matched-budget/seed, segment, and measurement gate")
    return {
        "ready": ordered,
        "unprocessed": unprocessed,
        "counts": {
            "independent_paired_runs": run_count,
            "comparison_records_ready": _count(len(ordered), "observed_ready_experiments"),
            "completed_evaluator_simulations_per_arm": completed_counts,
        },
        "diagnostics": diagnostics,
        "conflicting_runs": sorted(":".join(key) for key in conflicting_runs),
    }


def _choose_opportunity(
    opportunities: list[dict[str, Any]], progress: dict[str, Any]
) -> dict[str, Any] | None:
    available = [
        item for item in opportunities
        if item.get("current_status") in {"proposed", "in-progress"}
        and item["opportunity_id"] not in progress["investigated"]
        and item["opportunity_id"] not in progress["negative"]
        and item["primary_source"] not in progress["seen_sources"]
    ]
    active = progress.get("active")
    if isinstance(active, str) and active not in progress["negative"]:
        match = next((item for item in opportunities if item["opportunity_id"] == active and item.get("current_status") in {"proposed", "in-progress"}), None)
        if match is not None:
            return match
    return available[0] if available else None


def _source_ref(ref: str, digest: str | None = None, role: str | None = None) -> dict[str, str]:
    result = {"ref": ref}
    if digest:
        result["sha256"] = digest
    if role:
        result["role"] = role
    return result


def _input_refs(digests: dict[str, str], aliases: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        _source_ref(alias, digests[alias], "supplied-read-only-evidence")
        for alias in aliases if alias in digests
    ]


def _optional_input_source(digests: dict[str, str], alias: str, role: str) -> list[dict[str, str]]:
    if alias not in digests:
        return []
    return [_source_ref(alias, digests[alias], role)]


def _opportunity_refs(opportunity: dict[str, Any] | None, digests: dict[str, str]) -> list[dict[str, str]]:
    refs = [_source_ref(OPPORTUNITIES_REL, digests.get("opportunities_map"), "proposal-index-not-result")]
    if opportunity is not None:
        refs.append(_source_ref(
            f"{OPPORTUNITIES_REL}#opportunities/{opportunity['opportunity_id']}",
            digests.get("opportunities_map"),
            "candidate-direction",
        ))
        refs.append(_source_ref(opportunity["primary_source"], role="primary-source-candidate; reopen before relying on claim"))
    return refs


def _make_action(
    *,
    action_id: str,
    kind: str,
    title: str,
    reason: str,
    source_refs: list[dict[str, str]],
    evidence_refs: list[dict[str, str]],
    unknowns: list[str],
    inputs_needed: list[str],
    artifact: str,
    done_criteria: str,
    relevant_check: str,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "kind": kind,
        "title": title,
        "reason": reason,
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
        "unknowns": list(dict.fromkeys(unknowns)),
        "inputs_needed": inputs_needed,
        "done_when": {"artifact": artifact, "criteria": done_criteria},
        "relevant_check": relevant_check,
        "native_handoff": (
            "Hand off this single proposal through the selected harness's documented native goal/task store; "
            "use native memory for evidence pointers and native scheduling only when the user asks. "
            "This output is not a task database, scheduler, execution permission, or campaign mutation."
        ),
    }


def _unknown_count_paths(counts: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in counts.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and "value" in value and value.get("status") == "unknown":
            paths.append(path)
        elif isinstance(value, dict):
            paths.extend(_unknown_count_paths(value, path))
    return paths


def _counts_packet(
    campaign: dict[str, Any], coverage: dict[str, Any], experiments: dict[str, Any], comparison: dict[str, Any]
) -> dict[str, Any]:
    baseline = _baseline_state(campaign)
    experiment_counts = dict(experiments.get("counts", {}))
    experiment_counts.update(comparison.get("counts", {}))
    return {
        "baseline": {
            "target_completed_simulations": _count(TARGET_COMPLETIONS, "target_only", "This is a target, never an observed completion count."),
            "observed_completed_simulations": _count(
                baseline["observed"],
                "observed" if baseline["observed"] is not None else "unknown",
                "Only an exact non-negative integer from a non-template campaign record is an observation.",
            ),
            "reference_readiness": {"ready": baseline["ready"], "status": baseline["status"]},
        },
        "coverage": coverage["counts"],
        "experiments": experiment_counts,
    }


def _offline_design_action(
    opportunity: dict[str, Any] | None,
    digests: dict[str, str],
    evidence_refs: list[dict[str, str]],
    unknowns: list[str],
    *,
    stage: str = "paper_mechanism_hypothesis",
) -> dict[str, Any]:
    if opportunity is None:
        return _make_action(
            action_id="research:synthesize-recorded-directions",
            kind="offline_research_synthesis",
            title="Synthesize recorded directions and update the research map from evidence",
            reason="Every currently eligible map proposal is already recorded as investigated or has a terminal map status; reconcile evidence before selecting or publishing another direction.",
            source_refs=_opportunity_refs(None, digests),
            evidence_refs=evidence_refs,
            unknowns=unknowns + ["Which recorded findings satisfy the map's evidence standard remains unverified until reviewed."],
            inputs_needed=["Complete trial-inventory and source references for each recorded direction."],
            artifact="A local synthesis note and evidence-linked proposed map update.",
            done_criteria="Keep each positive, negative, inconclusive, and unknown result tied to its own evidence; do not promote citations or structural co-occurrence into superiority or OOS claims.",
            relevant_check="python .github/scripts/validate_agent_docs.py opportunities records",
        )

    title = opportunity["title"]
    reason = (
        f"The canonical map lists {opportunity['opportunity_id']} as a research proposal. "
        "Its citation motivates a testable direction; it is not evidence of superiority. "
        "This offline step remains useful while baseline, access, or installed-surface evidence is unavailable."
    )
    if stage == "custom_algorithm":
        kind = "design_depth_capped_challenger"
        action_title = f"Design a depth-capped challenger for {title}"
        done = "A local design artifact names only verified public capabilities, valid loaded pairs, a strict depth cap of 6, unchanged acceptance gates, and no filler or transformed duplicates."
        artifact = "research.md#custom-algorithm"
    elif stage == "preregistration":
        kind = "preregister_paper_backed_challenger"
        action_title = f"Preregister a paper-backed challenger for {title}"
        done = "A hashed protocol recorded before any result states the falsifiable hypothesis, comparator, metric/effect threshold, independent-run repetitions, matched budgets/seeds, scope, exclusions, look schedule, and decision/stopping rules."
        artifact = "campaign.json#preregistration and research.md#hypothesis"
    else:
        kind = "offline_paper_mechanism_design"
        action_title = f"Draft the paper, mechanism, and falsifiable hypothesis for {title}"
        done = "A local note records the exact source claim, a market/era-specific mechanism and falsifier, one falsifiable hypothesis, required public capabilities, and explicit evidence gaps."
        artifact = "research.md#paper, research.md#mechanism, research.md#hypothesis"
    return _make_action(
        action_id=f"research:{kind}:{opportunity['opportunity_id']}",
        kind=kind,
        title=action_title,
        reason=reason,
        source_refs=_opportunity_refs(opportunity, digests),
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["Out-of-sample correlation and usefulness remain unknown until exposed server-side evidence exists."],
        inputs_needed=[
            "Reopen the cited primary source and record the exact claim without strengthening it.",
            "State the target era/config only when observed; otherwise keep scope unknown.",
            "Do not require an invite, installed package, daemon, worker, or live service for this offline artifact.",
        ],
        artifact=artifact,
        done_criteria=done,
        relevant_check="python .github/scripts/validate_agent_docs.py opportunities compatibility",
    )


def _campaign_state_action(
    campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]]
) -> dict[str, Any] | None:
    if campaign.get("kind") != "real":
        return None
    state = campaign["state"]
    state_ref = _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-state-graph")
    campaign_ref = _source_ref("campaign_manifest", digests.get("campaign_manifest"), "supplied-campaign-evidence")
    sources = [state_ref, campaign_ref]
    if state == "paused":
        target = campaign["data"]["state_machine"]["resume_target"]
        return _make_action(
            action_id=f"campaign:{campaign['data']['campaign_id']}:reconcile-paused",
            kind="reconcile_paused_campaign",
            title="Reconcile the paused campaign before any resume decision",
            reason=f"The authoritative state is paused with resume_target={target!r}. Workers/triggers must remain stopped until ownership, registry, status, events, and checkpoints are reconciled.",
            source_refs=sources,
            evidence_refs=evidence_refs,
            unknowns=["Current worker/trigger status and checkpoint reconciliation are not observed by this read-only helper."],
            inputs_needed=["Observed registry/status/events/checkpoints and a safe ownership/fencing transfer if the user later authorizes resume."],
            artifact="A reconciliation/checkpoint note attached to the campaign record.",
            done_criteria="Confirm the recorded resume_target only, advance ownership/fencing safely, and preserve paused state until the separate harness explicitly resumes; no automatic relaunch.",
            relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
        )
    if state == "needs-input":
        target = campaign["data"]["state_machine"]["resume_target"]
        return _make_action(
            action_id=f"campaign:{campaign['data']['campaign_id']}:resolve-input",
            kind="resolve_campaign_input",
            title="Identify and resolve the recorded campaign blocker",
            reason=f"The authoritative state is needs-input with resume_target={target!r}; do not relaunch while the required input or infrastructure remains unavailable.",
            source_refs=sources,
            evidence_refs=evidence_refs,
            unknowns=["The missing external input/infrastructure and its availability are not established by the manifest alone."],
            inputs_needed=["The specific missing user input or bounded infrastructure observation; never place credentials in campaign artifacts."],
            artifact="An append-only blocker-resolution observation and reconciled checkpoint note.",
            done_criteria="Record the resolved input and reconcile ownership/status/checkpoints; resume only to the preserved target after a distinct safe handoff.",
            relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
        )
    if state in campaign.get("terminal_states", TERMINAL_STATES):
        return _make_action(
            action_id=f"campaign:{campaign['data']['campaign_id']}:terminal-report-no-relaunch",
            kind="verify_terminal_report_no_relaunch",
            title=f"Verify the {state} terminal report and preserve the no-relaunch boundary",
            reason="Terminal states have no outgoing transitions. Preserve pending outcomes and relaunch_prevented; continuing requires a new campaign id and a newly recorded baseline.",
            source_refs=sources,
            evidence_refs=evidence_refs,
            unknowns=["The terminal report and delayed server outcomes need verification from their referenced artifacts."],
            inputs_needed=["Terminal-report artifact, pending/ambiguous server counts, and observed trigger/worker shutdown evidence."],
            artifact="The campaign terminal report and resources.terminal_report fields.",
            done_criteria="Confirm outcome/state agreement, pending outcomes preserved, and relaunch_prevented=true; do not reactivate this campaign record.",
            relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
        )
    return None


def _prepare_baseline_action(
    campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]], unknowns: list[str]
) -> dict[str, Any]:
    campaign_id = campaign["data"]["campaign_id"]
    scope = campaign.get("scope") or {}
    frozen = campaign["data"]["frozen_baseline"]
    identity = frozen.get("algorithm_identity")
    if isinstance(identity, dict):
        identity = identity.get("recorded_algorithm")
    reusable_reference_recorded = (
        frozen.get("availability") == "available"
        and frozen.get("frozen") is True
        and isinstance(identity, str)
        and identity.strip().casefold() == "genetic"
        and _actual_int(frozen.get("observed_completed_simulations"), TARGET_COMPLETIONS)
        and _real_string(frozen.get("id"))
        and isinstance(frozen.get("hash"), str)
        and SHA256_RE.fullmatch(frozen["hash"].strip()) is not None
    )
    refs = [
        _source_ref(CAMPAIGN_TEMPLATE_REL, digests.get("campaign_template"), "100000-is-target-not-result"),
        _source_ref("campaign_manifest#frozen_baseline", digests.get("campaign_manifest"), "supplied-measurement-record"),
        _source_ref("campaign_manifest#compatible_baseline", digests.get("campaign_manifest"), "scope-compatibility-record"),
    ]
    return _make_action(
        action_id=f"campaign:{campaign_id}:prepare-genetic-100000-baseline",
        kind="prepare_genetic_baseline",
        title=(
            "Establish a scope-compatible reference to the recorded GA baseline"
            if reusable_reference_recorded else
            "Prepare and freeze a real genetic baseline at 100,000 completed simulations"
        ),
        reason=(
            "The manifest records a missing or incomplete compatible genetic reference, and the supplied context records "
            f"explicit authorization plus observed access/runtime and scope for era {scope.get('era_id')!r} / config {scope.get('config_hash')!r}. "
            + (
                "A complete immutable GA reference already exists; first verify whether its exact era/config is compatible and link it without rerunning. If not compatible, create a distinct baseline id/lineage without overwriting it. "
                if reusable_reference_recorded else
                "Prepare a distinct baseline id/lineage and freeze config before any separately authorized run; do not overwrite an existing reference. "
            )
            + "This is a proposal only; it does not launch or claim a run."
        ),
        source_refs=refs,
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["No completed simulations are added by this helper; observed counts remain those already in the manifest."],
        inputs_needed=[
            "Check immutable existing reference id/hash and its actual era/config before deciding whether it can be reused; otherwise freeze a new real era/config/loaded vocabulary before the run.",
            "Record the actual genetic implementation identity and distinct id/lineage; never silently replace a recorded reference.",
            "Account only evaluator-returned completed simulations; exclude started, queued, checkpointed, cached, or failed work.",
            "Attach immutable evidence and hashes after any separately authorized run; never overwrite an earlier baseline.",
        ],
        artifact="campaign.json#frozen_baseline, campaign.json#compatible_baseline, benchmark_record.json, and immutable run evidence.",
        done_criteria="Either link an immutable same-scope reference with evidence, or record a distinct compatible baseline with frozen config/evidence and at least 100000 measured completed evaluator simulations. Preserve incomplete counts and never substitute the target for an observation.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _freeze_baseline_action(
    campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]]
) -> dict[str, Any]:
    return _make_action(
        action_id=f"campaign:{campaign['data']['campaign_id']}:reconcile-frozen-baseline",
        kind="reconcile_frozen_baseline_record",
        title="Reconcile the completed baseline evidence and legal state transition",
        reason="The recorded reference and compatible baseline meet the count/identity gates, but the campaign remains in baseline; freeze the evidence and reconcile the append-only transition before research.",
        source_refs=[
            _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-transition-graph"),
            _source_ref("campaign_manifest#frozen_baseline", digests.get("campaign_manifest"), "supplied-measurement-record"),
        ],
        evidence_refs=evidence_refs,
        unknowns=["The source artifacts and transition guard evidence are not independently verified by this helper."],
        inputs_needed=["Configuration-frozen-before-run artifact, completed-evaluator evidence, post-run evidence hash, and verified ownership/transition record."],
        artifact="Immutable baseline artifacts plus an append-only T-baseline-freeze transition in campaign.json.",
        done_criteria="The state advances only after every canonical guard is observed; retain exact measured counts and distinct lineage.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _ablation_action(
    opportunity: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]], unknowns: list[str]
) -> dict[str, Any]:
    return _make_action(
        action_id=f"research:design-ablation:{opportunity['opportunity_id']}",
        kind="design_ablation",
        title=f"Design one controlled ablation for {opportunity['title']}",
        reason="The optional context records an ablation target and evidence reference. Isolate one mechanism component; the recorded pointer is not itself a measured finding.",
        source_refs=_opportunity_refs(opportunity, digests),
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["The ablation effect remains unknown until independent matched runs are recorded."],
        inputs_needed=["Review the recorded basis artifact; identify one component to remove while holding scope, budget, seeds, depth cap, and acceptance gates fixed."],
        artifact="A new versioned preregistration and research.md#custom-algorithm ablation note.",
        done_criteria="State a falsifiable ablation hypothesis, preserve the original protocol/results, keep depth <= 6, and change neither acceptance thresholds nor graph content with filler/duplicates.",
        relevant_check="python .github/scripts/validate_agent_docs.py records opportunities",
    )


def _comparison_action(
    comparison: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]], unknowns: list[str]
) -> dict[str, Any]:
    item = comparison["unprocessed"][0]
    experiment_id = item["experiment_id"]
    return _make_action(
        action_id=f"experiment:{experiment_id}:analyze-independent-runs",
        kind="analyze_independent_run_comparison",
        title="Analyze one matched independent-run comparison",
        reason=(
            f"Experiment {experiment_id!r} has {item['paired_runs']} explicit independent paired search run(s) "
            "with matched budgets/seeds, equal observed completed-evaluator counts, a single era/config segment, and non-null effect/uncertainty evidence. "
            "This authorizes analysis only, not a superiority or out-of-sample conclusion."
        ),
        source_refs=[
            _source_ref("experiment_evidence", digests.get("experiment_evidence"), "supplied-measurement-record"),
            _source_ref("docs/agents/research-campaign.md#statistical-units-and-inference", role="analysis-contract"),
        ],
        evidence_refs=evidence_refs,
        unknowns=unknowns + [
            "Out-of-sample correlation/usefulness remain unknown unless separately returned by authorized server-side evaluation.",
            "Structural co-occurrence is not return correlation.",
        ],
        inputs_needed=["Complete the trial inventory, preregistered analysis method, effect size, uncertainty, negative findings, and delayed server-outcome reconciliation."],
        artifact="experiment_record.json#comparison and research.md#measured-comparison.",
        done_criteria="Report only the independently paired runs in this experiment/era/config; retain exploratory classification when gates are incomplete, and state unavailable metrics as unknown rather than zero.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _reconcile_comparison_phase_action(
    campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]]
) -> dict[str, Any]:
    campaign_id = campaign["data"]["campaign_id"]
    return _make_action(
        action_id=f"campaign:{campaign_id}:reconcile-comparison-phase",
        kind="reconcile_measured_comparison_phase",
        title="Reconcile measured evidence with the append-only campaign state",
        reason=f"Independent-run evidence is present while the manifest state is {campaign['state']!r}; reconcile the legal state edge and trial inventory before treating it as a campaign comparison.",
        source_refs=[
            _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-transition-graph"),
            _source_ref("campaign_manifest", digests.get("campaign_manifest"), "supplied-campaign-evidence"),
            _source_ref("experiment_evidence", digests.get("experiment_evidence"), "supplied-measurement-record"),
        ],
        evidence_refs=evidence_refs,
        unknowns=["The run/state chronology and transition guard are not independently verified by this helper."],
        inputs_needed=["Reconcile ownership, checkpoints, complete trial inventory, and the legal comparison transition before analysis."],
        artifact="Append-only campaign transition and reconciled experiment record.",
        done_criteria="Do not rewrite history, infer missing states, or draw a result until the state-machine guard and measured evidence agree.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _reconcile_experiment_action(
    state: str, campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]], unknowns: list[str]
) -> dict[str, Any]:
    campaign_id = campaign["data"]["campaign_id"] if campaign.get("kind") == "real" else "unscoped"
    comparison_state = state == "comparison"
    return _make_action(
        action_id=f"campaign:{campaign_id}:reconcile-{state}-evidence",
        kind="reconcile_comparison_evidence" if comparison_state else "reconcile_active_experiment",
        title="Reconcile the experiment inventory and campaign status before analysis",
        reason=(
            "The campaign is in comparison but no complete independent-run comparison is evidenced."
            if comparison_state else
            "The campaign is in experiment; this read-only helper cannot tell whether a worker, checkpoint, or run is still active, so reconcile before any continuation."
        ),
        source_refs=[
            _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-state-graph"),
            _source_ref("experiment_evidence", digests.get("experiment_evidence"), "supplied-trial-inventory"),
        ],
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["Measured effect, uncertainty, completed independent runs, and server-side outcomes remain unknown unless recorded in compatible evidence."],
        inputs_needed=["Reconcile status/events/checkpoints and every completed, failed, negative, or abandoned trial; separate evaluator completions from candidate proposals and cache hits."],
        artifact="Append-only experiment inventory plus a reconciled campaign checkpoint/state observation.",
        done_criteria="Keep era/config segments separate; do not infer a result from target counts, unknown counters, or structural co-occurrence; do not relaunch based only on this proposal.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _live_prerequisites_action(
    campaign: dict[str, Any], digests: dict[str, str], evidence_refs: list[dict[str, str]], missing: list[str]
) -> dict[str, Any]:
    campaign_id = campaign["data"]["campaign_id"]
    return _make_action(
        action_id=f"campaign:{campaign_id}:verify-live-prerequisites",
        kind="verify_live_prerequisites",
        title="Record only the missing live-campaign prerequisites",
        reason="The compatible baseline and preregistration are present, but one or more explicit authorization/access/runtime observations are not established. Offline preparation may continue; live work is not recommended yet.",
        source_refs=[
            _source_ref("campaign_manifest#preregistration", digests.get("campaign_manifest"), "supplied-campaign-evidence"),
        ] + _optional_input_source(digests, "context", "optional-operator-observations"),
        evidence_refs=evidence_refs,
        unknowns=missing + ["No live worker/session status is queried by this helper."],
        inputs_needed=["For each missing gate, record a source and observation only after explicit user authorization; never store invite codes or credentials in context/evidence."],
        artifact="A sanitized, dated helper context observation or an explicit offline-only decision.",
        done_criteria="The gate is complete only for exact observed true values and matching era/config/vocabulary; no command is run and no campaign is mutated.",
        relevant_check="python scripts/research_next.py --selfcheck",
    )


def _run_registered_action(
    campaign: dict[str, Any], opportunity: dict[str, Any] | None, digests: dict[str, str],
    evidence_refs: list[dict[str, str]], unknowns: list[str]
) -> dict[str, Any]:
    campaign_id = campaign["data"]["campaign_id"]
    direction = opportunity["opportunity_id"] if opportunity else "registered-protocol"
    return _make_action(
        action_id=f"campaign:{campaign_id}:run-preregistered:{direction}",
        kind="run_preregistered_comparison",
        title="Hand off the preregistered matched comparison for its declared horizon",
        reason="The baseline, paper/mechanism/hypothesis/custom-algorithm stages, and preregistration are evidenced, and explicit live prerequisites are recorded. This is a proposal only; the helper never starts the run.",
        source_refs=[
            _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-state-graph"),
            _source_ref("campaign_manifest#preregistration", digests.get("campaign_manifest"), "supplied-preregistered-protocol"),
        ] + (_opportunity_refs(opportunity, digests) if opportunity else []),
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["Future measurement, statistical result, server acceptance, and OOS usefulness are not known."],
        inputs_needed=["Separate harness-owned task/session with a distinct identity; execute only under the exact preregistered scope, budget, seed, and horizon."],
        artifact="Append-only experiments.jsonl trials, checkpoints, and a measured experiment_record.json.",
        done_criteria="Every trial—including failed, negative, and abandoned—is recorded; only evaluator-returned completions count; no depth > 6, filler, transformed duplicates, or weakened gates.",
        relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
    )


def _coverage_cell_action(
    candidate: dict[str, Any] | None,
    coverage: dict[str, Any],
    opportunity: dict[str, Any],
    digests: dict[str, str],
    evidence_refs: list[dict[str, str]],
    unknowns: list[str],
    *,
    live_ready: bool,
) -> dict[str, Any]:
    if candidate is None:
        return _make_action(
            action_id=f"research:reconcile-coverage:{opportunity['opportunity_id']}",
            kind="reconcile_coverage_attribution",
            title="Reconcile loaded-pair attribution and evaluation counts",
            reason="The active proposal is coverage-stratified sampling, but no matching row has a compatible era/config, loaded valid pair, explicit attribution provenance, and a real integer evaluation count.",
            source_refs=_opportunity_refs(opportunity, digests) + _optional_input_source(
                digests, "coverage_ledger", "supplied-read-only-coverage-evidence"
            ),
            evidence_refs=evidence_refs,
            unknowns=unknowns + ["Unknown evaluation counts are not zero; the eligible pair universe remains unknown."],
            inputs_needed=["One non-template pair row with matching era/config, feature_set=loaded, valid_pair=true, attribution plus provenance, and an exact non-negative evaluation_count."],
            artifact="A corrected append-only coverage.jsonl observation or a documented missing-API gap.",
            done_criteria="Deduplicate identical rows, resolve conflicting rows without pooling, and leave invalid/unloaded/unattributed cells out of the eligible coverage count.",
            relevant_check="python .github/scripts/validate_agent_docs.py records",
        )
    pair = f"{candidate['feature']} × {candidate['rotation']}"
    if live_ready:
        kind = "target_undercovered_feature_rotation_pair"
        title = f"Target the observed lowest-count loaded pair: {pair}"
        reason = (
            f"The active coverage proposal has a matching, valid, loaded, explicitly attributed pair with "
            f"{candidate['evaluation_count']} observed evaluation(s), the minimum among known-count matching ledger rows. "
            "This is a scoped coverage action, not an OOS or performance claim."
        )
        inputs = ["Use the registered coverage protocol and exact era/config; keep graph depth <= 6 and count duplicates/rejections separately."]
        artifact = "An append-only coverage.jsonl row/checkpoint for this exact feature × rotation pair."
        done = "Record only observed loaded-valid attributed results; keep unknown-count/conflicting pairs unknown and do not claim the eligible universe is complete."
    else:
        kind = "offline_review_undercovered_feature_rotation_pair"
        title = f"Review the observed lowest-count loaded pair: {pair}"
        reason = (
            f"The matching ledger records {candidate['evaluation_count']} evaluation(s) for this valid loaded pair, "
            "the minimum among known-count matching rows. Live authorization/runtime gates are not all observed, so this is offline protocol review only."
        )
        inputs = ["Check the pair against the current public capability evidence and preregister any measurement before live work."]
        artifact = "A local note linking this pair to the coverage hypothesis and the evidence still required."
        done = "Keep construction descriptors as descriptors, do not infer return correlation, and do not describe the pair as evaluated when its count is unknown."
    return _make_action(
        action_id=f"research:{kind}:{opportunity['opportunity_id']}:{_json_digest([candidate['era_id'], candidate['config_hash'], candidate['feature'], candidate['rotation']])[:12]}",
        kind=kind,
        title=title,
        reason=reason,
        source_refs=_opportunity_refs(opportunity, digests) + _optional_input_source(
            digests, "coverage_ledger", "scoped-observation-not-complete-denominator"
        ),
        evidence_refs=evidence_refs,
        unknowns=unknowns + ["The complete eligible feature × rotation universe and any out-of-sample correlation/usefulness remain unknown."],
        inputs_needed=inputs,
        artifact=artifact,
        done_criteria=done,
        relevant_check="python .github/scripts/validate_agent_docs.py records opportunities",
    )


def _choose_action(
    *,
    opportunities: list[dict[str, Any]],
    campaign: dict[str, Any],
    context: dict[str, Any],
    coverage: dict[str, Any],
    experiments: dict[str, Any],
    comparison: dict[str, Any],
    digests: dict[str, str],
    negative_results: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_state = campaign.get("state") if campaign.get("kind") == "real" else None
    input_aliases = ("campaign_manifest", "coverage_ledger", "experiment_evidence", "context")
    evidence_refs = _input_refs(digests, input_aliases)
    campaign_state_action = _campaign_state_action(campaign, digests, evidence_refs)
    if campaign_state_action is not None:
        return campaign_state_action

    counts = _counts_packet(campaign, coverage, experiments, comparison)
    unknown_paths = _unknown_count_paths(counts)
    unknowns = [f"Count {path} is unknown; null is not zero." for path in unknown_paths]
    baseline = _baseline_state(campaign)
    scope = campaign.get("scope") if campaign.get("kind") == "real" else None
    live_ready, missing_live = _live_gate(context, scope, campaign)
    progress = _research_progress(context, experiments, campaign, opportunities, negative_results)
    opportunity = _choose_opportunity(opportunities, progress)

    if selected_state == "experiment":
        return _reconcile_experiment_action("experiment", campaign, digests, evidence_refs, unknowns)
    if selected_state == "comparison":
        if comparison.get("unprocessed"):
            return _comparison_action(comparison, digests, evidence_refs, unknowns)
        if not comparison.get("ready"):
            return _reconcile_experiment_action("comparison", campaign, digests, evidence_refs, unknowns)

    if comparison.get("unprocessed"):
        if selected_state in {"bootstrapping", "baseline", "research"}:
            return _reconcile_comparison_phase_action(campaign, digests, evidence_refs)
        return _comparison_action(comparison, digests, evidence_refs, unknowns)

    if campaign.get("kind") == "real" and not baseline["ready"]:
        if selected_state in {"bootstrapping", "baseline"} and live_ready:
            return _prepare_baseline_action(campaign, digests, evidence_refs, unknowns)
        baseline_note = (
            f"The observed GA completion count is {baseline['observed']} and remains below the 100000 target."
            if baseline["observed"] is not None and baseline["observed"] < TARGET_COMPLETIONS
            else "A compatible frozen GA baseline is missing, incomplete, or not proven for the recorded era/config."
        )
        blocked = [f"Live baseline proposal is gated on {item}." for item in missing_live]
        return _offline_design_action(
            opportunity,
            digests,
            evidence_refs,
            unknowns + [baseline_note] + blocked,
        )

    if campaign.get("kind") == "real" and selected_state == "baseline":
        return _freeze_baseline_action(campaign, digests, evidence_refs)

    if campaign.get("kind") == "real" and selected_state == "bootstrapping" and baseline["ready"]:
        return _make_action(
            action_id=f"campaign:{campaign['data']['campaign_id']}:record-baseline-reference",
            kind="record_compatible_baseline_reference",
            title="Record the verified compatible baseline reference before research",
            reason="A complete same-scope GA baseline is recorded, while the campaign remains bootstrapping; reconcile the baseline reference and legal state edge before research.",
            source_refs=[
                _source_ref(STATE_MACHINE_REL, digests.get("state_machine"), "authoritative-transition-graph"),
                _source_ref("campaign_manifest#compatible_baseline", digests.get("campaign_manifest"), "supplied-campaign-evidence"),
            ],
            evidence_refs=evidence_refs,
            unknowns=["Ownership claim and transition guard evidence are not independently verified by this helper."],
            inputs_needed=["Verify the baseline id/lineage, selected era/config, and campaign ownership record."],
            artifact="campaign.json#compatible_baseline plus an append-only legal transition.",
            done_criteria="Only transition when the canonical T-research-start guard is evidenced; preserve the original baseline and do not start a worker.",
            relevant_check="python .github/scripts/validate_agent_docs.py records state-machine",
        )

    pending_ablation = progress.get("pending_ablation")
    if campaign.get("kind") == "real" and baseline["ready"] and isinstance(pending_ablation, dict):
        ablation_opportunity = next(
            item for item in opportunities if item["opportunity_id"] == pending_ablation["opportunity_id"]
        )
        return _ablation_action(ablation_opportunity, digests, evidence_refs, unknowns)

    if campaign.get("kind") == "real" and baseline["ready"]:
        stages = _pipeline_stages(campaign)
        if not all(stages.values()):
            missing_stage = next(key for key, complete in stages.items() if not complete)
            return _offline_design_action(
                opportunity,
                digests,
                evidence_refs,
                unknowns,
                stage="custom_algorithm" if missing_stage == "custom_algorithm" else "paper_mechanism_hypothesis",
            )
        if not _preregistration_ready(campaign):
            return _offline_design_action(
                opportunity,
                digests,
                evidence_refs,
                unknowns,
                stage="preregistration",
            )

        coverage_focus = progress.get("active") == "coverage-stratified-sampling"
        if coverage_focus:
            candidates = coverage.get("minimum_candidates", [])
            candidate = candidates[0] if candidates else None
            return _coverage_cell_action(
                candidate,
                coverage,
                opportunity or next(item for item in opportunities if item["opportunity_id"] == "coverage-stratified-sampling"),
                digests,
                evidence_refs,
                unknowns,
                live_ready=live_ready,
            )

        if selected_state == "comparison" and comparison.get("ready"):
            next_progress = dict(progress)
            next_progress["active"] = None
            next_opportunity = _choose_opportunity(opportunities, next_progress)
            return _offline_design_action(
                next_opportunity,
                digests,
                evidence_refs,
                unknowns,
                stage="preregistration",
            )

        if live_ready:
            return _run_registered_action(campaign, opportunity, digests, evidence_refs, unknowns)
        return _live_prerequisites_action(campaign, digests, evidence_refs, missing_live)

    return _offline_design_action(opportunity, digests, evidence_refs, unknowns)


def load_packet(
    root: Path,
    *,
    campaign_path: str | None = None,
    coverage_path: str | None = None,
    experiments_path: str | None = None,
    context_path: str | None = None,
) -> dict[str, Any]:
    """Read the canonical map and optional evidence, then return one action."""
    opportunity_data, state_data, _template, digests = _load_authorities(root)
    campaign = {"kind": "none", "data": {}, "state": None, "scope": None}
    if campaign_path:
        raw_campaign, digests["campaign_manifest"] = _read_json(Path(campaign_path), "--campaign")
        campaign = _validate_campaign(raw_campaign, state_data)

    coverage_rows = None
    if coverage_path:
        coverage_rows, digests["coverage_ledger"] = _read_jsonl(Path(coverage_path), "--coverage")
    coverage = _coverage_analysis(coverage_rows, campaign.get("scope"))

    raw_experiments = None
    if experiments_path:
        raw_experiments, digests["experiment_evidence"] = _read_experiment_records(Path(experiments_path))
    experiments = _experiment_records(raw_experiments)

    context = {"kind": "none", "data": {}, "progress": {}}
    if context_path:
        raw_context, digests["context"] = _read_json(Path(context_path), "--context")
        valid_ids = {item["opportunity_id"] for item in opportunity_data["opportunities"]}
        context = _validate_context(raw_context, valid_ids)
    context_diagnostics = (
        ["context template ignored; its booleans and progress are not observations"]
        if context.get("kind") == "template" else []
    )

    comparison = _comparison_analysis(
        experiments,
        campaign.get("scope"),
        set(context.get("progress", {}).get("analyzed_experiment_ids", [])),
        campaign["data"].get("campaign_id") if campaign.get("kind") == "real" else None,
    )
    experiments["counts"]["independent_paired_runs"] = comparison["counts"]["independent_paired_runs"]
    experiments["counts"]["comparison_records_ready"] = comparison["counts"]["comparison_records_ready"]
    action = _choose_action(
        opportunities=opportunity_data["opportunities"],
        campaign=campaign,
        context=context,
        coverage=coverage,
        experiments=experiments,
        comparison=comparison,
        digests=digests,
        negative_results=opportunity_data.get("negative_results", []),
    )
    counts = _counts_packet(campaign, coverage, experiments, comparison)
    unknown_counts = _unknown_count_paths(counts)
    sources = [
        _source_ref(OPPORTUNITIES_REL, digests["opportunities_map"], "canonical-candidate-map"),
        _source_ref(STATE_MACHINE_REL, digests["state_machine"], "canonical-state-graph"),
        _source_ref(CAMPAIGN_TEMPLATE_REL, digests["campaign_template"], "target-is-not-result"),
    ]
    for alias in ("campaign_manifest", "coverage_ledger", "experiment_evidence", "context"):
        if alias in digests:
            role = "ignored-template-input" if alias == "context" and context.get("kind") == "template" else "supplied-read-only-evidence"
            sources.append(_source_ref(alias, digests[alias], role))
    return {
        "schema_version": 1,
        "decision": "one_next_action",
        "action": action,
        "counts": counts,
        "unknown_counts": unknown_counts,
        "diagnostics": context_diagnostics + coverage.get("diagnostics", []) + experiments.get("diagnostics", []) + comparison.get("diagnostics", []),
        "evidence_sources": sources,
        "read_only": True,
        "executes_or_mutates_campaign": False,
        "is_scheduler_or_task_database": False,
    }


def render_text(packet: dict[str, Any]) -> str:
    action = packet["action"]
    qualitative_unknowns = [item for item in action["unknowns"] if not item.startswith("Count ")]
    lines = [
        f"NEXT ACTION [{action['id']}] {action['kind']}",
        action["title"],
        f"Why: {action['reason']}",
        "",
        f"Done when: {action['done_when']['criteria']} Artifact: {action['done_when']['artifact']}",
        f"Inputs needed: {'; '.join(action['inputs_needed'])}",
        "Unknowns: " + ("; ".join(qualitative_unknowns) if qualitative_unknowns else "no additional qualitative unknowns") + "; unavailable count fields are shown as null [unknown] below.",
        f"Relevant check: {action['relevant_check']}",
        f"Native handoff: {action['native_handoff']}",
        "",
        "Counts (null means unknown, never zero):",
    ]

    def append_counts(values: dict[str, Any], indent: str) -> None:
        for name, value in values.items():
            if isinstance(value, dict) and "value" in value:
                lines.append(f"{indent}{name}: {value['value']!r} [{value['status']}]")
            elif isinstance(value, dict):
                lines.append(f"{indent}{name}:")
                append_counts(value, indent + "  ")

    for group, values in packet["counts"].items():
        lines.append(f"  {group}:")
        append_counts(values, "    ")
    lines.append("Sources/evidence digests:")
    lines.extend(f"  - {item['ref']} sha256={item.get('sha256', 'not-applicable')}" for item in action["source_refs"] + action["evidence_refs"])
    if packet["diagnostics"]:
        lines.append("Evidence diagnostics:")
        lines.extend(f"  - {item}" for item in packet["diagnostics"])
    lines.append("This proposal is read-only; it is not a scheduler, task database, or execution request.")
    return "\n".join(lines) + "\n"


def _fixture_campaign(template: dict[str, Any], *, state: str = "baseline", baseline: str = "missing", stages: bool = False, preregistered: bool = False) -> dict[str, Any]:
    data = json.loads(json.dumps(template))
    data.pop("_template", None)
    data["campaign_id"] = "campaign-selfcheck"
    data["state_machine"]["state"] = state
    data["state_machine"]["resume_target"] = "research" if state in {"paused", "needs-input"} else None
    data["state_machine"]["revision"] = 1
    data["state_machine"]["ownership"].update({
        "owner_session_identity": "child-selfcheck",
        "owner_revision": 1,
        "fencing_token": "fence-selfcheck",
        "claimed_at": "2026-01-01T00:00:00Z",
        "claim_mechanism": "atomic-create",
        "claim_verified": True,
    })
    transition_from = {
        "baseline": "bootstrapping",
        "research": "baseline",
        "experiment": "research",
        "comparison": "experiment",
        "paused": "baseline",
        "needs-input": "baseline",
        "exhausted": "baseline",
        "stopped": "baseline",
        "completed": "comparison",
    }
    transition_id = {
        "baseline": "T-baseline-start",
        "research": "T-baseline-freeze",
        "experiment": "T-protocol-open",
        "comparison": "T-horizon-reached",
        "paused": "T-hold",
        "needs-input": "T-input-needed",
        "exhausted": "T-exhausted",
        "stopped": "T-stop",
        "completed": "T-complete",
    }
    data["transitions"] = [] if state == "bootstrapping" else [{
        "transition_id": transition_id[state],
        "from": transition_from[state],
        "to": state,
        "timestamp": "2026-01-01T00:00:00Z",
        "reason": "synthetic selfcheck transition",
        "owner_session_identity": "child-selfcheck",
        "owner_revision": 1,
        "guard_passed": True,
        **({"resume_target": "research"} if state in {"paused", "needs-input"} else {}),
    }]
    data["frozen_baseline"].update({
        "availability": "unknown",
        "id": None,
        "hash": None,
        "target_completed_simulations": TARGET_COMPLETIONS,
        "observed_completed_simulations": None,
        "frozen": False,
        "algorithm_identity": "genetic",
    })
    data["compatible_baseline"].update({
        "status": "missing",
        "status_reason": "selfcheck fixture baseline is not present",
        "baseline_id": None,
        "config_frozen_before_run": None,
        "evidence_frozen_after_run": None,
    })
    if baseline in {"partial", "full"}:
        data["frozen_baseline"].update({
            "availability": "available",
            "id": "ga-selfcheck",
            "hash": "a" * 64,
            "observed_completed_simulations": 99_999 if baseline == "partial" else TARGET_COMPLETIONS,
            "frozen": True,
        })
        data["compatible_baseline"].update({
            "status": "created",
            "status_reason": None,
            "baseline_id": "compatible-selfcheck",
            "lineage": ["ga-selfcheck"],
            "config_frozen_before_run": True,
            "evidence_frozen_after_run": True,
        })
    scope = {"era_id": "era-selfcheck", "config_hash": "config-selfcheck", "loaded_vocabulary_hash": "vocabulary-selfcheck"}
    data["preregistration"]["data_scope"].update(scope)
    data["provenance"]["data_scope"].update(scope)
    if stages:
        data["pipeline_stages"]["paper"].update({"source": "https://arxiv.org/abs/1504.04909", "claim": "A source claim recorded for a synthetic selfcheck."})
        data["pipeline_stages"]["mechanism"].update({"statement": "Synthetic mechanism fixture.", "falsifier": "Synthetic falsifier fixture."})
        data["pipeline_stages"]["hypothesis"].update({"statement": "Synthetic falsifiable hypothesis.", "comparison": "genetic vs challenger", "threshold": "0.1"})
        data["pipeline_stages"]["custom_algorithm"].update({
            "name": "synthetic-challenger", "depth_cap": 6,
            "self_rejects_depth_gt_6": True, "filler_nodes_added": False,
            "acceptance_thresholds_unchanged": True,
        })
    if preregistered:
        data["preregistration"].update({
            "protocol_id": "protocol-selfcheck", "protocol_version": 1,
            "recorded_at": "2026-01-01T00:00:00Z", "protocol_hash": "b" * 64,
            "recorded_before_results": True, "hypothesis": "Synthetic preregistered hypothesis.",
            "mechanism": "Synthetic mechanism.", "primary_metric": "yield_rate",
            "secondary_metrics": [], "direction": "increase", "minimum_meaningful_effect": 0.1,
            "comparator": "genetic vs synthetic-challenger",
            "seeds_repetitions": {"seed_set_id": "seeds-selfcheck", "search_runs_per_arm": 2, "predeclared": True},
            "evaluation_allocations": {"baseline": 1000, "variant": 1000, "pilots_counted_separately": True},
            "data_scope": {**scope, "history_window": "window-selfcheck"},
            "exclusions": [], "look_schedule": {"kind": "fixed_horizon", "looks": 1},
            "decision_criteria": "Synthetic decision criterion.", "stopping_criteria": "Synthetic stopping rule.",
            "holdout_handling": "No held-out data is assumed.",
        })
    return data


def _fixture_context(*, live: bool = False, progress: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "authorization": {
            "explicit_live_authorization": live,
            "evidence_ref": "selfcheck-only authorization fixture" if live else None,
        },
        "observations": {
            "live_access_available": live,
            "package_installed": live,
            "genetic_callable_observed": live,
            "separate_same_harness_cli_observed": live,
            "distinct_session_identity_observed": live,
            "source_ref": "selfcheck-only runtime fixture" if live else None,
            "observed_at": "2026-01-01T00:00:00Z" if live else None,
        },
        "scope_observation": {
            "era_id": "era-selfcheck", "config_hash": "config-selfcheck",
            "loaded_vocabulary_hash": "vocabulary-selfcheck",
            "source_ref": "selfcheck-only scope fixture", "observed_at": "2026-01-01T00:00:00Z",
        },
        "research_progress": progress or {},
    }


def _fixture_experiment(*, measured: bool = False, negative: bool = False) -> dict[str, Any]:
    record = {
        "record_type": "experiment_record",
        "experiment_id": "experiment-selfcheck",
        "campaign_id": "campaign-selfcheck",
        "opportunity_id": "coverage-stratified-sampling",
        "pipeline_stage": "measured_comparison" if measured else "paper",
        "preregistration_ref": {"recorded_before_results": measured, "protocol_hash": "c" * 64 if measured else None},
        "paper": {"source": "https://arxiv.org/abs/1504.04909", "exact_claim": "Synthetic citation fixture."},
        "negative_findings": [{"finding": "Synthetic negative finding."}] if negative else [],
    }
    if measured:
        record.update({
            "comparison": {
                "baseline": {"algorithm": "genetic", "algorithm_identity": "genetic-selfcheck", "budget_evaluations": 1000, "completed_evaluator_simulations": 987, "seed_set_id": "seeds-selfcheck", "config_hash": "config-selfcheck"},
                "variant": {"algorithm": "synthetic-challenger", "budget_evaluations": 1000, "completed_evaluator_simulations": 987, "seed_set_id": "seeds-selfcheck", "config_hash": "config-selfcheck"},
                "budgets_matched": True, "seeds_matched": True, "effect_size": 0.02, "uncertainty": {"lower": -0.01, "upper": 0.05},
            },
            "statistics": {"unit_of_analysis": "independent_paired_search_runs", "search_runs_baseline": 2, "search_runs_variant": 2},
            "provenance": {"data_scope": {"era_id": "era-selfcheck", "config_hash": "config-selfcheck"}},
        })
    return record


def _fixture_pair(
    feature: str,
    rotation: str,
    *,
    count: Any = 0,
    era: str = "era-selfcheck",
    config: str = "config-selfcheck",
    feature_set: str = "loaded",
    valid: bool = True,
    attribution: str = "analytics",
    provenance: str = "selfcheck analytics row",
) -> dict[str, Any]:
    return {
        "record_type": "feature_rotation_pair", "era_id": era, "config_hash": config,
        "feature_set": feature_set, "feature": feature, "rotation": rotation,
        "valid_pair": valid, "evaluated": count > 0 if type(count) is int else False,
        "evaluation_count": count, "dependency_of_passing_graph": False,
        "canonical_graph_id": None, "duplicate_of": None, "pair_attribution": attribution,
        "attribution_provenance": provenance, "filler_or_transformed_duplicate": False,
        "depth_cap": 6,
    }


def _fixture_trial(run_number: int) -> dict[str, Any]:
    return {
        "record_type": "experiment_trial",
        "trial_id": f"trial-selfcheck-{run_number}",
        "experiment_id": "trial-inventory-selfcheck",
        "campaign_id": "campaign-selfcheck",
        "preregistration_protocol_id": "protocol-selfcheck",
        "pipeline_stages_complete": ["paper", "mechanism", "hypothesis", "custom_algorithm", "measured_comparison"],
        "classification": "exploratory",
        "status": "completed",
        "unit_of_analysis": "independent_paired_search_runs",
        "search_run_id": f"paired-search-selfcheck-{run_number}",
        "baseline": {
            "algorithm": "genetic", "algorithm_identity": "genetic-selfcheck",
            "budget_evaluations": 1000, "completed_evaluator_simulations": 800,
            "seed_set_id": "seeds-selfcheck", "config_hash": "config-selfcheck",
        },
        "variant": {
            "algorithm": "synthetic-challenger", "budget_evaluations": 1000,
            "completed_evaluator_simulations": 800,
            "seed_set_id": "seeds-selfcheck", "config_hash": "config-selfcheck",
        },
        "budgets_matched": True,
        "seeds_matched": True,
        "era_segment": "era-selfcheck",
        "config_segment": "config-selfcheck",
        "result": {"effect_size": 0.01 * run_number, "uncertainty": {"lower": -0.02, "upper": 0.04}, "threshold_cleared": False},
    }


def run_selfcheck(root: Path) -> int:
    """Run focused, synthetic regressions without package, network, or worker use."""
    import tempfile

    failures: list[str] = []
    try:
        _, _, template, _ = _load_authorities(root)
    except NextActionError as exc:
        print(f"FAIL: research_next selfcheck cannot load canonical inputs: {exc}")
        return 1

    def check(name: str, passed: bool, detail: str = "") -> None:
        if not passed:
            failures.append(f"{name}: {detail or 'assertion failed'}")

    with tempfile.TemporaryDirectory(prefix="research-next-selfcheck-") as temporary:
        temp = Path(temporary)

        def write(name: str, value: Any, *, jsonl: bool = False) -> str:
            path = temp / (name + (".jsonl" if jsonl else ".json"))
            if jsonl:
                path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in value), encoding="utf-8")
            else:
                path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
            return str(path)

        def packet(
            *, campaign: Any = None, coverage: list[dict[str, Any]] | None = None,
            experiments: Any = None, context: Any = None,
        ) -> dict[str, Any]:
            kwargs: dict[str, str] = {}
            if campaign is not None:
                kwargs["campaign_path"] = write("campaign", campaign)
            if coverage is not None:
                kwargs["coverage_path"] = write("coverage", coverage, jsonl=True)
            if experiments is not None:
                suffix = "jsonl" if isinstance(experiments, list) else "json"
                if suffix == "jsonl":
                    kwargs["experiments_path"] = write("experiments", experiments, jsonl=True)
                else:
                    kwargs["experiments_path"] = write("experiment", experiments)
            if context is not None:
                kwargs["context_path"] = write("context", context)
            return load_packet(root, **kwargs)

        try:
            first = packet()
            second = packet()
            check("empty-context offline route", first["action"]["kind"] == "offline_paper_mechanism_design")
            check("stable action id", first["action"]["id"] == second["action"]["id"])
            check("unknowns stay null", first["counts"]["baseline"]["observed_completed_simulations"]["value"] is None)
            check("unknown denominator stays null", first["counts"]["coverage"]["eligible_pair_universe"]["value"] is None)

            template_only = load_packet(
                root,
                campaign_path=str(root / CAMPAIGN_TEMPLATE_REL),
                coverage_path=str(root / "examples/agent-campaign/coverage.jsonl"),
                experiments_path=str(root / "examples/agent-campaign/experiment_record.json"),
                context_path=str(root / "examples/agent-campaign/next-action/context-template.json"),
            )
            check("templates ignored", template_only["action"]["kind"] == "offline_paper_mechanism_design")
            check("template counters not observations", template_only["counts"]["baseline"]["observed_completed_simulations"]["value"] is None)

            missing = _fixture_campaign(template, baseline="missing")
            missing_offline = packet(campaign=missing)
            check("missing baseline stays offline", missing_offline["action"]["kind"] == "offline_paper_mechanism_design")
            authorized_context = _fixture_context(live=True)
            missing_live = packet(campaign=missing, context=authorized_context)
            check("authorized missing baseline proposal", missing_live["action"]["kind"] == "prepare_genetic_baseline")
            unverified_owner = _fixture_campaign(template)
            unverified_owner["state_machine"]["ownership"]["claim_verified"] = False
            owner_gate = packet(campaign=unverified_owner, context=authorized_context)
            check("unverified owner cannot enable baseline proposal", owner_gate["action"]["kind"] == "offline_paper_mechanism_design")
            partial = _fixture_campaign(template, baseline="partial")
            partial_packet = packet(campaign=partial, context=authorized_context)
            check("partial baseline remains incomplete", partial_packet["action"]["kind"] == "prepare_genetic_baseline")
            check("partial count is measured not rounded", partial_packet["counts"]["baseline"]["observed_completed_simulations"]["value"] == 99_999)

            full_incomplete = _fixture_campaign(template, state="research", baseline="full", stages=True)
            prereg_packet = packet(campaign=full_incomplete)
            check("full baseline advances to preregistration", prereg_packet["action"]["kind"] == "preregister_paper_backed_challenger")

            negative_record = _fixture_experiment(negative=True)
            negative_packet = packet(experiments=negative_record)
            check("negative recorded approach advances paper choice", "diversity-archive" in negative_packet["action"]["id"])
            completed_record = _fixture_experiment()
            completed_packet = packet(experiments=completed_record)
            check("completed paper approach advances paper choice", "diversity-archive" in completed_packet["action"]["id"], completed_packet["action"]["id"])

            progress = {"negative_approach_ids": ["coverage-stratified-sampling"]}
            progressed = packet(context={"schema_version": 1, "research_progress": progress})
            check("context progress advances paper choice", "diversity-archive" in progressed["action"]["id"])
        except Exception as exc:  # noqa: BLE001
            failures.append(f"offline/baseline/progress scenarios raised {type(exc).__name__}: {exc}")

        try:
            ready_campaign = _fixture_campaign(template, state="research", baseline="full", stages=True, preregistered=True)
            coverage_context = _fixture_context(
                live=True,
                progress={"active_opportunity_id": "coverage-stratified-sampling"},
            )
            cold = _fixture_pair("feature-cold", "rotation-a", count=0)
            covered = _fixture_pair("feature-covered", "rotation-b", count=4)
            uncertain = _fixture_pair("feature-unknown", "rotation-c", count=None)
            boolean_count = _fixture_pair("feature-bool", "rotation-d", count=True)
            conflicted_a = _fixture_pair("feature-conflict", "rotation-e", count=0)
            conflicted_b = _fixture_pair("feature-conflict", "rotation-e", count=2)
            missing_duplicate_provenance = _fixture_pair("feature-duplicate-unknown", "rotation-f", count=0)
            missing_duplicate_provenance.pop("duplicate_of")
            mixed_coverage = [
                cold, json.loads(json.dumps(cold)), covered, uncertain, boolean_count,
                conflicted_a, conflicted_b,
                missing_duplicate_provenance,
                _fixture_pair("feature-other-era", "rotation-a", era="era-other"),
                _fixture_pair("feature-other-config", "rotation-a", config="config-other"),
                _fixture_pair("feature-catalogue", "rotation-a", feature_set="catalogue"),
                _fixture_pair("feature-invalid", "rotation-a", valid=False),
                _fixture_pair("feature-unattributed", "rotation-a", attribution="missing_api_gap", provenance="<missing>"),
                {"_template": True, **cold},
            ]
            selected_cell = packet(campaign=ready_campaign, coverage=mixed_coverage, context=coverage_context)
            action = selected_cell["action"]
            check("valid observed coverage cell selected", action["kind"] == "target_undercovered_feature_rotation_pair")
            check("unknown count not selected", "feature-cold" in action["title"] and "feature-unknown" not in action["title"])
            check("coverage duplicates deduplicated", selected_cell["counts"]["coverage"]["duplicate_rows"]["value"] == 1)
            check("coverage conflicts excluded", selected_cell["counts"]["coverage"]["conflicting_pair_keys"]["value"] == 1)
            check("coverage denominator remains unknown", selected_cell["counts"]["coverage"]["eligible_pair_universe"]["value"] is None)

            offline_cell = packet(
                campaign=ready_campaign,
                coverage=[cold],
                context=_fixture_context(progress={"active_opportunity_id": "coverage-stratified-sampling"}),
            )
            check("coverage without live gates stays offline", offline_cell["action"]["kind"] == "offline_review_undercovered_feature_rotation_pair")
            unknown_cell = packet(
                campaign=ready_campaign,
                coverage=[uncertain, boolean_count],
                context=coverage_context,
            )
            check("unknown/bool coverage counts do not select zero", unknown_cell["action"]["kind"] == "reconcile_coverage_attribution")
            check("unknown coverage counts are explicit", unknown_cell["counts"]["coverage"]["unknown_evaluation_count_pairs"]["value"] == 2)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"coverage eligibility scenarios raised {type(exc).__name__}: {exc}")

        try:
            full_comparison_campaign = _fixture_campaign(
                template, state="comparison", baseline="full", stages=True, preregistered=True
            )
            measured = packet(campaign=full_comparison_campaign, experiments=_fixture_experiment(measured=True))
            check("independent-run evidence enables analysis action", measured["action"]["kind"] == "analyze_independent_run_comparison")
            check("budget is not substituted for completed count", measured["counts"]["experiments"]["completed_evaluator_simulations_per_arm"]["baseline"]["value"] == 987)
            wrong_phase = packet(
                campaign=_fixture_campaign(template, state="research", baseline="full", stages=True, preregistered=True),
                experiments=_fixture_experiment(measured=True),
            )
            check("measured evidence respects campaign phase", wrong_phase["action"]["kind"] == "reconcile_measured_comparison_phase")
            other_campaign_record = _fixture_experiment(measured=True)
            other_campaign_record["campaign_id"] = "campaign-other"
            isolated_campaign = packet(campaign=full_comparison_campaign, experiments=other_campaign_record)
            check("experiment evidence does not cross campaign ids", isolated_campaign["counts"]["experiments"]["comparison_records_ready"]["value"] == 0)
            unmeasured_record = _fixture_experiment(measured=True)
            unmeasured_record["comparison"]["effect_size"] = None
            unmeasured = packet(campaign=full_comparison_campaign, experiments=unmeasured_record)
            check("unknown measurements do not enable conclusion", unmeasured["action"]["kind"] == "reconcile_comparison_evidence")
            unequal_completion = _fixture_experiment(measured=True)
            unequal_completion["comparison"]["variant"]["completed_evaluator_simulations"] = 986
            unequal_packet = packet(campaign=full_comparison_campaign, experiments=unequal_completion)
            check("unmatched completed counts do not enable analysis", unequal_packet["action"]["kind"] == "reconcile_comparison_evidence")
            bool_runs = _fixture_experiment(measured=True)
            bool_runs["statistics"]["search_runs_baseline"] = True
            bool_runs["statistics"]["search_runs_variant"] = True
            bool_packet = packet(campaign=full_comparison_campaign, experiments=bool_runs)
            check("boolean run counters are not observations", bool_packet["counts"]["experiments"]["independent_paired_runs"]["value"] is None)
            trial_inventory = packet(experiments=[_fixture_trial(1), _fixture_trial(2)])
            check("separate completed paired runs support analysis", trial_inventory["action"]["kind"] == "analyze_independent_run_comparison")
            check("trial completions stay separate from budget", trial_inventory["counts"]["experiments"]["completed_evaluator_simulations_per_arm"]["baseline"]["value"] == 1600)
            split_segment = _fixture_trial(2)
            split_segment["era_segment"] = "era-other"
            split_segment["config_segment"] = "config-other"
            split_segment["baseline"]["config_hash"] = "config-other"
            split_segment["variant"]["config_hash"] = "config-other"
            isolated = packet(experiments=[_fixture_trial(1), split_segment])
            check("independent runs do not pool across era/config", isolated["counts"]["experiments"]["comparison_records_ready"]["value"] == 0)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"experiment measurement scenarios raised {type(exc).__name__}: {exc}")

        try:
            ready_research = _fixture_campaign(template, state="research", baseline="full")
            ablation_context = _fixture_context(progress={
                "pending_ablation": {
                    "opportunity_id": "novelty-pressure-archive",
                    "basis_ref": "experiment:recorded-selfcheck",
                    "component": "archive novelty weighting",
                }
            })
            ablation = packet(campaign=ready_research, context=ablation_context)
            check("recorded ablation choice", ablation["action"]["kind"] == "design_ablation")

            paused = packet(campaign=_fixture_campaign(template, state="paused"))
            blocked = packet(campaign=_fixture_campaign(template, state="needs-input"))
            stopped = packet(campaign=_fixture_campaign(template, state="stopped"))
            completed = packet(campaign=_fixture_campaign(template, state="completed"))
            exhausted = packet(campaign=_fixture_campaign(template, state="exhausted"))
            check("paused returns reconciliation", paused["action"]["kind"] == "reconcile_paused_campaign")
            check("needs-input returns user-input task", blocked["action"]["kind"] == "resolve_campaign_input")
            check("stopped is terminal", stopped["action"]["kind"] == "verify_terminal_report_no_relaunch")
            check("completed is terminal", completed["action"]["kind"] == "verify_terminal_report_no_relaunch")
            check("exhausted is terminal", exhausted["action"]["kind"] == "verify_terminal_report_no_relaunch")
            check("terminal handoff forbids relaunch", "do not reactivate" in completed["action"]["done_when"]["criteria"])
            terminal_with_results = packet(
                campaign=_fixture_campaign(template, state="stopped"),
                experiments=_fixture_experiment(measured=True),
                context=_fixture_context(live=True),
            )
            check("terminal state preempts measured evidence", terminal_with_results["action"]["kind"] == "verify_terminal_report_no_relaunch")

            malformed = _fixture_campaign(template)
            malformed.pop("state_machine")
            try:
                packet(campaign=malformed)
                check("malformed manifest fails clearly", False, "missing state_machine was accepted")
            except NextActionError as exc:
                check("malformed manifest fails clearly", "state_machine" in str(exc))
            boolean_count_campaign = _fixture_campaign(template)
            boolean_count_campaign["frozen_baseline"]["observed_completed_simulations"] = True
            try:
                packet(campaign=boolean_count_campaign)
                check("boolean baseline count rejected", False, "boolean was treated as a count")
            except NextActionError as exc:
                check("boolean baseline count rejected", "bool is not a count" in str(exc))

            invalid_json = temp / "invalid-campaign.json"
            invalid_json.write_text("{not-json", encoding="utf-8")
            try:
                load_packet(root, campaign_path=str(invalid_json))
                check("malformed JSON fails clearly", False, "invalid JSON was accepted")
            except NextActionError as exc:
                check("malformed JSON fails clearly", "--campaign" in str(exc) and "invalid JSON" in str(exc))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ablation/state/malformed scenarios raised {type(exc).__name__}: {exc}")

    if failures:
        for failure in failures:
            print(f"FAIL: research_next selfcheck: {failure}")
        print(f"\n{len(failures)} research_next selfcheck failure(s)")
        return 1
    print("note: research_next selfcheck: offline/template, baseline gates, opportunity progress, independent-run evidence, coverage eligibility, ablation, hold/terminal, and malformed-input scenarios passed")
    print("ok")
    return 0
