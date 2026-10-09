#!/usr/bin/env python3
"""Print a portable OpenForage onboarding packet or explicitly generate its docs.

The default invocation is read-only and prints the offline research route.
Use ``--task`` for a specific user request and ``--harness`` only when the
product was explicitly identified. No environment hint is used to guess it.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

_SCRIPT_PATH = Path(__file__).absolute()
_ROOT_PATH = _SCRIPT_PATH.parent.parent
_LOCAL_ONBOARDING_SOURCES = (
    "scripts/onboarding/__init__.py",
    "scripts/onboarding/entry_fragments.py",
    "scripts/onboarding/capabilities.py",
    "scripts/onboarding/completion.py",
)
_unsafe_import = _SCRIPT_PATH.is_symlink() or _SCRIPT_PATH.parent.is_symlink() or _ROOT_PATH.is_symlink()
for _relative_source in _LOCAL_ONBOARDING_SOURCES:
    _current = _ROOT_PATH
    for _part in Path(_relative_source).parts:
        _current = _current / _part
        if _current.is_symlink():
            _unsafe_import = True
            break
if _unsafe_import:
    print("FAIL: startup helper or onboarding implementation path is a symlink; no completion check was run", file=sys.stderr)
    raise SystemExit(2)

from onboarding.entry_fragments import (
    ENTRY_FRAGMENT_OUTPUTS,
    render_entry_fragment,
    replace_entry_fragment,
)
from onboarding.capabilities import (
    PROJECTION_BEGIN_MARKER,
    PROJECTION_END_MARKER,
    CapabilityProjectionError,
    project_capability_records,
    render_capability_projection,
    replace_capability_projection,
)
from onboarding.completion import (
    CompletionError,
    check_completion,
    fixture_ids,
    load_fixture_contract,
    parse_route_helper_argv,
    read_json_safe,
    render_completion_text,
    render_finish_commands,
    selected_input_paths,
    selfcheck_problems as completion_selfcheck_problems,
    validate_series_manifest_path,
)

ROOT = _ROOT_PATH.resolve()
ONBOARDING_PATH = "docs/agents/references/onboarding.json"
BEGIN_MARKER = "<!-- BEGIN GENERATED START-HERE ROUTE -->"
END_MARKER = "<!-- END GENERATED START-HERE ROUTE -->"
GENERATED_OUTPUTS = {
    "readme": "README.md",
    "agents": "AGENTS.md",
    "claude": "CLAUDE.md",
    "portable_packet": "docs/agents/start-here.md",
}
AVAILABILITY_LABELS = {"observed", "documented", "unknown"}


class OnboardingError(RuntimeError):
    """Invalid or incomplete canonical onboarding data."""


def _load_json(root: Path, relpath: str, *, required: bool = True) -> Any:
    try:
        return read_json_safe(root, relpath)
    except (CompletionError, OSError, json.JSONDecodeError) as exc:
        if required:
            raise OnboardingError(f"cannot read {relpath}: {exc}") from exc
        return None


def _read_sources(root: Path, onboarding: dict) -> dict[str, Any]:
    paths = onboarding.get("source_paths", {})
    names = {
        "compatibility": paths.get("compatibility"),
        "harness_registry": paths.get("harness_registry"),
        "research_opportunities": paths.get("research_opportunities"),
        "campaign_template": paths.get("campaign_template"),
    }
    return {
        key: _load_json(root, path, required=False) if isinstance(path, str) else None
        for key, path in names.items()
    }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _safe_harness_label(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", value):
        return value
    return "<unrecognized harness identifier>"


def _registry_entries(registry: Any) -> list[dict]:
    return [entry for entry in registry if isinstance(entry, dict)] if isinstance(registry, list) else []


def _match_harness(requested: str | None, entries: list[dict]) -> dict | None:
    if requested is None:
        return None
    wanted = _slug(requested.strip())
    if not wanted:
        return None
    for entry in entries:
        product = entry.get("product")
        route = entry.get("route")
        route_id = Path(route).stem if isinstance(route, str) else ""
        if wanted in {_slug(product), _slug(route_id)}:
            return entry
    return None


def _record_state(
    records: list[dict[str, Any]],
    capability: str,
    *,
    mechanisms: set[str] | None = None,
) -> tuple[str, str]:
    matches = [
        record
        for record in records
        if record.get("capability") == capability
        and (mechanisms is None or record.get("mechanism") in mechanisms)
    ]
    verified = [record for record in matches if record.get("availability") == "observed"]
    if verified:
        return (
            "observed",
            f"{len(verified)} scoped record(s) exist; versions, profiles, probes, and mechanisms remain separate. "
            "The JSON packet lists their exact scopes. This does not imply a launch.",
        )
    if matches:
        reasons = [
            record.get("unknown_reason")
            for record in matches
            if isinstance(record.get("unknown_reason"), str) and record["unknown_reason"].strip()
        ]
        detail = reasons[0] if reasons else "The canonical scoped records do not verify this capability."
        return "unknown", str(detail)
    return "unknown", "No matching verified scoped capability record exists."


def _documented_evidence(entry: dict | None, field: str) -> tuple[str, str]:
    if not entry:
        return "unknown", "No harness was explicitly named; runtime identity was not inferred."
    evidence = entry.get(field)
    if not isinstance(evidence, str) or not evidence.strip():
        return "unknown", "The harness registry contains no evidence for this capability."
    text = evidence.strip()
    lower = text.casefold()
    unsupported_markers = (
        "not documented",
        "unverified:",
        "unresolved:",
        "not established",
        "no adapter",
        "does not document",
        "does not provide",
        "none recorded",
        "unknown pending",
    )
    if any(marker in lower for marker in unsupported_markers):
        return "unknown", text
    if entry.get("status") == "documented-unverified":
        return "documented", text
    if text.startswith(("Docs:", "Upstream README", "Documented")):
        return "documented", text
    return "unknown", text


def _capability(
    name: str,
    availability: str,
    evidence: str,
    source: str,
) -> dict[str, str]:
    if availability not in AVAILABILITY_LABELS:
        availability = "unknown"
    return {
        "name": name,
        "availability": availability,
        "evidence": evidence,
        "source": source,
    }


def _package_observation(compatibility: dict | None) -> dict[str, str]:
    compat_path = "docs/agents/references/compatibility.json"
    try:
        version = importlib.metadata.version("openforage")
    except importlib.metadata.PackageNotFoundError:
        fact = "The openforage distribution is not installed in the current Python environment."
    except Exception as exc:  # metadata can fail on a damaged local environment
        return _capability(
            "OpenForage package installation",
            "unknown",
            f"Local package metadata could not be read ({type(exc).__name__}); no install was attempted.",
            compat_path,
        )
    else:
        fact = f"The openforage distribution is installed (version {version}); behavior is not probed by this packet."
    if compatibility is None:
        fact += " Compatibility expectations could not be read from the repository."
    return _capability("OpenForage package installation", "observed", fact, compat_path)


def _runtime_capabilities(
    root: Path,
    entry: dict | None,
    registry_path: str,
    *,
    explicitly_named: bool,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not explicitly_named:
        unknown = "No harness was explicitly named; no environment or process hint was inspected."
        caps = [
            _capability("Separate same-harness CLI invocation", "unknown", unknown, registry_path),
            _capability("Distinct session identity", "unknown", unknown, registry_path),
            _capability("Native scheduler", "unknown", unknown, registry_path),
            _capability("Native goals", "unknown", unknown, registry_path),
            _capability("Native task store", "unknown", unknown, registry_path),
            _capability("Native memory", "unknown", unknown, registry_path),
        ]
        return caps, {
            "matched_product": None,
            "registry_status": "unknown",
            "registry_availability": "unknown",
            "route": "references/unsupported.md",
            "explicitly_named": False,
            "capability_projection_version": 1,
            "capability_records": [],
        }

    if entry is None:
        unknown = "This identifier is not in the registry. Follow the generic discovery route; do not infer commands."
        caps = [
            _capability(name, "unknown", unknown, registry_path)
            for name in (
                "Separate same-harness CLI invocation",
                "Distinct session identity",
                "Native scheduler",
                "Native goals",
                "Native task store",
                "Native memory",
            )
        ]
        return caps, {
            "matched_product": None,
            "registry_status": "unknown",
            "registry_availability": "unknown",
            "route": "references/unsupported.md",
            "explicitly_named": True,
            "capability_projection_version": 1,
            "capability_records": [],
        }

    try:
        records = project_capability_records(root, entry)
    except CapabilityProjectionError as exc:
        raise OnboardingError(f"invalid capability records for {entry.get('product')}: {exc}") from exc

    caps: list[dict[str, str]] = []
    process_label, process_detail = _record_state(records, "separate-cli-invocation")
    identity_label, identity_detail = _record_state(records, "distinct-session-identity")
    for name, observed_label, observed_detail, evidence_field in (
        (
            "Separate same-harness CLI invocation",
            process_label,
            process_detail,
            "separate_session_evidence",
        ),
        ("Distinct session identity", identity_label, identity_detail, "separate_session_evidence"),
    ):
        if observed_label == "observed":
            label, detail = observed_label, observed_detail
        else:
            label, detail = _documented_evidence(entry, evidence_field)
            if name == "Separate same-harness CLI invocation":
                evidence_text = str(entry.get(evidence_field, "")).casefold()
                cli_process_documented = any(
                    phrase in evidence_text
                    for phrase in ("separate cli invocation", "separate cli process", "new cli process", "fresh cli process")
                )
                if label == "documented" and not cli_process_documented:
                    label = "unknown"
                    detail = (
                        "The registry documents session or scheduler surfaces, but not a separate same-harness CLI process. "
                        "Do not infer continuous-search support."
                    )
        caps.append(_capability(name, label, detail, registry_path))

    for display, capability, mechanisms in (
        ("Parent independence", "parent-independence", None),
        ("Exact-child continuation", "resume", None),
        (
            "External controller recurrence",
            "scheduler-wakeup-and-consumption",
            {"external-background-controller", "external-controller", "external-bridge", "external-finite-driver"},
        ),
        ("External controller pause", "pause", {"external-background-controller", "external-controller"}),
        ("External controller stop", "stop", {"external-background-controller", "external-controller"}),
        ("External controller recovery", "crash-recovery", {"external-background-controller", "external-controller"}),
        ("External exact-target control", "exact-target-control", {"external-background-controller", "external-controller"}),
        ("External terminal-trigger stop", "terminal-triggers-stopped", {"external-background-controller", "external-controller"}),
    ):
        label, detail = _record_state(records, capability, mechanisms=mechanisms)
        caps.append(_capability(display, label, detail, registry_path))

    for display, capability in (
        ("Native goals", "native-goal-operation"),
        ("Native task tool operation", "native-task-tool-operation"),
        ("Native memory", "native-memory-operation"),
        ("Native scheduler", "native-scheduler"),
        ("Native task store", "native-task-store"),
    ):
        label, detail = _record_state(records, capability)
        if capability == "native-task-tool-operation" and label == "observed":
            detail += " Only the explicit native task-tool operation is observed; it does not certify a durable task store."
        if capability == "native-scheduler" and label != "observed":
            external = _record_state(
                records,
                "scheduler-wakeup-and-consumption",
                mechanisms={"external-background-controller", "external-controller"},
            )[0]
            if external == "observed":
                detail = "External due-time controller records do not establish native scheduler state."
        if capability == "native-task-store" and label != "observed":
            task_tool = _record_state(records, "native-task-tool-operation")[0]
            if task_tool == "observed":
                detail = "A task tool event does not establish a durable native task store."
        caps.append(_capability(display, label, detail, registry_path))

    route = entry.get("route")
    return caps, {
        "matched_product": entry.get("product"),
        "registry_status": entry.get("status", "unknown"),
        "registry_availability": (
            "documented" if entry.get("status") == "documented-unverified" else "unknown"
        ),
        "route": route if isinstance(route, str) else "references/unsupported.md",
        "explicitly_named": True,
        "capability_projection_version": 1,
        "capability_records": records,
    }


def _reference_goal(sources: dict[str, Any], paths: dict[str, str]) -> dict[str, Any] | None:
    compatibility = sources.get("compatibility")
    template = sources.get("campaign_template")
    if not isinstance(compatibility, dict) or not isinstance(template, dict):
        return None
    try:
        algorithm = compatibility["expected_surface"]["cli"]["documented_default_algorithm"]["value"]
        target = template["frozen_baseline"]["target_completed_simulations"]
    except (KeyError, TypeError):
        return None
    if not isinstance(algorithm, str) or not isinstance(target, int) or isinstance(target, bool):
        return None
    return {
        "algorithm_identity": algorithm,
        "target_completed_simulations": target,
        "status": "target only; no measured result is implied",
        "source_paths": [paths.get("campaign_template"), paths.get("compatibility")],
    }


def _research_map_summary(sources: dict[str, Any], paths: dict[str, str]) -> dict[str, Any]:
    research_map = sources.get("research_opportunities")
    map_path = paths.get("research_opportunities", "docs/agents/research-opportunities.json")
    opportunities = research_map.get("opportunities") if isinstance(research_map, dict) else None
    if not isinstance(opportunities, list):
        return {
            "availability": "unknown",
            "proposal_count": None,
            "path": map_path,
            "note": "The map could not be read; report the missing source and do not infer its contents.",
        }
    return {
        "availability": "observed",
        "proposal_count": len(opportunities),
        "path": map_path,
        "note": "Map entries are proposals, not results or superiority claims.",
    }


def _fact_paths(task: dict, paths: dict[str, str]) -> list[str]:
    keys = task.get("fact_sources", [])
    result = [ONBOARDING_PATH]
    for key in keys if isinstance(keys, list) else []:
        value = paths.get(key)
        if isinstance(value, str) and value not in result:
            result.append(value)
    for key in ("harness_registry", "compatibility"):
        value = paths.get(key)
        if isinstance(value, str) and value not in result:
            result.append(value)
    return result


def _fixture_immediate_action(contract: dict[str, Any]) -> str:
    sequence = " → ".join(f"`{step}`" for step in contract["finish_sequence"])
    return (
        "First read the declared task guidance and available inputs, including any disclosed primary-source excerpt, "
        "before writing a deliverable. Then follow the fixture finish sequence in this exact order: "
        f"{sequence}."
    )


def _fixture_verification_commands(contract: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "command": item["command"],
            "when": f"Run as `{item['check_id']}` in the fixture's required finish sequence.",
        }
        for item in contract["permitted_finish_commands"]
    ]


def _fixture_completion_criteria(contract: dict[str, Any]) -> list[str]:
    deliverables = ", ".join(f"`{path}`" for path in contract["deliverables"])
    scratch = ", ".join(f"`{path}`" for path in contract["scratch_paths"])
    sequence = " → ".join(f"`{step}`" for step in contract["finish_sequence"])
    check_ids = ", ".join(f"`{check_id}`" for check_id in contract["fixed_finish_check_ids"])
    criteria = [
        "Use only the available inputs declared in this fixture contract.",
        f"Produce all declared deliverables: {deliverables}.",
        f"Keep optional scratch within the declared path(s): {scratch}; scratch is not a deliverable.",
        f"Complete the required finish sequence in this exact order: {sequence}.",
        f"Run each fixed finish check using its paired permitted command: {check_ids}.",
    ]
    if contract["finish_sequence"]:
        final_step = contract["finish_sequence"][-1]
        if final_step in contract["fixed_finish_check_ids"]:
            criteria.append(f"Pass `{final_step}` as the final read-only completion check.")
    return criteria


def _registry_route_path(registry_path: str, route: str) -> str:
    registry_dir = registry_path.rsplit("/", 1)[0]
    skill_dir = registry_dir.rsplit("/", 1)[0]
    if route.startswith("references/"):
        return f"{skill_dir}/{route}"
    return f"{registry_dir}/{route}"


def build_packet(
    root: Path,
    task_id: str | None = None,
    harness_name: str | None = None,
    *,
    fixture_id: str | None = None,
    series_manifest: str | None = None,
    package_observation: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build packet data using canonical route files and local metadata only."""
    onboarding = _load_json(root, ONBOARDING_PATH)
    if not isinstance(onboarding, dict):
        raise OnboardingError(f"{ONBOARDING_PATH} must contain a JSON object")
    selected = task_id or onboarding.get("default_task")
    tasks = onboarding.get("tasks")
    if not isinstance(tasks, dict) or selected not in tasks:
        raise OnboardingError(f"unknown onboarding task {selected!r}")
    task = tasks[selected]
    if not isinstance(task, dict):
        raise OnboardingError(f"task {selected!r} must contain an object")
    if series_manifest is not None and fixture_id is None:
        raise OnboardingError("--series-manifest requires an explicitly selected --fixture")
    fixture_projection = None
    if fixture_id is not None:
        try:
            contract = load_fixture_contract(root, selected, fixture_id)
            available_inputs = selected_input_paths(contract, selected, series_manifest)
            if selected == "research" and series_manifest is not None:
                validate_series_manifest_path(selected, series_manifest)
            fixture_projection = {
                "id": fixture_id,
                "task": selected,
                "contract_version": contract["contract_version"],
                "available_input_paths": available_inputs,
                "deliverables": list(contract["deliverables"]),
                "scratch_paths": list(contract["scratch_paths"]),
                "fixed_finish_check_ids": list(contract["fixed_finish_check_ids"]),
                "finish_sequence": list(contract["finish_sequence"]),
                "permitted_finish_commands": render_finish_commands(contract, selected, series_manifest),
                "series_manifest": series_manifest,
            }
        except (CompletionError, OSError) as exc:
            raise OnboardingError(f"invalid fixture completion contract: {exc}") from exc

    sources = _read_sources(root, onboarding)
    paths = onboarding.get("source_paths", {})
    if not isinstance(paths, dict):
        paths = {}
    registry_path = paths.get("harness_registry", "src/openforage/skills/agent_runtime_integration/references/harnesses.json")
    if harness_name is not None and not isinstance(sources.get("harness_registry"), list):
        raise OnboardingError(
            f"cannot match the explicitly named harness: {registry_path} is missing or malformed"
        )
    entries = _registry_entries(sources.get("harness_registry"))
    safe_requested = _safe_harness_label(harness_name)
    entry = _match_harness(harness_name, entries) if harness_name is not None else None
    capabilities, runtime = _runtime_capabilities(
        root,
        entry,
        registry_path,
        explicitly_named=harness_name is not None,
    )
    if safe_requested == "<unrecognized harness identifier>":
        runtime["matched_product"] = None
        runtime["registry_status"] = "unknown"
        runtime["registry_availability"] = "unknown"
        runtime["route"] = "references/unsupported.md"

    package = package_observation or _package_observation(sources.get("compatibility"))
    authoritative_paths = list(task.get("authoritative_paths", []))
    routing_and_fact_sources = _fact_paths(task, paths)
    immediate_action = task.get("immediate_action", "")
    verification_commands = list(task.get("verification_commands", []))
    completion_criteria = list(task.get("completion_criteria", []))
    if fixture_projection is not None:
        authoritative_paths = list(fixture_projection["available_input_paths"])
        routing_and_fact_sources = list(fixture_projection["available_input_paths"])
        immediate_action = _fixture_immediate_action(fixture_projection)
        verification_commands = _fixture_verification_commands(fixture_projection)
        completion_criteria = _fixture_completion_criteria(fixture_projection)
    all_capabilities = [
        package,
        _capability(
            "Live campaign access",
            "unknown",
            "Not checked; invite codes and credentials are never inspected. The operator confirms access only for an explicitly authorized live campaign.",
            "README.md",
        ),
        *capabilities,
    ]
    continuous_process = next(
        item for item in capabilities if item["name"] == "Separate same-harness CLI invocation"
    )
    continuous_identity = next(
        item for item in capabilities if item["name"] == "Distinct session identity"
    )
    process_identity_observed = (
        continuous_process["availability"] == "observed"
        and continuous_identity["availability"] == "observed"
    )
    continuous_observed = False
    if process_identity_observed:
        continuous_message = (
            "Scoped process and identity records are informational only; this packet does not authorize or imply a live launch. "
            "Continuous search still requires explicit authorization and every live prerequisite; never use the main session as fallback."
        )
    else:
        continuous_message = (
            "Continuous search is not established. Keep work offline until a distinct same-harness CLI process and identity are supported; never use the main session as fallback."
        )

    objective = task.get("objective", "")
    packet: dict[str, Any] = {
        "schema_version": 1,
        "task": {
            "id": selected,
            "label": task.get("label", selected),
            "objective": objective,
            "explicit_user_task_wins": True,
        },
        "mission": onboarding.get("mission", ""),
        "research_intent": onboarding.get("research_intent", ""),
        "reference_goal": _reference_goal(sources, paths)
        if selected in {"research", "campaign"}
        else None,
        "research_map": _research_map_summary(sources, paths)
        if selected in {"research", "campaign"}
        else None,
        "essential_context": list(task.get("essential_context", [])),
        "authoritative_paths": authoritative_paths,
        "routing_and_fact_sources": routing_and_fact_sources,
        "immediate_action": immediate_action,
        "required_tools": list(task.get("required_tools", [])),
        "capabilities": all_capabilities,
        "runtime": {
            **runtime,
            "requested_identifier": safe_requested,
            "selected_route": _registry_route_path(
                registry_path,
                str(runtime.get("route", "references/unsupported.md")),
            ),
        },
        "continuous_search_gate": {
            "availability": "observed" if continuous_observed else "unknown",
            "evidence_scope": continuous_message,
            "main_session_fallback": False,
        },
        "offline_work_available": bool(task.get("offline_allowed", False)),
        "live_campaign_required_for_selected_route": bool(task.get("live_campaign_required", False)),
        "access_rule": onboarding.get("access_rule", ""),
        "native_primitives_rule": onboarding.get("runtime_rules", {}).get("native_primitives", ""),
        "continuous_search_rule": onboarding.get("runtime_rules", {}).get("continuous_search", ""),
        "essential_rules": list(onboarding.get("essential_rules", [])),
        "verification_commands": verification_commands,
        "completion_criteria": completion_criteria,
        "generation": {
            "read_only_by_default": True,
            "generate_command": "python scripts/start_here.py --generate",
            "check_command": "python scripts/start_here.py --check",
            "static_readable_copy": onboarding.get("generated_entrypoints", {}).get("portable_packet", "docs/agents/start-here.md"),
        },
        "fixture_completion_contract": fixture_projection,
        "available_fixture_routes": [
            {
                "id": fixture,
                "selector": f"python scripts/start_here.py --task {selected} --fixture {fixture}",
            }
            for fixture in fixture_ids(root).get(selected, [])
            if fixture_id is None
        ],
    }
    return packet


def _markdown_path(path: str, link_prefix: str = "") -> str:
    return f"[`{path}`]({link_prefix}{path})"


def render_packet_text(
    packet: dict[str, Any], *, include_title: bool = True, link_prefix: str = ""
) -> str:
    task = packet["task"]
    lines: list[str] = []
    if include_title:
        lines.extend((f"# Start here: {task['label']}", ""))
    lines.extend((f"**Task:** `{task['id']}` — {task['objective']}", ""))
    lines.append(f"**Mission:** {packet['mission']}")
    if task["id"] in {"research", "campaign"}:
        lines.append(f"**Research direction:** {packet['research_intent']}")
        research_map = packet.get("research_map") or {}
        proposal_count = research_map.get("proposal_count")
        map_path = research_map.get("path")
        if isinstance(proposal_count, int) and isinstance(map_path, str):
            lines.append(
                f"**Research map:** {proposal_count} proposals are indexed in "
                f"{_markdown_path(map_path, link_prefix)}; proposals are not results."
            )
        elif isinstance(map_path, str):
            lines.append(
                f"**Research map — unknown:** {research_map.get('note', 'inspect the referenced source.') } "
                f"Source: {_markdown_path(map_path, link_prefix)}."
            )
        goal = packet.get("reference_goal")
        if isinstance(goal, dict):
            algorithm = goal["algorithm_identity"]
            suffix = " (GA)" if algorithm.casefold() == "genetic" else ""
            target = f"{goal['target_completed_simulations']:,}"
            lines.append(
                f"**Reference target:** `{algorithm}`{suffix}, {target} completed simulations — {goal['status']}."
            )
            evidence_paths = ", ".join(
                _markdown_path(path, link_prefix) for path in goal.get("source_paths", []) if path
            )
            if evidence_paths:
                lines.append(f"Target and algorithm facts come from {evidence_paths}; use the compatibility record for benchmark status.")
    lines.append("## Immediate action")
    lines.append(packet["immediate_action"])
    lines.append("")
    lines.append("## Essential context")
    lines.extend(f"- {item}" for item in packet["essential_context"])
    lines.append("")
    lines.append("## Authoritative paths")
    lines.extend(
        f"- {_markdown_path(path, link_prefix)}" for path in packet["authoritative_paths"]
    )
    lines.append("")
    lines.append("## Capability availability")
    for capability in packet["capabilities"]:
        lines.append(
            f"- **{capability['name']} — {capability['availability']}:** "
            f"{capability['evidence']} Source: {_markdown_path(capability['source'], link_prefix)}."
        )
    if packet["runtime"].get("matched_product"):
        lines.append(
            f"- **Selected harness:** {packet['runtime']['matched_product']} "
            f"(registry status: {packet['runtime']['registry_status']}; "
            f"availability label: {packet['runtime']['registry_availability']}); route: "
            f"{_markdown_path(packet['runtime']['selected_route'], link_prefix)}."
        )
    elif packet["runtime"].get("explicitly_named"):
        lines.append(
            f"- **Selected harness:** `{packet['runtime'].get('requested_identifier')}` is not registered; "
            f"use the generic discovery route {_markdown_path(packet['runtime']['selected_route'], link_prefix)}."
        )
    else:
        lines.append(
            f"- **Selected harness — unknown:** none was explicitly supplied; no environment hint was used. "
            f"Generic route: {_markdown_path(packet['runtime']['selected_route'], link_prefix)}."
        )
    lines.append(f"- **Continuous search gate — {packet['continuous_search_gate']['availability']}:** {packet['continuous_search_gate']['evidence_scope']}")
    lines.append("")
    lines.append("## Required tools")
    lines.extend(f"- {item}" for item in packet["required_tools"])
    lines.append("")
    lines.append("## Verification")
    lines.extend(
        f"- `{item['command']}` — {item['when']}"
        for item in packet["verification_commands"]
    )
    lines.append("")
    lines.append("## Completion criteria")
    lines.extend(f"- {item}" for item in packet["completion_criteria"])
    lines.append("")
    fixture = packet.get("fixture_completion_contract")
    if isinstance(fixture, dict):
        lines.append(f"## Explicit fixture contract: `{fixture['id']}`")
        lines.append("This opt-in route does not change the generic task route. Its exact file boundary comes from the canonical onboarding contract.")
        lines.append("**Available inputs:**")
        lines.extend(f"- `{path}`" for path in fixture["available_input_paths"])
        lines.append("**Required deliverables:**")
        lines.extend(f"- `{path}`" for path in fixture["deliverables"])
        lines.append("**Optional scratch (not a deliverable or artifact-hash input):**")
        lines.extend(f"- `{path}`" for path in fixture["scratch_paths"])
        lines.append("**Fixed finish-check IDs:** " + ", ".join(f"`{item}`" for item in fixture["fixed_finish_check_ids"]))
        lines.append("**Required order:**")
        lines.extend(f"{index}. `{step}`" for index, step in enumerate(fixture["finish_sequence"], 1))
        lines.append("**Permitted finish commands:**")
        lines.extend(
            f"- `{item['command']}` — `{item['check_id']}`"
            for item in fixture["permitted_finish_commands"]
        )
        lines.append(
            "The completion check is offline and read-only. It validates only fixed public checks and disclosed schemas; "
            "native execution evidence remains subject to independent trace review."
        )
        lines.append("")
    elif packet.get("available_fixture_routes"):
        lines.append("## Optional fixture completion contracts")
        lines.append("Generic work remains task-driven. Select a fixture explicitly to print its exact input paths, deliverables, optional scratch, fixed checks, and final read-only completion command.")
        lines.extend(f"- `{item['id']}`: `{item['selector']}`" for item in packet["available_fixture_routes"])
        lines.append("")
    lines.append("## Non-negotiable routing rules")
    lines.append(f"- {packet['access_rule']}")
    lines.append(f"- {packet['native_primitives_rule']}")
    lines.append(f"- {packet['continuous_search_rule']}")
    lines.extend(f"- {rule}" for rule in packet["essential_rules"])
    lines.append("- This packet makes no registration, launch, install, network, or session-start request.")
    lines.append("")
    lines.append("## Read-only commands")
    lines.append("- Print the default packet: `python scripts/start_here.py`.")
    lines.append("- Select a route: `python scripts/start_here.py --task <research|contribute|maintenance|diagnose|adapter|campaign>`.")
    lines.append("- Select an opt-in fixture contract: `python scripts/start_here.py --task <task> --fixture <fixture>`.")
    lines.append("- Run its offline read-only completion check: add `--check-completion`; research fixture checks also require a selected safe `--series-manifest` under the research series directory.")
    lines.append("- Name a harness explicitly when relevant: `python scripts/start_here.py --task research --harness <registry-product>`.")
    lines.append("- JSON packet: add `--format json`; help: `python scripts/start_here.py --help`.")
    lines.append("- Deterministic offline selfcheck: `python scripts/start_here.py --selfcheck`.")
    lines.append("- Generated docs are changed only by explicit `--generate`; `--check` reports drift without writing.")
    return "\n".join(lines) + "\n"


def render_root_route(root: Path, onboarding: dict, sources: dict[str, Any], role: str) -> str:
    task_id = onboarding.get("default_task")
    tasks = onboarding.get("tasks", {})
    task = tasks.get(task_id, {}) if isinstance(tasks, dict) else {}
    goal = _reference_goal(sources, onboarding.get("source_paths", {}))
    lines = [BEGIN_MARKER]
    lines.append(f"**Mission.** {onboarding.get('mission', '')}")
    if isinstance(goal, dict):
        algorithm = goal["algorithm_identity"]
        suffix = " (GA)" if algorithm.casefold() == "genetic" else ""
        lines.append(
            f"**Research objective.** {onboarding.get('research_intent', '')} "
            f"The intended reference target is `{algorithm}`{suffix} with "
            f"{goal['target_completed_simulations']:,} completed simulations; this is a target, not a result."
        )
    if role == "readme":
        lines.append(
            "**Next useful action.** Follow the explicit user task. If the user is seeking signals without a more specific task, "
            "start with offline research: run `python scripts/start_here.py`. It prints a task packet and does not install, "
            "connect, register, launch a campaign, start a session, or search live."
        )
    else:
        lines.append(
            "**Immediate action.** Follow the explicit user task; for signal-seeking intent with no more specific request, "
            "run `python scripts/start_here.py` for the default offline research packet. It is read-only and never launches work."
        )
    lines.append(f"**Access.** {onboarding.get('access_rule', '')}")
    lines.append(
        "**Task selectors.** Use `--task research|contribute|maintenance|diagnose|adapter|campaign`; "
        "the first-contribution guide owns the contribution file/validation map. "
        "Read or paste [`docs/agents/start-here.md`](docs/agents/start-here.md) when Python or automatic root-instruction loading is unavailable."
    )
    rules = onboarding.get("runtime_rules", {})
    lines.append(f"**Continuous searches.** {rules.get('continuous_search', '')}")
    lines.append(f"**Native primitives.** {rules.get('native_primitives', '')}")
    essential = onboarding.get("essential_rules", [])
    if not isinstance(essential, list) or not essential:
        raise OnboardingError("essential_rules must be a non-empty list")
    lines.append("**Essential rules.** " + " ".join(str(rule) for rule in essential))
    lines.append(END_MARKER)
    return "\n".join(lines)


def _replace_block(text: str, replacement: str, relpath: str) -> str:
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise OnboardingError(
            f"{relpath} must contain exactly one generated-route marker pair; refusing to rewrite it"
        )
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    if end < start:
        raise OnboardingError(f"{relpath} generated-route markers are reversed")
    return text[:start] + replacement + text[end:]


def render_generated_files(root: Path) -> dict[str, str]:
    onboarding = _load_json(root, ONBOARDING_PATH)
    if not isinstance(onboarding, dict):
        raise OnboardingError(f"{ONBOARDING_PATH} must contain a JSON object")
    sources = _read_sources(root, onboarding)
    outputs = onboarding.get("generated_entrypoints")
    if not isinstance(outputs, dict):
        raise OnboardingError("generated_entrypoints must be an object")
    result: dict[str, str] = {}
    for key in ("readme", "agents", "claude"):
        relpath = outputs.get(key)
        if not isinstance(relpath, str) or relpath != GENERATED_OUTPUTS[key]:
            raise OnboardingError(f"generated_entrypoints.{key} must be {GENERATED_OUTPUTS[key]!r}")
        source_text = (root / relpath).read_text(encoding="utf-8")
        block = render_root_route(root, onboarding, sources, key)
        result[relpath] = _replace_block(source_text, block, relpath)

    packet_path = outputs.get("portable_packet")
    if not isinstance(packet_path, str) or packet_path != GENERATED_OUTPUTS["portable_packet"]:
        raise OnboardingError(
            f"generated_entrypoints.portable_packet must be {GENERATED_OUTPUTS['portable_packet']!r}"
        )
    static_packet = build_packet(
        root,
        task_id=onboarding.get("default_task"),
        package_observation=_capability(
            "OpenForage package installation",
            "unknown",
            "Static copy: package state depends on the reader's interpreter; offline research does not require an install.",
            onboarding.get("source_paths", {}).get("compatibility", "docs/agents/references/compatibility.json"),
        ),
    )
    result[packet_path] = (
        "<!-- Generated from docs/agents/references/onboarding.json and the referenced public registries. -->\n"
        "<!-- Regenerate explicitly with `python scripts/start_here.py --generate`; normal use is read-only. -->\n\n"
        + render_packet_text(static_packet, link_prefix="../../")
    )

    fragment_outputs = onboarding.get("generated_entry_fragments")
    if fragment_outputs != ENTRY_FRAGMENT_OUTPUTS:
        raise OnboardingError(
            "generated_entry_fragments must match the guarded first-use entrypoint list"
        )
    compatibility = sources.get("compatibility")
    campaign_template = sources.get("campaign_template")
    if not isinstance(compatibility, dict) or not isinstance(campaign_template, dict):
        raise OnboardingError("entry guidance requires compatibility and campaign template records")
    for key, relpath in ENTRY_FRAGMENT_OUTPUTS.items():
        try:
            block = render_entry_fragment(relpath, onboarding, compatibility, campaign_template)
            source_text = (root / relpath).read_text(encoding="utf-8")
            result[relpath] = replace_entry_fragment(source_text, block, relpath)
        except (OSError, ValueError) as exc:
            raise OnboardingError(f"cannot render generated entry guidance for {relpath}: {exc}") from exc

    capability_doc = "docs/agents/harness-verification.md"
    try:
        capability_text = (root / capability_doc).read_text(encoding="utf-8")
        capability_summary = render_capability_projection(root)
        result[capability_doc] = replace_capability_projection(
            capability_text, capability_summary, capability_doc
        )
    except (OSError, CapabilityProjectionError) as exc:
        raise OnboardingError(f"cannot render scoped capability projection for {capability_doc}: {exc}") from exc
    return result


def check_generated(root: Path) -> list[str]:
    try:
        expected = render_generated_files(root)
    except (OnboardingError, OSError) as exc:
        return [str(exc)]
    drift = []
    for relpath, text in expected.items():
        path = root / relpath
        try:
            current = path.read_text(encoding="utf-8")
        except OSError as exc:
            drift.append(f"{relpath}: cannot read generated entrypoint ({exc})")
            continue
        if current != text:
            drift.append(f"{relpath}: generated route is stale; run `python scripts/start_here.py --generate`")
    return drift


def generate(root: Path) -> list[str]:
    generated = render_generated_files(root)
    for relpath, text in generated.items():
        path = root / relpath
        path.write_text(text, encoding="utf-8")
    return sorted(generated)


def selfcheck(root: Path) -> int:
    failures: list[str] = []
    try:
        onboarding = _load_json(root, ONBOARDING_PATH)
        sources = _read_sources(root, onboarding)
        default = onboarding.get("default_task")
        first = build_packet(root, package_observation=_capability(
            "OpenForage package installation", "observed", "not installed in selfcheck", "compatibility.json"
        ))
        second = build_packet(root, package_observation=_capability(
            "OpenForage package installation", "observed", "not installed in selfcheck", "compatibility.json"
        ))
        contribution = build_packet(
            root,
            "contribute",
            package_observation=_capability(
                "OpenForage package installation", "observed", "not installed in selfcheck", "compatibility.json"
            ),
        )
        unknown = build_packet(
            root,
            "research",
            "unregistered-test-harness",
            package_observation=_capability(
                "OpenForage package installation", "observed", "not installed in selfcheck", "compatibility.json"
            ),
        )
        if first != second:
            failures.append("default JSON packet is not deterministic")
        if default != "research" or first["task"]["id"] != "research":
            failures.append("no-argument route must default to research")
        if contribution["task"]["id"] != "contribute" or contribution["live_campaign_required_for_selected_route"]:
            failures.append("explicit contribution task did not override the default route")
        if not first["offline_work_available"] or first["live_campaign_required_for_selected_route"]:
            failures.append("offline research is incorrectly blocked on a live campaign")
        package_capability = next(
            cap for cap in first["capabilities"] if cap["name"] == "OpenForage package installation"
        )
        if package_capability["availability"] != "observed" or "not installed" not in package_capability["evidence"].casefold():
            failures.append("missing package observation is not reported as a fact")
        if unknown["runtime"]["matched_product"] is not None or any(
            cap["availability"] != "unknown"
            for cap in unknown["capabilities"]
            if cap["name"] != "OpenForage package installation"
        ):
            failures.append("unknown harness did not stay generic with unknown capabilities")
        if not unknown["immediate_action"] or "offline" not in unknown["immediate_action"].casefold():
            failures.append("unknown harness or missing package suppressed the useful offline action")
        if unknown["continuous_search_gate"]["main_session_fallback"] is not False:
            failures.append("unknown separate-session support permits a main-session search fallback")
        if "never fall back" not in onboarding.get("runtime_rules", {}).get("continuous_search", "").casefold():
            failures.append("canonical continuous-search rule does not reject main-session fallback")
        access_rule = onboarding.get("access_rule", "").casefold()
        if not all(phrase in access_rule for phrase in ("never register", "start a campaign", "automatically")):
            failures.append("canonical access rule does not prohibit automatic live registration or campaign launch")
        if not _reference_goal(sources, onboarding.get("source_paths", {})):
            failures.append("research reference target is not read from compatibility/template data")
        research_map = _research_map_summary(sources, onboarding.get("source_paths", {}))
        if research_map["availability"] != "observed" or not research_map.get("proposal_count"):
            failures.append("research opportunity proposals are not read from their authoritative map")
        for product, proof_id, expected_version, task_status in (
            ("OpenCode", "background-proof-ad1a6d07359d4573bf5ab25628d184fe", "1.18.31", "observed"),
            ("Codex", "background-proof-c81444655561472aa20ed857260f92ce", "codex-cli 0.156.1", "unknown"),
        ):
            product_packet = build_packet(
                root,
                "research",
                product,
                package_observation=_capability(
                    "OpenForage package installation", "observed", "not installed in selfcheck", "compatibility.json"
                ),
            )
            projected = product_packet["runtime"].get("capability_records", [])
            proof_records = [record for record in projected if record.get("test_id") == proof_id]
            if not proof_records or any(
                record.get("product_version") != expected_version
                or record.get("scope", {}).get("os") != "linux"
                or record.get("scope", {}).get("profile") != "isolated"
                or record.get("probe") != "bounded-native-background-agent-proof"
                or not record.get("scope", {}).get("evidence_scope")
                for record in proof_records
            ):
                failures.append(f"{product} packet lost proof version/OS/profile/probe/evidence scope")
            scoped_entries = _registry_entries(sources.get("harness_registry"))
            selected_entry = _match_harness(product, scoped_entries)
            try:
                selected_records = (
                    project_capability_records(
                        root,
                        selected_entry,
                        selectors={
                            "test_id": proof_id,
                            "product_version": expected_version,
                            "mechanism": "external-background-controller",
                            "scope.os": "linux",
                            "scope.profile": "isolated",
                        },
                    )
                    if selected_entry is not None
                    else []
                )
            except CapabilityProjectionError as exc:
                failures.append(f"{product} exact capability selection failed: {exc}")
                selected_records = []
            if not selected_records:
                failures.append(f"{product} exact mechanism/profile selection returned no records")
            elif any(
                record.get("test_id") != proof_id
                or record.get("product_version") != expected_version
                or record.get("mechanism") != "external-background-controller"
                or record.get("scope", {}).get("os") != "linux"
                or record.get("scope", {}).get("profile") != "isolated"
                for record in selected_records
            ):
                failures.append(f"{product} exact selector pooled an unrelated capability scope")
            elif selected_records != project_capability_records(
                root,
                selected_entry,
                selectors={
                    "test_id": proof_id,
                    "product_version": expected_version,
                    "mechanism": "external-background-controller",
                    "scope.os": "linux",
                    "scope.profile": "isolated",
                },
            ):
                failures.append(f"{product} capability projection is not deterministic")
            labels = {cap["name"]: cap["availability"] for cap in product_packet["capabilities"]}
            for name in ("Parent independence", "Exact-child continuation", "External controller recurrence"):
                if labels.get(name) != "observed":
                    failures.append(f"{product} packet did not project observed {name.lower()}")
            for name in ("Native goals", "Native memory", "Native scheduler", "Native task store"):
                if labels.get(name) != "unknown":
                    failures.append(f"{product} packet elevated {name.lower()} beyond the scoped proof")
            if labels.get("Native task tool operation") != task_status:
                failures.append(f"{product} packet misreported native task-tool evidence")
            if product_packet["continuous_search_gate"]["availability"] != "unknown":
                failures.append(f"{product} scoped records implied an operational search launch")
            if product_packet["continuous_search_gate"]["main_session_fallback"] is not False:
                failures.append(f"{product} packet permits a main-session search fallback")
        for packet in (first, contribution, unknown):
            if any(cap.get("availability") not in AVAILABILITY_LABELS for cap in packet["capabilities"]):
                failures.append("a capability is missing observed/documented/unknown labeling")
                break
        if any("openforage register" in item.get("command", "") for item in first["verification_commands"]):
            failures.append("offline route contains an automatic registration command")
        for task_id, packet in (("research", first), ("contribute", contribution)):
            task = onboarding["tasks"][task_id]
            for field in (
                "authoritative_paths",
                "immediate_action",
                "verification_commands",
                "completion_criteria",
            ):
                if packet[field] != task[field]:
                    failures.append(f"generic {task_id} route changed its canonical {field}")
            if packet["fixture_completion_contract"] is not None:
                failures.append(f"generic {task_id} route was forced into a fixture contract")
            if packet["routing_and_fact_sources"] != _fact_paths(task, onboarding["source_paths"]):
                failures.append(f"generic {task_id} route changed its canonical fact sources")
        docs_fixture = build_packet(root, "contribute", fixture_id="first-finished-contribution-primary")
        research_fixture = build_packet(
            root,
            "research",
            fixture_id="paper-backed-coverage-challenger",
            series_manifest="examples/fresh-agent-evals/research/series/selfcheck-only.json",
        )

        def check_fixture_projection(label: str, packet: dict[str, Any]) -> None:
            contract = packet["fixture_completion_contract"]
            if not isinstance(contract, dict):
                failures.append(f"selected {label} fixture has no completion contract")
                return
            inputs = contract["available_input_paths"]
            if packet["authoritative_paths"] != inputs:
                failures.append(f"selected {label} authoritative paths do not match its declared inputs")
            if packet["routing_and_fact_sources"] != inputs:
                failures.append(f"selected {label} fact sources escape its declared inputs")
            commands = packet["verification_commands"]
            expected_commands = contract["permitted_finish_commands"]
            if [item["command"] for item in commands] != [item["command"] for item in expected_commands]:
                failures.append(f"selected {label} verification commands differ from its permitted commands or order")
            expected_ids = contract["fixed_finish_check_ids"]
            if [item["when"] for item in commands] != [
                f"Run as `{check_id}` in the fixture's required finish sequence."
                for check_id in expected_ids
            ]:
                failures.append(f"selected {label} verification stages do not retain their fixed check IDs")
            if label == "contribution" and (
                expected_ids[:2] != ["docs-baseline-validation", "docs-final-validation"]
                or len(commands) < 2
                or commands[0]["command"] != commands[1]["command"]
            ):
                failures.append("contribution baseline/final copies of the same command lost their check order")

            action = packet["immediate_action"]
            sequence = contract["finish_sequence"]
            action_positions = [action.find(step) for step in sequence]
            first_step = action_positions[0] if action_positions else -1
            if (
                "declared task guidance" not in action
                or "available inputs" not in action
                or "before writing a deliverable" not in action
                or any(position < 0 for position in action_positions)
                or action_positions != sorted(action_positions)
                or action.find("declared task guidance") > first_step
            ):
                failures.append(f"selected {label} immediate action does not orient before its ordered finish sequence")
            primary_source_position = action.find("primary-source excerpt")
            if label == "research" and not 0 <= primary_source_position < first_step:
                failures.append("research fixture does not orient to its disclosed primary-source excerpt before writing")

            criteria = packet["completion_criteria"]
            criteria_text = "\n".join(criteria)
            if "available inputs declared" not in criteria_text:
                failures.append(f"selected {label} completion criteria omit the declared input boundary")
            if any(path not in criteria_text for path in contract["deliverables"] + contract["scratch_paths"]):
                failures.append(f"selected {label} completion criteria omit a declared deliverable or scratch path")
            criteria_positions = [criteria_text.find(step) for step in sequence]
            if any(position < 0 for position in criteria_positions) or criteria_positions != sorted(criteria_positions):
                failures.append(f"selected {label} completion criteria do not preserve its finish sequence")
            if any(check_id not in criteria_text for check_id in expected_ids):
                failures.append(f"selected {label} completion criteria omit a fixed finish-check ID")

            text = render_packet_text(packet)
            expected_sources = "\n".join(f"- {_markdown_path(path)}" for path in inputs)
            expected_verification = "\n".join(
                f"- `{item['command']}` — {item['when']}" for item in commands
            )
            expected_criteria = "\n".join(f"- {item}" for item in criteria)
            if f"## Immediate action\n{action}\n\n## Essential context" not in text:
                failures.append(f"selected {label} text packet does not render its JSON immediate action")
            if f"## Authoritative paths\n{expected_sources}\n\n## Capability availability" not in text:
                failures.append(f"selected {label} text paths differ from its JSON/contract inputs")
            if f"## Verification\n{expected_verification}\n\n## Completion criteria" not in text:
                failures.append(f"selected {label} text commands differ from its JSON/contract commands")
            if f"## Completion criteria\n{expected_criteria}\n\n## Explicit fixture contract" not in text:
                failures.append(f"selected {label} text completion criteria differ from its JSON packet")

        check_fixture_projection("contribution", docs_fixture)
        check_fixture_projection("research", research_fixture)
        if (
            docs_fixture["fixture_completion_contract"]["deliverables"]
            != ["docs/agents/quickstart.md", "trial-output/completion.json"]
        ):
            failures.append("explicit docs fixture did not project its canonical exact deliverables")
        if (
            "examples/fresh-agent-evals/research/series/selfcheck-only.json"
            not in research_fixture["fixture_completion_contract"]["available_input_paths"]
        ):
            failures.append("selected research manifest was not projected as one exact additional input")
        if any(
            re.search(r"(?:grader|oracle|held.?out)", path, re.I)
            for packet in (docs_fixture, research_fixture)
            for path in packet["fixture_completion_contract"]["available_input_paths"]
        ):
            failures.append("fixture projection disclosed protected evaluator or held-out paths")
        completion_failures = completion_selfcheck_problems(root)
        failures.extend(f"completion checker: {item}" for item in completion_failures)
        # Rendering is pure: it computes content but does not write entrypoints.
        render_generated_files(root)
    except (OnboardingError, OSError, TypeError, KeyError) as exc:
        failures.append(f"selfcheck could not build the packet: {exc}")

    for failure in failures:
        print(f"FAIL: start_here selfcheck: {failure}")
    if failures:
        print(f"\n{len(failures)} selfcheck failure(s)")
        return 1
    print("note: start_here selfcheck: deterministic offline routing, scoped Codex/OpenCode capability projections, unknown harness, no launch implication, and no main-session fallback passed")
    print("ok")
    return 0


def _parser(onboarding: dict, registry: Any) -> argparse.ArgumentParser:
    tasks = onboarding.get("tasks", {})
    task_names = sorted(tasks) if isinstance(tasks, dict) else []
    products = sorted(
        {
            _slug(str(entry.get("product", "")))
            for entry in _registry_entries(registry)
            if entry.get("product")
        }
    )
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Known --harness identifiers (derived from harnesses.json): "
            + ", ".join(products)
            + ". Omit --harness for generic instructions; no environment-based detection is used."
        ),
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--generate", action="store_true", help="explicitly regenerate the marked root routes and static Markdown packet")
    actions.add_argument("--check", action="store_true", help="check generated entrypoints for drift without writing")
    actions.add_argument("--selfcheck", action="store_true", help="run deterministic offline routing regressions")
    actions.add_argument("--list-tasks", action="store_true", help="list task selectors")
    actions.add_argument("--list-harnesses", action="store_true", help="list products in the harness registry")
    parser.add_argument("--task", choices=task_names, default=None, help="explicit task route; defaults to the canonical research route")
    parser.add_argument("--harness", default=None, help="explicit registry product/id; unknown values receive generic discovery guidance")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="packet output format (default: text)")
    known_fixtures = sorted({fixture for values in fixture_ids(ROOT).values() for fixture in values})
    parser.add_argument("--fixture", choices=known_fixtures, default=None, help="opt into one task-bound exact completion contract")
    parser.add_argument("--series-manifest", default=None, help="one exact safe JSON manifest beneath the selected task's series directory")
    actions.add_argument("--check-completion", action="store_true", help="run the selected fixture's stdlib-only read-only completion check")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        onboarding = _load_json(ROOT, ONBOARDING_PATH)
        if not isinstance(onboarding, dict):
            raise OnboardingError(f"{ONBOARDING_PATH} must contain a JSON object")
        sources = _read_sources(ROOT, onboarding)
    except OnboardingError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    try:
        parser = _parser(onboarding, sources.get("harness_registry"))
    except (CompletionError, OSError, TypeError) as exc:
        print(f"FAIL: invalid canonical fixture contract: {exc}", file=sys.stderr)
        return 2
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck(ROOT)
    if args.check:
        drift = check_generated(ROOT)
        if drift:
            for item in drift:
                print(f"FAIL: {item}")
            return 1
        print("note: generated onboarding entrypoints match canonical route and evidence sources")
        print("ok")
        return 0
    if args.generate:
        try:
            paths = generate(ROOT)
        except (OnboardingError, OSError) as exc:
            print(f"FAIL: generation refused: {exc}", file=sys.stderr)
            return 2
        for path in paths:
            print(f"generated {path}")
        return 0
    if args.list_tasks:
        for task_id, task in sorted(onboarding.get("tasks", {}).items()):
            print(f"{task_id}\t{task.get('label', task_id)}")
        return 0
    if args.list_harnesses:
        for entry in _registry_entries(sources.get("harness_registry")):
            product = entry.get("product", "unknown")
            print(f"{_slug(str(product))}\t{product}\t{entry.get('status', 'unknown')}")
        return 0

    if args.fixture is not None and args.task is None:
        parser.error("--fixture requires an explicit matching --task")
    if args.series_manifest is not None and args.fixture is None:
        parser.error("--series-manifest requires an explicit --fixture")
    if args.check_completion and (args.task is None or args.fixture is None):
        parser.error("--check-completion requires an explicit --task and --fixture")
    if args.check_completion:
        try:
            if args.series_manifest is not None:
                validate_series_manifest_path(args.task, args.series_manifest)
            result = check_completion(ROOT, args.task, args.fixture, args.series_manifest)
        except (CompletionError, OSError) as exc:
            result = {
                "status": "fail",
                "task": args.task,
                "fixture": args.fixture,
                "read_only": True,
                "artifact_problems": [str(exc)],
                "next_steps": ["Select the exact task-bound fixture and a safe optional series manifest, then rerun the check."],
                "checks": [],
                "missing_inputs": [],
                "unsafe_paths": [],
                "unexpected_deliverables": [],
                "manual_review": ["Unrelated or pre-existing working-tree changes remain unresolved for manual review."],
            }
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(render_completion_text(result), end="")
        return 0 if result.get("status") == "pass" else 1

    try:
        packet = build_packet(
            ROOT, args.task, args.harness, fixture_id=args.fixture, series_manifest=args.series_manifest,
        )
    except OnboardingError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(packet, indent=2, ensure_ascii=False))
    else:
        print(render_packet_text(packet), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
