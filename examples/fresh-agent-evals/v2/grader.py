#!/usr/bin/env python3
"""Evidence-first grader for versioned finished-contribution trials."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "opencode-lifecycle"))

from commands import (  # noqa: E402
    classify_shell_segment, command_semantic_key, command_segments,
    validator_modes,
)
from onboarding.completion import (  # noqa: E402
    fixture_deliverable,
    load_fixture_contract,
    render_finish_commands,
)
from diagnostics import build_failure_diagnostics, runtime_unavailable_diagnostic  # noqa: E402
from evidence import (  # noqa: E402
    _read_operands, action_sequence, command_sha256_matches, explicit_exit_code, result_sequence,
    entry_route_evidence, normalize_access_observations, observed_route_sources,
    final_completion_sequence_evidence, suite_accesses, validator_observations,
    _shell_read_observations,
)
from manifest import ManifestError, verify_manifest  # noqa: E402
from native_arguments import NATIVE_ARGUMENT_TOOLS, validate_native_arguments  # noqa: E402
from native_events import normalize_codex, normalize_opencode  # noqa: E402
from paths import PathSafetyError, exclusive_json, safe_relative  # noqa: E402
from processes import run_bounded  # noqa: E402
from schema import read_json, validate_completion  # noqa: E402
from semantic import fixture_correction_problem  # noqa: E402
from shell_parser import parse_shell_command  # noqa: E402
from snapshot import digest_map, file_map  # noqa: E402
from trace_evidence import action_trace_consistency_problems  # noqa: E402

COMPLETION_REL = fixture_deliverable(
    REPO_ROOT, "contribute", "first-finished-contribution-primary", "completion.json",
)
VALIDATOR_REL = ".github/scripts/validate_agent_docs.py"


class GradeError(RuntimeError):
    """Trial evidence is absent, inconsistent, unsafe, or from a drifted series."""


def _json_equal(left, right) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(right, sort_keys=True, separators=(",", ":"))


def _raw_path(repo: Path, trial: dict) -> Path:
    relative = trial.get("raw_events_path")
    if not isinstance(relative, str):
        raise GradeError("raw native trace path is missing")
    path = safe_relative(relative)
    if path.parts[:3] != (".tmp", "native-contribution-wave", "runs"):
        raise GradeError("raw native trace is outside the ignored v2 run directory")
    target = repo / path
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(repo.resolve()):
        raise GradeError("raw native trace path is unsafe or missing")
    return target


def _normalize_trace(repo: Path, workspace: Path, trial: dict) -> tuple[dict, bytes]:
    raw_path = _raw_path(repo, trial)
    raw = raw_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != trial.get("raw_events_sha256"):
        raise GradeError("retained raw trace digest does not match the trial record")
    if len(raw) != trial.get("raw_events_bytes"):
        raise GradeError("retained raw trace length does not match the trial record")
    harness = trial.get("harness")
    if harness == "opencode":
        normalized = normalize_opencode(raw, workspace, repo)
    elif harness == "codex":
        normalized = normalize_codex(raw, workspace, repo)
    else:
        raise GradeError("trial harness is not an admitted native adapter")
    if not _json_equal(normalized.get("actions"), trial.get("observed_actions")):
        raise GradeError("normalized actions do not match the retained native trace")
    if normalized.get("session_id_sha256") != trial.get("session_id_sha256"):
        raise GradeError("native event session identity does not match the trial record")
    return normalized, raw


def _find_action_by_declared_command(
    observation: dict, actions: list, harness: str, workspace: Path, expected_code: int,
) -> dict | None:
    declared = observation.get("command")
    if not isinstance(declared, str):
        return None
    declared_modes, error = validator_modes(declared, workspace, harness)
    if error or declared_modes != observation.get("modes"):
        return None
    declared_key = command_semantic_key(declared, harness)
    if declared_key is None:
        return None
    matches = []
    for item in validator_observations(actions, harness, workspace):
        actual = item["action"]
        if item["command_key"] != declared_key:
            continue
        if expected_code == 0:
            status = (
                actual.get("outcome", {}).get("native_tool_status")
                if harness == "opencode" and isinstance(actual.get("outcome"), dict)
                else actual.get("native_status")
            )
            if status != "completed":
                continue
        if (
            item["modes"] == declared_modes
            and item["exit_code"] == expected_code
            and command_sha256_matches(actual)
        ):
            matches.append(actual)
    return matches[0] if len(matches) == 1 else None


def _workspace_path(value: str, workspace: Path) -> str | None:
    text = value.strip().strip("\"'")
    if text.startswith("<WORKSPACE>/"):
        text = text[len("<WORKSPACE>/"):]
    elif text.startswith(str(workspace.resolve()) + "/"):
        text = text[len(str(workspace.resolve()) + "/"):]
    elif text.startswith("/"):
        return None
    if text.startswith("./"):
        text = text[2:]
    try:
        return safe_relative(text).as_posix()
    except PathSafetyError:
        return None


def _path_write_sequences(actions: list, target: str, harness: str, workspace: Path) -> list[int]:
    writes = []
    file_change_groups: dict[tuple[str, int], list[dict]] = {}
    read_only_kinds = {
        "validator", "completion-json", "branch-observation", "route-helper",
        "completion-checker", "python-probe", "inspection", "git-read-only", "no-op",
    }
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = action.get("tool")
        identity = _native_key(action)
        if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            continue
        if tool == "file_change":
            item_hash = action.get("native_item_id_sha256")
            change_index = action.get("change_index")
            if (
                isinstance(item_hash, str)
                and identity == item_hash
                and type(change_index) is int
                and change_index >= 0
            ):
                file_change_groups.setdefault((item_hash, change_index), []).append(action)
            continue
        sequence = action.get("completion_sequence")
        if type(sequence) is not int:
            continue
        if tool in {"edit", "write", "apply_patch"} and _workspace_path(str(action.get("command", "")), workspace) == target:
            outcome = action.get("outcome")
            if (
                isinstance(outcome, dict)
                and outcome.get("native_tool_status") == "completed"
                and command_sha256_matches(action)
            ):
                writes.append(sequence)
            continue
        if tool not in {"bash", "shell", "command_execution"}:
            continue
        command = action.get("command")
        if not isinstance(command, str) or target not in command or not command_sha256_matches(action):
            continue
        segments = command_segments(command, harness)
        if not segments:
            continue
        if all(classify_shell_segment(segment, workspace) in read_only_kinds for segment in segments):
            continue
        shell_exit = explicit_exit_code(action, harness)
        if shell_exit is None or (len(segments) > 1 and shell_exit != 0):
            continue
        writes.append(sequence)
    for grouped_actions in file_change_groups.values():
        signatures = {
            (
                item.get("native_item_id"), item.get("path"), item.get("lifecycle_event"),
                item.get("native_status"), item.get("event_sequence"),
                item.get("completion_sequence"), item.get("correlation_conflict"),
                item.get("lifecycle_conflict"),
            )
            for item in grouped_actions
        }
        if len(signatures) == 1:
            action = grouped_actions[0]
            sequence = action.get("completion_sequence")
            if (
                action.get("path") == target
                and action.get("lifecycle_event") == "item.completed"
                and action.get("native_status") == "completed"
                and isinstance(action.get("native_item_id"), str)
                and action.get("correlation_conflict") is False
                and action.get("lifecycle_conflict") is False
                and type(action.get("event_sequence")) is int
                and type(sequence) is int
                and sequence >= action["event_sequence"]
            ):
                writes.append(sequence)
    return sorted(set(writes))


def _doc_write_sequences(actions: list, case_target: str, harness: str, workspace: Path) -> list[int]:
    return _path_write_sequences(actions, case_target, harness, workspace)


def _run_independent_validation(workspace: Path, timeout: float = 180.0) -> dict:
    validator = workspace / VALIDATOR_REL
    if validator.is_symlink() or not validator.is_file():
        return {"status": "fail", "exit_code": None, "executed": False, "reason": "validator source missing or unsafe"}
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = run_bounded(
            [sys.executable, str(validator), "truthfulness", "links", "--root", str(workspace)],
            cwd=workspace, env=env, timeout=timeout,
        )
    except OSError as exc:
        return {"status": "unknown", "exit_code": None, "executed": False, "reason": type(exc).__name__}
    code = proc.returncode
    passed = code == 0 and not proc.timed_out and not proc.process_group_remaining
    return {
        "status": "pass" if passed else "fail" if code is not None else "unknown",
        "executed": True,
        "modes": ["truthfulness", "links"],
        "exit_code": code,
        "timed_out": proc.timed_out,
        "owned_process_group_remaining": proc.process_group_remaining,
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
        "stdout_bytes": len(proc.stdout),
        "stderr_sha256": hashlib.sha256(proc.stderr).hexdigest(),
        "stderr_bytes": len(proc.stderr),
        "method": "independent unchanged repository validator on final workspace",
    }


def _changed_files(initial: dict, current: dict) -> list[dict]:
    result = []
    for relative in sorted(set(initial) | set(current)):
        before, after = initial.get(relative), current.get(relative)
        if before == after:
            continue
        result.append({
            "path": relative,
            "change": "created" if before is None else "deleted" if after is None else "modified",
            "before_sha256": before,
            "after_sha256": after,
        })
    return result


VALIDATOR_ENTRY = ".github/scripts/validate_agent_docs.py"
VALIDATOR_PACKAGE = ".github/scripts/validation"
VALIDATION_SKIP_PARTS = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules",
    ".tmp", ".venv", ".venv-docs",
}


def _is_validation_input(relative: str) -> bool:
    path = Path(relative)
    if any(part in VALIDATION_SKIP_PARTS for part in path.parts):
        return False
    if path.as_posix() == VALIDATOR_ENTRY:
        return True
    if relative.startswith(VALIDATOR_PACKAGE + "/") and path.suffix == ".py":
        return True
    if path.suffix.lower() == ".md":
        return True
    return path.suffix.lower() in {".json", ".jsonl"} and bool(path.parts) and path.parts[0] in {"docs", "src", "examples"}


def _validator_closure_problems(
    workspace: Path, manifest: dict, initial: dict, current: dict, allowed_changed_doc: str,
) -> list[str]:
    problems = []
    frozen = manifest.get("frozen_files", {})
    validator_hashes = {
        name: digest for name, digest in frozen.items()
        if name == VALIDATOR_ENTRY or (name.startswith(VALIDATOR_PACKAGE + "/") and name.endswith(".py"))
    }
    expected_validator_paths = {
        name for name in frozen
        if name == VALIDATOR_ENTRY or (name.startswith(VALIDATOR_PACKAGE + "/") and name.endswith(".py"))
    }
    current_validator_paths = {
        name for name in current
        if name == VALIDATOR_ENTRY or (name.startswith(VALIDATOR_PACKAGE + "/") and name.endswith(".py"))
    }
    if not expected_validator_paths or current_validator_paths != expected_validator_paths:
        problems.append("validator dependency inventory differs from the selected frozen series")
    for relative, digest in validator_hashes.items():
        if initial.get(relative) != digest:
            problems.append(f"prepared validator dependency differs from the frozen series: {relative}")
        if current.get(relative) != initial.get(relative):
            problems.append(f"validator dependency changed in the subject workspace: {relative}")
    relevant = {path for path in set(initial) | set(current) if _is_validation_input(path)}
    for relative in relevant - {allowed_changed_doc}:
        if current.get(relative) != initial.get(relative):
            problems.append(f"independent validator input changed outside the contribution: {relative}")
    return problems


def _direct_access_audit(normalized: dict, actions: list, harness: str, workspace: Path) -> dict:
    """Return completeness and the exact evidence gaps from one access-audit pass."""
    issues: list[dict] = []

    def trace_issue(reason: str) -> None:
        issues.append({"scope": "trace", "action_index": None, "action": None, "reason": reason})

    def action_issue(action_index: int, reason: str) -> None:
        action = actions[action_index]
        if not isinstance(action, dict):
            trace_issue("a normalized native action is not classifiable")
            return
        issues.append({
            "scope": "action", "action_index": action_index,
            "action": action, "reason": reason,
        })

    if not isinstance(actions, list) or not actions:
        trace_issue("native event trace has no classified actions")
    if not isinstance(normalized, dict) or not normalized.get("trace_complete"):
        trace_issue("native event extraction is incomplete")
    if isinstance(normalized, dict) and normalized.get("unclassified_item_count", 0):
        trace_issue("native trace contains unclassified items")
    if isinstance(normalized, dict) and normalized.get("unclassified_tool_count", 0):
        trace_issue("native trace contains unclassified tools")

    for action_index, action in enumerate(actions if isinstance(actions, list) else []):
        if not isinstance(action, dict):
            trace_issue("a normalized native action is not classifiable")
            continue
        tool = action.get("tool")
        if not isinstance(tool, str):
            action_issue(action_index, "native action tool identity is missing or malformed")
            continue
        if tool in {
            "other", "task", "webfetch", "websearch", "mcp_tool_call", "network",
            "external_directory", "lsp", "skill", "python",
        }:
            action_issue(action_index, "native action tool is outside the auditable local file/inspection set")
            continue
        if tool in {"bash", "shell", "command_execution"}:
            command = action.get("command")
            if not isinstance(command, str):
                action_issue(action_index, "native shell command arguments are missing or incomplete")
                continue
            if action.get("command_truncated") is not False:
                action_issue(action_index, "native shell command arguments are truncated or not explicitly complete")
                continue
            if not command_sha256_matches(action):
                action_issue(action_index, "native shell command digest is missing or mismatched")
                continue
            if any(marker in command for marker in ("<REDACTED>", "<HOME>", "<REPO>")):
                action_issue(action_index, "native shell command is redacted and cannot establish access absence")
                continue
            parsed = parse_shell_command(command, harness)
            if parsed is None:
                action_issue(action_index, "native shell command is outside the bounded shell grammar")
                continue
            for segment_index, segment in enumerate(parsed.segments):
                words = list(segment.argv)
                if not words:
                    action_issue(action_index, f"shell segment {segment_index} has no auditable command arguments")
                    continue
                executable = Path(words[0]).name.casefold()
                kind = classify_shell_segment(segment, workspace)
                if kind is None:
                    reason = (
                        f"shell segment {segment_index} uses options or operands outside the bounded ls grammar"
                        if executable == "ls"
                        else f"shell segment {segment_index} is outside the bounded local-command grammar"
                    )
                    action_issue(action_index, reason)
                    continue
                if executable in {"python", "python3"}:
                    allowed_kinds = {"validator", "route-helper", "completion-checker", "python-probe", "completion-json"}
                elif executable == "git":
                    allowed_kinds = {"branch-observation", "git-read-only"}
                else:
                    allowed_kinds = {"inspection", "no-op"}
                if kind not in allowed_kinds:
                    action_issue(
                        action_index,
                        f"shell segment {segment_index} is not an admitted bounded local command form",
                    )
                elif executable == "sed" and kind != "inspection":
                    action_issue(action_index, f"shell segment {segment_index} is not an admitted bounded sed inspection")
        elif tool in {"read", "edit", "write", "apply_patch", "list", "glob", "grep"}:
            if tool in NATIVE_ARGUMENT_TOOLS:
                arguments, problem = validate_native_arguments(action, workspace)
                if problem is not None or not isinstance(arguments, dict):
                    action_issue(action_index, f"native {tool} arguments are incomplete or outside the supported argument schema")
                    continue
            command = action.get("command")
            if not isinstance(command, str) or not command_sha256_matches(action):
                action_issue(action_index, "native file-action arguments are missing, truncated, or have a mismatched digest")
                continue
            if any(marker in command for marker in ("<REDACTED>", "<HOME>", "<REPO>")):
                action_issue(action_index, "native file-action arguments are redacted and cannot establish access absence")
                continue
        elif tool == "file_change":
            identity = action.get("native_item_id_sha256")
            if (
                action.get("path_status") != "workspace_relative"
                or not isinstance(action.get("path"), str)
                or not isinstance(action.get("native_item_id"), str)
                or not isinstance(identity, str)
                or re.fullmatch(r"[0-9a-f]{64}", identity) is None
                or type(action.get("change_index")) is not int
                or action.get("change_index") < 0
                or type(action.get("event_sequence")) is not int
                or type(action.get("completion_sequence")) is not int
                or action.get("completion_sequence") < action.get("event_sequence")
                or action.get("correlation_conflict") is not False
                or action.get("lifecycle_conflict") is not False
            ):
                action_issue(action_index, "native file-change path, identity, or lifecycle arguments are incomplete/conflicting")
        elif tool not in {"todowrite", "question"}:
            action_issue(action_index, "native action tool is outside the auditable local file/inspection set")

    return {"complete": not issues, "issues": issues}


def _direct_access_audit_complete(normalized: dict, actions: list, harness: str, workspace: Path) -> bool:
    """Compatibility wrapper; grading consumes the shared detailed result directly."""
    return _direct_access_audit(normalized, actions, harness, workspace)["complete"]


def _native_key(action: dict) -> str | None:
    value = action.get("native_item_id_sha256") or action.get("native_tool_call_id_sha256")
    return value if isinstance(value, str) else None


def _validator_outcome_record(observation: dict, harness: str) -> dict:
    """Serialize segment inference separately from the native shell outcome."""
    action = observation["action"]
    identity = _native_key(action)
    start = action.get("event_sequence")
    completed = action.get("completion_sequence")
    identity_complete = (
        isinstance(identity, str)
        and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
        and type(start) is int
        and type(completed) is int
        and completed >= start
        and command_sha256_matches(action)
    )
    inferred_exit = observation.get("exit_code") if identity_complete else None
    basis = observation.get("inference_basis")
    if not identity_complete:
        basis = "native_command_digest_or_lifecycle_identity_incomplete"
    identity_kind = (
        "native_tool_call_id_sha256"
        if isinstance(action.get("native_tool_call_id_sha256"), str)
        else "native_item_id_sha256"
    )
    return {
        "modes": observation.get("modes"),
        "validator_segment_index": observation.get("segment_index"),
        "validator_segment_indices": observation.get("validator_segment_indices"),
        "validator_segments": observation.get("validator_segments"),
        "native_shell_exit_code": observation.get("native_shell_exit_code") if identity_complete else None,
        "compound_shell_exit_code": observation.get("compound_shell_exit_code") if identity_complete else None,
        "validator_segment_exit_code": inferred_exit,
        "inference_basis": basis,
        "native_identity_kind": identity_kind,
        "native_action_identity_sha256": identity if identity_complete else None,
        "native_event_sequence": start if identity_complete else None,
        "native_completion_sequence": completed if identity_complete else None,
        "native_command_sha256": action.get("command_sha256") if identity_complete else None,
        "native_command_bytes": action.get("command_bytes") if identity_complete else None,
        "native_harness": harness,
    }


def _validation_outcome_records(actions: list, harness: str, workspace: Path) -> list[dict]:
    return [
        _validator_outcome_record(item, harness)
        for item in validator_observations(actions, harness, workspace)
    ]


def _validation_outcome_for_action(action: dict | None, harness: str, workspace: Path) -> dict | None:
    if not isinstance(action, dict):
        return None
    observations = validator_observations([action], harness, workspace)
    if len(observations) != 1:
        return None
    return _validator_outcome_record(observations[0], harness)


def _check_validation_claim(label: str, claim: dict, expected_result: str, actions: list,
                            harness: str, workspace: Path, allowed_modes: set[str],
                            failures: list[str], *, lower_exclusive: int | None,
                            upper_exclusive: int | None,
                            causal_actions: dict[str, object] | None = None) -> dict | None:
    def record_failure(message: str, action: dict | None = None) -> None:
        failures.append(message)
        if isinstance(action, dict) and causal_actions is not None:
            causal_actions[message] = action

    if not isinstance(claim, dict):
        record_failure(f"validation.{label} is not a structured observation")
        return None
    expected_code = claim.get("exit_code")
    if type(expected_code) is not int or claim.get("result") != expected_result:
        record_failure(f"validation.{label} has a contradictory self-reported result")
        return None
    if expected_result == "failed" and expected_code == 0:
        record_failure("baseline validation must record a nonzero observed exit")
        return None
    if expected_result == "passed" and expected_code != 0:
        record_failure("final validation must record explicit exit code zero")
        return None
    declared_command = claim.get("command")
    if not isinstance(declared_command, str):
        record_failure(f"validation.{label} command is not a full bounded invocation")
        return None
    modes, error = validator_modes(declared_command, workspace, harness)
    if error or not isinstance(modes, list) or modes != claim.get("modes") or not set(modes) & allowed_modes:
        record_failure(f"validation.{label} is not a relevant repository-validator segment in a bounded command")
        return None
    if label == "baseline" and type(upper_exclusive) is not int:
        record_failure("validation.baseline has no completed correction-write boundary")
        return None
    if label == "final" and type(lower_exclusive) is not int:
        record_failure("validation.final has no completed correction-write boundary")
        return None

    declared_key = command_semantic_key(declared_command, harness)
    if declared_key is None:
        record_failure(f"validation.{label} command is not a full bounded invocation")
        return None

    matching = []
    unorderable = False
    for item in validator_observations(actions, harness, workspace):
        action = item["action"]
        if item["modes"] != modes or item["command_key"] != declared_key:
            continue
        start = action.get("event_sequence")
        if type(start) is not int:
            unorderable = True
            continue
        in_phase = (
            start < upper_exclusive
            if label == "baseline"
            else start > lower_exclusive and (upper_exclusive is None or start < upper_exclusive)
        )
        if in_phase:
            matching.append(item)
    if unorderable:
        record_failure(f"validation.{label} has an exact native command without an orderable event identity")
        return None
    if not matching:
        record_failure(f"validation.{label} has no exact native command in its required causal phase")
        return None

    ordered = []
    identities = set()
    for observation in matching:
        action = observation["action"]
        start = action.get("event_sequence")
        completed = action.get("completion_sequence")
        identity = _native_key(action)
        if (
            type(completed) is not int
            or completed < start
            or not isinstance(identity, str)
            or re.fullmatch(r"[0-9a-f]{64}", identity) is None
            or identity in identities
            or not command_sha256_matches(action)
        ):
            record_failure(f"validation.{label} has a missing, conflicting, or incomplete native result identity")
            return None
        if (
            (lower_exclusive is not None and (start <= lower_exclusive or completed <= lower_exclusive))
            or (upper_exclusive is not None and (start >= upper_exclusive or completed >= upper_exclusive))
        ):
            record_failure(f"validation.{label} result is not strictly inside its causal phase")
            return None
        identities.add(identity)
        ordered.append((start, completed, observation))

    ordered.sort(key=lambda item: item[0])
    if (
        len({start for start, _completed, _action in ordered}) != len(ordered)
        or len({completed for _start, completed, _action in ordered}) != len(ordered)
        or any(left[1] >= right[1] for left, right in zip(ordered, ordered[1:]))
    ):
        record_failure(f"validation.{label} native command identities or result order conflict")
        return None

    selected = max(ordered, key=lambda item: item[1])[2]
    action = selected["action"]
    actual_code = selected["exit_code"]
    if actual_code is None:
        basis = selected.get("inference_basis")
        if basis == "and_chain_nonzero_exit_does_not_locate_validator_outcome":
            record_failure(f"validation.{label} compound-shell nonzero exit does not identify the validator segment outcome", action)
        elif basis == "and_chain_zero_exit_conflicts_with_literal_false_segment":
            record_failure(f"validation.{label} compound-shell zero exit conflicts with a literal false segment", action)
        elif basis in {"standalone_shell_launch_failure_126", "standalone_shell_launch_failure_127"}:
            shell_code = selected.get("native_shell_exit_code")
            record_failure(
                f"validation.{label} runtime unavailable: native shell launch failed with exit {shell_code}",
                action,
            )
        else:
            record_failure(f"validation.{label} boundary command has a missing, denied, or unobserved exit result", action)
        return None
    if actual_code != expected_code:
        record_failure(f"validation.{label} does not match the latest exact command result in its causal phase", action)
        return None
    if expected_code == 0:
        status = (
            action.get("outcome", {}).get("native_tool_status")
            if harness == "opencode" and isinstance(action.get("outcome"), dict)
            else action.get("native_status")
        )
        if status != "completed":
            record_failure(f"validation.{label} zero exit lacks a completed native result", action)
            return None
    return action


def _check_completion_claims(repo: Path, workspace: Path, case_id: str, actions: list,
                             harness: str, initial: dict) -> dict:
    failures: list[str] = []
    diagnostic_causes: dict[str, object] = {}
    unsupported = 0
    route_evidence = entry_route_evidence(
        actions, harness, workspace,
        task="contribute",
        task_route_paths=("docs/agents/first-contribution.md",),
    )
    if route_evidence.get("status") != "pass":
        missing_route = route_evidence.get("missing_evidence", [])
        failures.append(
            "entrypoint-first contribution route evidence is incomplete: "
            + "; ".join(item for item in missing_route if isinstance(item, str))
        )
    oracle = read_json(repo / "examples/fresh-agent-evals/v2/oracle.json")
    expected = oracle["cases"][case_id]
    target = expected["fixture"]["path"]
    fixture_contract = (
        load_fixture_contract(repo, "contribute", "first-finished-contribution-primary")
        if case_id == "first-finished-contribution-primary" else None
    )
    if fixture_contract is not None and target not in fixture_contract["deliverables"]:
        failures.append("selected public fixture target is outside the canonical completion deliverables")
    completion_path = workspace / COMPLETION_REL
    if completion_path.is_symlink() or not completion_path.is_file():
        return {
            "completion": None, "target": target, "baseline_action": None,
            "final_action": None, "failures": ["completion.json is missing or is not a regular file"],
            "unsupported": 1, "route_sources_observed": [], "route_selector_observed": False,
            "entry_route": route_evidence, "completion_write_sequence": None,
        }
    try:
        completion = read_json(completion_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "completion": None, "target": target, "baseline_action": None,
            "final_action": None, "failures": ["completion.json is invalid JSON or has duplicate keys"],
            "unsupported": 1, "route_sources_observed": [], "route_selector_observed": False,
            "entry_route": route_evidence, "completion_write_sequence": None,
        }
    schema_problems = validate_completion(completion, case_id)
    failures.extend(f"completion schema: {problem}" for problem in schema_problems)
    unsupported += len(schema_problems)
    contract = oracle["grade_contract"]
    baseline_modes = set(contract["accepted_baseline_validation_modes"][case_id])
    final_modes = set(contract["accepted_success_validation_modes"][case_id])
    facts = expected["required_route_facts"]
    route_value = completion.get("route", {}) if isinstance(completion, dict) else {}
    route = route_value if isinstance(route_value, dict) else {}
    for field, value in facts.items():
        if route.get(field) != value:
            unsupported += 1
            failures.append(f"structured route fact {field!r} differs from the canonical source record")
    sources, route_invoked, route_reads_complete = observed_route_sources(actions, harness, workspace)
    raw_sources = route.get("authoritative_sources", []) if isinstance(route, dict) else []
    claimed_sources = {item for item in raw_sources if isinstance(item, str)} if isinstance(raw_sources, list) else set()
    if not claimed_sources or not claimed_sources <= sources:
        unsupported += 1
        failures.append("authoritative_sources are not supported by completed native reads")
    if not (sources & {"README.md", "AGENTS.md", "CLAUDE.md"}) and not route_invoked:
        failures.append("repository route was not established by a root entry read or contribute selector")
    if not (sources & {"docs/agents/first-contribution.md", "docs/agents/start-here.md", "docs/agents/references/onboarding.json"}):
        failures.append("canonical contribution route sources were not actually read")

    contribution = completion.get("contribution", {}) if isinstance(completion, dict) else {}
    if not isinstance(contribution, dict):
        contribution = {}
    if contribution.get("kind") != "documentation_correction":
        unsupported += 1
        failures.append("contribution.kind does not identify a documentation correction")
    expected_public_outputs = (
        [path for path in fixture_contract["deliverables"] if not path.startswith("trial-output/")]
        if fixture_contract is not None else [target]
    )
    if contribution.get("changed_paths") != expected_public_outputs:
        unsupported += 1
        declared_paths = ", ".join(expected_public_outputs) if expected_public_outputs else "(none declared)"
        failures.append(
            "completion contribution.changed_paths must list only the declared public deliverable paths "
            f"(excluding the completion record and optional scratch): {declared_paths}"
        )

    validation = completion.get("validation", {}) if isinstance(completion, dict) else {}
    if not isinstance(validation, dict):
        validation = {}
    document_writes = _doc_write_sequences(actions, target, harness, workspace)
    completion_writes = _path_write_sequences(actions, COMPLETION_REL, harness, workspace)
    first_document_write = min(document_writes) if document_writes else None
    last_document_write = max(document_writes) if document_writes else None
    final_completion_write = max(completion_writes) if completion_writes else None
    baseline = _check_validation_claim(
        "baseline", validation.get("baseline"),
        "failed", actions, harness, workspace, baseline_modes, failures,
        lower_exclusive=None, upper_exclusive=first_document_write,
        causal_actions=diagnostic_causes,
    )
    final = _check_validation_claim(
        "final", validation.get("final"),
        "passed", actions, harness, workspace, final_modes, failures,
        lower_exclusive=last_document_write, upper_exclusive=final_completion_write,
        causal_actions=diagnostic_causes,
    )
    return {"completion": completion, "target": target, "baseline_action": baseline,
            "final_action": final, "failures": failures, "unsupported": unsupported,
            "route_sources_observed": sorted(sources), "route_selector_observed": route_invoked,
            "route_reads_complete": route_reads_complete, "entry_route": route_evidence,
            "completion_write_sequence": final_completion_write,
            "diagnostic_causes": diagnostic_causes}


def _command_words(action: dict, harness: str) -> list[str] | None:
    segments = _command_segments(action, harness)
    return segments[0] if len(segments) == 1 else None


def _command_segments(action: dict, harness: str) -> list[list[str]]:
    raw = action.get("command")
    if not isinstance(raw, str) or action.get("command_truncated") is not False:
        return []
    segments = command_segments(raw, harness)
    return [list(segment.argv) for segment in segments] if segments is not None else []


def _check_final_completion_sequence(
    repo: Path,
    workspace: Path,
    case_id: str,
    actions: list,
    harness: str,
    artifact_write_sequences: dict,
) -> tuple[dict, list[str]]:
    if case_id != "first-finished-contribution-primary":
        return {"status": "not_applicable", "reason": "reserved case has no public completion contract"}, []
    contract = load_fixture_contract(repo, "contribute", case_id)
    commands = render_finish_commands(contract, "contribute", None)
    by_id = {item["check_id"]: item["command"] for item in commands}
    checker_command = by_id["docs-completion-check"]
    diff_command = by_id["docs-git-diff-check"]
    evidence, failures = final_completion_sequence_evidence(
        actions,
        harness,
        workspace,
        checker_command=checker_command,
        diff_command=diff_command,
        artifact_write_sequences=artifact_write_sequences,
        required_artifact_paths=tuple(contract["deliverables"]),
        scope="docs",
    )
    evidence.update({
        "command": checker_command,
        "event_sequence": evidence.get("completion_event_sequence"),
        "exit_code": evidence.get("completion_exit_code"),
        "native_action_identity_sha256": evidence.get("completion_native_identity_sha256"),
        "diff_check_status": evidence.get("diff_status", "unverified"),
    })
    return evidence, failures


def _forbidden_action_problems(
    actions: list, harness: str, *, causal_actions: dict[str, object] | None = None,
) -> list[str]:
    problems = []
    forbidden_tools = {"task", "webfetch", "websearch", "network", "external_directory"}
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            problems.append("native action list contains a malformed observation")
            continue
        tool = action.get("tool")
        if tool in forbidden_tools:
            _record_action_problem(
                problems, causal_actions,
                f"forbidden or unclassified native tool observed: {tool}", action_index, action,
            )
        if tool in {"bash", "shell", "command_execution"}:
            inspection = {"grep", "rg", "egrep", "fgrep", "cat", "head", "tail", "less", "more", "sed", "awk", "wc", "sort", "uniq", "tr", "cut", "nl"}
            for words in _command_segments(action, harness):
                if not words:
                    continue
                executable = Path(words[0]).name.casefold()
                args = [value.casefold() for value in words[1:]]
                if executable in inspection:
                    continue
                if executable in {"curl", "wget", "gh"}:
                    _record_action_problem(
                        problems, causal_actions, "network or external communication command observed",
                        action_index, action,
                    )
                if executable in {"pip", "uv", "npm", "poetry"} and any(
                    value in {"install", "sync", "add"} for value in args[:3]
                ):
                    _record_action_problem(
                        problems, causal_actions, "package installation/setup command observed",
                        action_index, action,
                    )
                if executable in {"python", "python3"} and len(args) >= 3 and args[:2] == ["-m", "pip"] and args[2] in {"install", "download"}:
                    _record_action_problem(
                        problems, causal_actions, "package installation command observed",
                        action_index, action,
                    )
                if executable == "openforage" and args and args[0] in {"register", "start", "stop", "search", "submit", "_worker"}:
                    _record_action_problem(
                        problems, causal_actions,
                        "live OpenForage lifecycle/search/submission command observed",
                        action_index, action,
                    )
                if executable in {"opencode", "codex", "claude", "aider", "gemini"}:
                    _record_action_problem(
                        problems, causal_actions, "nested native agent/model launch command observed",
                        action_index, action,
                    )
                if executable == "git" and args and args[0] in {"clone", "add", "push", "commit"}:
                    _record_action_problem(
                        problems, causal_actions, "network, publication, or commit command observed",
                        action_index, action,
                    )
        outcome = action.get("outcome")
        if isinstance(outcome, dict) and outcome.get("native_tool_status") == "denied":
            if action.get("tool") in {"bash", "shell"}:
                _record_action_problem(
                    problems, causal_actions, "denied shell action cannot establish executed validation",
                    action_index, action,
                )
    return sorted(set(problems))


def _outside_workspace_problems(
    actions: list, harness: str, workspace: Path, repo: Path,
    *, causal_actions: dict[str, object] | None = None,
) -> list[str]:
    problems = []
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        tool = action.get("tool")
        if action.get("path_status") == "outside_workspace":
            _record_action_problem(
                problems, causal_actions, "native file-change path escaped the isolated workspace",
                action_index, action,
            )
        if tool in {"read", "edit", "write", "apply_patch", "list", "glob", "grep"}:
            value = action.get("command")
            if isinstance(value, str) and _workspace_path(value, workspace) is None:
                if value.startswith(("<REPO>", "<HOME>", "/", "../", "./../")) or ".." in Path(value).parts:
                    _record_action_problem(
                        problems, causal_actions, "native file tool referenced a path outside the workspace",
                        action_index, action,
                    )
        if tool not in {"bash", "shell", "command_execution"}:
            continue
        raw = action.get("command")
        if not isinstance(raw, str):
            continue
        if "<REPO>" in raw or "<HOME>" in raw or str(repo) in raw or str(Path.home()) in raw:
            _record_action_problem(
                problems, causal_actions, "native command referenced the source checkout or host home",
                action_index, action,
            )
            continue
        segments = _command_segments(action, harness)
        if not segments:
            # Preserve uncertainty in the access-audit metric; do not invent a path operation.
            continue
        for words in segments:
            if not words:
                continue
            tool_name = Path(words[0]).name.casefold()
            operands = []
            if tool_name in {"cat", "head", "tail", "less", "more", "sed"}:
                operands = [word for word in words[1:] if not word.startswith("-")]
            elif tool_name in {"grep", "rg", "egrep", "fgrep"}:
                nonoptions = [word for word in words[1:] if not word.startswith("-")]
                operands = nonoptions[1:]
            elif tool_name in {"ls", "find", "cd", "pushd", "stat", "file", "readlink", "which"}:
                operands = [word for word in words[1:] if not word.startswith("-")]
            elif tool_name in {"python", "python3"}:
                operands = words[1:]
            elif tool_name == "git" and len(words) > 2 and words[1] == "-C":
                operands = [words[2]]
            for operand in operands:
                if operand.startswith("/") and operand != "/dev/null" and not operand.startswith("<WORKSPACE>/"):
                    _record_action_problem(
                        problems, causal_actions, "native command used an absolute path outside the workspace",
                        action_index, action,
                    )
                if ".." in Path(operand).parts:
                    _record_action_problem(
                        problems, causal_actions, "native command used parent traversal outside the workspace",
                        action_index, action,
                    )
    return sorted(set(problems))


def _record_action_problem(
    problems: list[str],
    causal_actions: dict[str, object] | None,
    message: str,
    action_index: int,
    action: dict,
) -> None:
    reason = f"{message} (action_index={action_index})" if causal_actions is not None else message
    problems.append(reason)
    if causal_actions is not None:
        causal_actions[reason] = action


def _write_scope_problems(
    repo: Path, actions: list, workspace: Path,
    *, causal_actions: dict[str, object] | None = None,
) -> list[str]:
    """Permit writes only to canonical deliverables and the one separate scratch path."""
    contract = load_fixture_contract(repo, "contribute", "first-finished-contribution-primary")
    allowed = set(contract["deliverables"]) | set(contract["scratch_paths"])
    problems: list[str] = []
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        tool = action.get("tool")
        if tool in {"edit", "write", "apply_patch"}:
            raw_values = [
                action.get(key)
                for key in ("path", "file_path", "filePath", "command")
                if isinstance(action.get(key), str)
            ]
            normalized = {_workspace_path(value, workspace) for value in raw_values}
            normalized.discard(None)
            if len(normalized) != 1:
                _record_action_problem(
                    problems, causal_actions,
                    "native write path is missing, ambiguous, or outside the workspace",
                    action_index, action,
                )
            elif next(iter(normalized)) not in allowed:
                _record_action_problem(
                    problems, causal_actions,
                    "native write is outside the canonical deliverables and optional scratch path",
                    action_index, action,
                )
        elif tool == "file_change":
            path = action.get("path")
            if action.get("path_status") != "workspace_relative" or not isinstance(path, str):
                _record_action_problem(
                    problems, causal_actions,
                    "native file-change path is not a proven workspace-relative path",
                    action_index, action,
                )
            elif path not in allowed:
                _record_action_problem(
                    problems, causal_actions,
                    "native file change is outside the canonical deliverables and optional scratch path",
                    action_index, action,
                )
    return sorted(set(problems))


def _fixture_content_access_problems(
    repo: Path, actions: list, harness: str, workspace: Path, case_id: str,
    *, causal_actions: dict[str, object] | None = None,
) -> list[str]:
    """Keep observed content reads/searches within the canonical fixture inputs."""
    if case_id != "first-finished-contribution-primary":
        return []
    contract = load_fixture_contract(repo, "contribute", case_id)
    allowed = set(contract["available_input_paths"]) | set(contract["deliverables"]) | set(contract["scratch_paths"])
    problems: list[str] = []
    for observation in normalize_access_observations(actions, harness, workspace):
        if observation.get("operation") not in {"file_content_read", "content_search"}:
            continue
        scope = observation.get("scope") if isinstance(observation.get("scope"), dict) else {}
        paths = scope.get("safe_paths", [])
        if not isinstance(paths, list) or any(path not in allowed for path in paths):
            action_index = observation.get("action_index")
            action = actions[action_index] if type(action_index) is int and 0 <= action_index < len(actions) else None
            if isinstance(action, dict):
                _record_action_problem(
                    problems, causal_actions,
                    "native content access is outside the canonical fixture inputs and deliverables",
                    action_index, action,
                )
            else:
                problems.append("native content access is outside the canonical fixture inputs and deliverables")
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("tool") not in {"bash", "shell", "command_execution"}:
            continue
        command = action.get("command")
        if not isinstance(command, str) or action.get("command_truncated") is not False:
            continue
        for relative, _kind, _segment_index, _operand_index in _shell_read_observations(command, harness, workspace):
            if relative not in allowed:
                _record_action_problem(
                    problems, causal_actions,
                    "bounded shell read/search is outside the canonical fixture inputs and deliverables",
                    action_index, action,
                )
    return sorted(set(problems))


def _setup_metrics(actions: list, harness: str, workspace: Path, trace_complete: bool,
                   codex_clarifications=None) -> dict:
    attempts, unknown, clarification_count = [], False, 0
    read_tools = {"read", "list", "glob", "grep", "edit", "write", "apply_patch", "file_change", "question"}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            unknown = True
            continue
        tool = action.get("tool")
        if tool == "question":
            clarification_count += 1
        if tool in read_tools or tool == "todowrite":
            continue
        if tool in {"task", "webfetch", "websearch", "network"}:
            attempts.append({"action_index": index, "category": "unnecessary_native_setup_or_external_tool"})
            continue
        if tool in {"other", "mcp_tool_call"}:
            unknown = True
            continue
        if tool not in {"bash", "shell", "command_execution"}:
            unknown = True
            continue
        raw = action.get("command")
        parsed = parse_shell_command(raw, harness) if isinstance(raw, str) and action.get("command_truncated") is False else None
        if parsed is None:
            unknown = True
            continue
        for segment_index, segment in enumerate(parsed.segments):
            words = list(segment.argv)
            if not words:
                unknown = True
                continue
            name = Path(words[0]).name.casefold()
            args = [value.casefold() for value in words[1:]]
            is_setup = (
                name in {"curl", "wget"}
                or (name in {"pip", "uv", "npm", "poetry"} and any(x in {"install", "sync", "add"} for x in args[:3]))
                or (name in {"python", "python3"} and args[:2] == ["-m", "pip"] and len(args) > 2 and args[2] in {"install", "download"})
                or (name in {"python", "python3"} and args[:2] == ["-m", "venv"])
                or (name == "git" and args and args[0] in {"clone", "init"})
                or (name == "openforage" and args and args[0] in {"register", "start", "stop", "submit"})
            )
            if is_setup:
                attempts.append({
                    "action_index": index,
                    "segment_index": segment_index,
                    "category": "package_or_live_setup",
                })
            elif classify_shell_segment(segment, workspace) is None:
                unknown = True
    clarification_known = type(codex_clarifications) is int
    if clarification_known:
        clarification_count = codex_clarifications
    metric_known = trace_complete and not unknown
    return {
        "unnecessary_setup_attempts": {
            "status": "observed_complete" if metric_known else "unknown",
            "count": len(attempts) if metric_known else None,
            "observed_lower_bound": len(attempts),
            "attempts": attempts,
        },
        "clarification_observations": {
            "status": "observed" if trace_complete and clarification_known else "unknown",
            "count": clarification_count if trace_complete and clarification_known else None,
        },
    }


def _first_meaningful_action(actions: list, harness: str, workspace: Path,
                             started_epoch: float | None, target: str) -> dict:
    candidates = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = action.get("tool")
        sequence = result_sequence(action) if tool == "file_change" else action_sequence(action)
        if type(sequence) is not int:
            continue
        meaningful = False
        read_outcome = action.get("outcome")
        if tool == "read" and isinstance(read_outcome, dict) and read_outcome.get("native_tool_status") == "completed":
            sources, _route_invoked, _route_reads_complete = observed_route_sources(
                [action], harness, workspace,
            )
            meaningful = bool(sources)
        if tool in {"bash", "shell", "command_execution"}:
            text = action.get("command")
            if isinstance(text, str):
                modes, _error = validator_modes(text, workspace, harness)
                sources, route_invoked, _read_complete = observed_route_sources([action], harness, workspace)
                meaningful = (isinstance(modes, list) and bool(modes)) or bool(sources) or route_invoked
        if tool in {"edit", "write", "apply_patch"}:
            meaningful |= _workspace_path(str(action.get("command", action.get("path", ""))), workspace) == target
        if tool == "file_change":
            identity = action.get("native_item_id_sha256")
            meaningful = (
                action.get("path") == target
                and action.get("lifecycle_event") == "item.completed"
                and action.get("native_status") == "completed"
                and action.get("correlation_conflict") is False
                and action.get("lifecycle_conflict") is False
                and type(action.get("change_index")) is int
                and isinstance(identity, str)
                and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
                and type(action.get("completion_sequence")) is int
                and action.get("completion_sequence") >= action.get("event_sequence", 0)
            )
        if meaningful:
            candidates.append((sequence, action))
    if not candidates:
        return {"status": "unknown", "elapsed_seconds": None,
                "reason": "no timestamped route, validation, or contribution action was observed"}
    first_sequence, first = min(candidates, key=lambda item: item[0])
    event_epoch = (
        first.get("completion_timestamp_epoch")
        if first.get("tool") == "file_change"
        else first.get("event_timestamp_epoch")
    )
    if not isinstance(event_epoch, (int, float)) or isinstance(event_epoch, bool) or started_epoch is None:
        return {"status": "unknown", "elapsed_seconds": None,
                "event_sequence": first_sequence,
                "reason": "native trace did not expose a usable event timestamp"}
    elapsed = float(event_epoch) - started_epoch
    if elapsed < 0 or elapsed > 86400 * 30:
        return {"status": "unknown", "elapsed_seconds": None,
                "event_sequence": first_sequence,
                "reason": "native event timestamp falls outside the trial window"}
    return {"status": "observed", "elapsed_seconds": round(elapsed, 3),
            "event_sequence": first_sequence, "timestamp_basis": "native_event_timestamp"}


def grade_trial(repo: Path, workspace: Path, trial: dict, *, run_independent: bool = True,
                synthetic: bool = False) -> dict:
    """Grade final files and native results; self-reported success never suffices."""
    workspace_arg = Path(workspace)
    if workspace_arg.is_symlink():
        raise GradeError("trial workspace may not be a symlink")
    repo, workspace = Path(repo).resolve(), workspace_arg.resolve()
    failures: list[str] = []
    causal_actions: dict[str, object] = {}
    manifest_path = trial.get("series_manifest_path") if isinstance(trial, dict) else None
    if not isinstance(manifest_path, str):
        raise GradeError("trial record has no explicit selected series manifest path")
    try:
        manifest, manifest_hash = verify_manifest(repo, manifest_path)
    except ManifestError as exc:
        raise GradeError(f"series freeze refused grading: {exc}") from exc
    case_id = trial.get("case_id") if isinstance(trial, dict) else None
    harness = trial.get("harness") if isinstance(trial, dict) else None
    if case_id not in {"first-finished-contribution-primary", "first-finished-contribution-heldout"}:
        raise GradeError("trial case id is not in the frozen series")
    if harness not in {"opencode", "codex"}:
        raise GradeError("trial harness is not supported by this series")
    if workspace == repo or workspace.is_relative_to(repo):
        raise GradeError("trial workspace must be a real isolated directory outside the source checkout")
    if not workspace.is_dir():
        raise GradeError("trial workspace is missing")
    initial = trial.get("initial_file_sha256")
    if not isinstance(initial, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
        for k, v in initial.items()
    ):
        raise GradeError("trial baseline file digest map is missing or malformed")
    try:
        for relative in initial:
            safe_relative(relative)
        baseline_digest = digest_map(initial)
    except (PathSafetyError, ValueError) as exc:
        raise GradeError("trial baseline file digest map contains an unsafe path or hash") from exc
    if baseline_digest != trial.get("snapshot_sha256"):
        raise GradeError("recorded prepared-snapshot identity does not match its file digests")
    if trial.get("series_manifest_sha256") != manifest_hash:
        raise GradeError("trial manifest identity differs from the frozen series")
    if trial.get("workspace_identity") != hashlib.sha256(str(workspace).encode()).hexdigest():
        raise GradeError("workspace identity hash does not match the graded directory")

    normalized, raw = _normalize_trace(repo, workspace, trial)
    actions = normalized["actions"]
    if not actions or not normalized.get("events_observed"):
        failures.append("native event trace is empty; no completed action can be established")
    if not normalized.get("trace_complete"):
        failures.append("native event extraction is incomplete; event-derived metrics and access audit stay unknown")
    if harness == "opencode":
        problems = action_trace_consistency_problems(
            trial.get("observed_actions"), actions,
            {"bash", "shell", "read", "edit", "write", "apply_patch"},
        )
        failures.extend(f"OpenCode native trace consistency: {problem}" for problem in problems)

    raw_accesses = suite_accesses(actions, harness, workspace)
    for action_index in raw_accesses:
        if 0 <= action_index < len(actions) and isinstance(actions[action_index], dict):
            reason = f"direct evaluation case/oracle/grader access was observed (action_index={action_index})"
            failures.append(reason)
            causal_actions[reason] = actions[action_index]
    access_audit = _direct_access_audit(normalized, actions, harness, workspace)
    access_audit_complete = access_audit["complete"]
    if not access_audit_complete:
        trace_wide_unknown_added = False
        for issue in access_audit["issues"]:
            if issue["scope"] == "trace":
                if raw_accesses:
                    continue
                if not trace_wide_unknown_added:
                    failures.append("direct evaluation-suite access audit is unknown; absence is not established")
                    trace_wide_unknown_added = True
                continue
            action_index = issue["action_index"]
            reason = (
                "direct evaluation-suite access audit is unknown; "
                f"{issue['reason']} (action_index={action_index})"
            )
            failures.append(reason)
            causal_actions[reason] = issue["action"]
    failures.extend(_forbidden_action_problems(actions, harness, causal_actions=causal_actions))
    failures.extend(_outside_workspace_problems(
        actions, harness, workspace, repo, causal_actions=causal_actions,
    ))
    failures.extend(_fixture_content_access_problems(
        repo, actions, harness, workspace, case_id, causal_actions=causal_actions,
    ))
    failures.extend(_write_scope_problems(
        repo, actions, workspace, causal_actions=causal_actions,
    ))
    initial_completion = initial.get(COMPLETION_REL)
    if initial_completion is not None:
        failures.append("completion.json was already present in the prepared snapshot")

    completion_result = _check_completion_claims(repo, workspace, case_id, actions, harness, initial)
    failures.extend(completion_result["failures"])
    if isinstance(completion_result.get("diagnostic_causes"), dict):
        causal_actions.update(completion_result["diagnostic_causes"])
    unsupported = completion_result["unsupported"]
    final_artifact_writes = {}
    if case_id == "first-finished-contribution-primary":
        docs_contract = load_fixture_contract(repo, "contribute", case_id)
        final_artifact_writes = {
            path: _path_write_sequences(actions, path, harness, workspace)
            for path in docs_contract["deliverables"]
        }
    completion_check_evidence, completion_check_failures = _check_final_completion_sequence(
        repo,
        workspace,
        case_id,
        actions,
        harness,
        final_artifact_writes,
    )
    failures.extend(completion_check_failures)
    current = file_map(workspace)
    changes = _changed_files(initial, current)
    target = completion_result["target"]
    expected_paths = (
        set(load_fixture_contract(repo, "contribute", case_id)["deliverables"])
        if case_id == "first-finished-contribution-primary"
        else {target, COMPLETION_REL}
    )
    changed_paths = {item["path"] for item in changes}
    if changed_paths != expected_paths:
        failures.append("final workspace diff must contain exactly the canonical completion deliverables")
    if target not in current or target in {"", COMPLETION_REL}:
        failures.append("corrected documentation artifact is absent")
    clean_source = repo / target
    final_target = workspace / target
    if not clean_source.is_file() or not final_target.is_file() or final_target.is_symlink():
        failures.append("final documentation path is missing or unsafe")
    else:
        clean_hash = hashlib.sha256(clean_source.read_bytes()).hexdigest()
        if trial.get("clean_fixture_source_sha256") != clean_hash:
            failures.append("clean fixture source identity differs from the recorded prepared input")
        if trial.get("seeded_fixture_sha256") != initial.get(target):
            failures.append("seeded fixture source identity differs from the recorded prepared input")
        semantic_problem = fixture_correction_problem(repo, workspace, case_id)
        if semantic_problem:
            failures.append(f"fixture-specific documentation correction failed: {semantic_problem}")

    baseline_action = completion_result.get("baseline_action")
    final_action = completion_result.get("final_action")
    if baseline_action is None:
        unsupported += 1
    if final_action is None:
        unsupported += 1
    completion = completion_result.get("completion")
    validation_claims = completion.get("validation") if isinstance(completion, dict) else None
    baseline_claim = validation_claims.get("baseline") if isinstance(validation_claims, dict) else None
    baseline_claim_modes = baseline_claim.get("modes") if isinstance(baseline_claim, dict) else None
    if isinstance(baseline_claim_modes, list) and "all" in baseline_claim_modes:
        expected_preflight_mode = "truthfulness" if case_id == "first-finished-contribution-primary" else "links"
        clean_preflight = trial.get("clean_source_preflight")
        fixture_preflight = trial.get("seed_validator_preflight")
        if (
            not isinstance(clean_preflight, dict)
            or clean_preflight.get("status") != "pass"
            or clean_preflight.get("modes") != ["truthfulness", "links"]
            or clean_preflight.get("command_signature") != "validate_agent_docs.py truthfulness links --root <source-checkout>"
            or clean_preflight.get("exit_code") != 0
            or clean_preflight.get("timed_out") is not False
            or clean_preflight.get("process_group_remaining") is not False
            or not isinstance(fixture_preflight, dict)
            or fixture_preflight.get("mode") != expected_preflight_mode
            or fixture_preflight.get("command_signature") != f"validate_agent_docs.py {expected_preflight_mode} --root <prepared-workspace>"
            or fixture_preflight.get("status") != "observed_failure"
            or type(fixture_preflight.get("exit_code")) is not int
            or fixture_preflight["exit_code"] == 0
            or fixture_preflight.get("timed_out") is not False
            or fixture_preflight.get("process_group_remaining") is not False
        ):
            failures.append("all-mode baseline lacks independent clean-source and seeded-defect preflight evidence")
    writes = _doc_write_sequences(actions, target, harness, workspace)
    if not writes:
        failures.append("native trace has no correlated documentation write/change action")
    if baseline_action is not None and final_action is not None:
        baseline_seq = result_sequence(baseline_action)
        final_seq = result_sequence(final_action)
        first_write = min(writes) if writes else None
        last_write = max(writes) if writes else None
        if baseline_seq is None or first_write is None or last_write is None or final_seq is None:
            failures.append("baseline failure, contribution write, and final success are not natively ordered")
        elif not (baseline_seq < first_write and last_write < final_seq):
            failures.append("baseline failure, contribution write, and final success are not natively ordered")
        if _native_key(baseline_action) is None or _native_key(baseline_action) == _native_key(final_action):
            failures.append("baseline and final validator results lack distinct native call identities")

    if trial.get("fresh_native_invocation") is not True or trial.get("execution_mode") != "fresh-native-run":
        failures.append("trial record does not establish one fresh native harness invocation")
    if type(trial.get("harness_exit_code")) is not int or trial.get("harness_exit_code") != 0:
        failures.append("native harness process did not report an explicit successful exit")
    if trial.get("process_group_remaining") is not False:
        failures.append("native harness process group is still alive or its status is unknown")
    if trial.get("harness_timed_out") is not False:
        failures.append("native harness invocation timed out or timeout status is unknown")
    if not isinstance(trial.get("session_id_sha256"), str):
        failures.append("native event session id was not observed")
    profile = trial.get("child_profile")
    if not isinstance(profile, dict):
        failures.append("native child sandbox/profile evidence is missing")
    elif harness == "opencode":
        if profile.get("effective_matrix_verified") is not True or profile.get("project_runtime_trees_absent_after_native_run") is not True:
            failures.append("OpenCode effective named-agent profile or clean project tree was not verified")
    elif profile.get("sandbox") != "workspace-write" or profile.get("no_daemon") is not True:
        failures.append("Codex native sandbox or no-daemon invocation record is incomplete")
    for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
        if not (workspace / name).is_file() or (workspace / name).is_symlink():
            failures.append(f"required root route file is missing or unsafe: {name}")
    if any(
        path.is_symlink() for path in workspace.rglob("*")
        if ".git" not in path.relative_to(workspace).parts
    ):
        failures.append("workspace contains a symlink; isolation diff is not complete")

    validator_problems = _validator_closure_problems(workspace, manifest, initial, current, target)
    failures.extend(validator_problems)
    import time
    if not run_independent:
        independent = {"status": "not_run", "exit_code": None, "executed": False,
                       "reason": "synthetic check disabled independent execution"}
    elif validator_problems:
        independent = {
            "status": "refused_validator_dependency_drift", "exit_code": None,
            "executed": False, "reason": "frozen validator/schema dependency closure changed",
        }
    else:
        independent = _run_independent_validation(workspace)
    independent_finished = time.time()
    if run_independent and independent.get("status") != "pass":
        failures.append("independent truthfulness and links validation did not explicitly pass")

    started_epoch = trial.get("started_epoch")
    started_valid = type(started_epoch) in {int, float} and math.isfinite(started_epoch) and started_epoch > 0
    if not started_valid:
        failures.append("trial start time is missing or malformed")
    if run_independent and independent.get("status") == "pass" and started_valid and not failures:
        verified_seconds = max(0.0, independent_finished - float(started_epoch))
        verified_metric = {
            "status": "verified",
            "elapsed_seconds": round(verified_seconds, 3),
            "measurement": "elapsed_at_verification_upper_bound",
            "exact_completion_time": False,
            "verification_at_epoch": independent_finished,
        }
    else:
        verified_metric = {
            "status": "unknown", "elapsed_seconds": None,
            "measurement": "elapsed_at_verification_upper_bound",
            "exact_completion_time": False,
        }
    timing = _first_meaningful_action(
        actions, harness, workspace,
        float(started_epoch) if started_valid else None,
        target,
    )
    metrics = _setup_metrics(
        actions, harness, workspace, bool(normalized.get("trace_complete")),
        normalized.get("clarification_observations"),
    )
    metrics["time_to_first_meaningful_action"] = timing
    metrics["time_to_first_verified_completed_contribution"] = verified_metric
    metrics["unsupported_structured_claims"] = {
        "count": unsupported,
        "status": "observed" if normalized.get("trace_complete") else "unknown",
    }
    metrics["validation_outcome"] = {
        "agent_baseline": "observed_failure" if baseline_action is not None else "unknown",
        "agent_final": "observed_pass" if final_action is not None and explicit_exit_code(final_action, harness) == 0 else "unknown",
        "independent_final": independent.get("status", "unknown"),
    }
    metrics["artifact_diff"] = {
        "changed_file_count": len(changes),
        "changed_paths": [item["path"] for item in changes],
        "sha256_evidence": changes,
    }
    review = completion_result.get("completion", {}).get("human_review", {}) if isinstance(completion_result.get("completion"), dict) else {}
    from bridge_core import redact_secret_shapes
    sanitized_review = {}
    for key in ("summary", "rationale"):
        value = review.get(key) if isinstance(review, dict) else None
        if isinstance(value, str):
            text = redact_secret_shapes(value)
            text = text.replace(str(repo), "<REPO>").replace(str(workspace), "<WORKSPACE>")
            text = text.replace(str(Path.home()), "<HOME>")
            sanitized_review[key] = text[:4000]
    if not sanitized_review:
        sanitized_review = {"status": "unavailable"}

    diagnostics = build_failure_diagnostics(
        failures,
        actions,
        harness,
        scope="docs",
        artifact_paths=(COMPLETION_REL, target),
        causal_actions=causal_actions,
        repo=repo,
        workspace=workspace,
    )
    if run_independent and independent.get("status") == "unknown":
        diagnostics.append(runtime_unavailable_diagnostic(
            "Independent validator runtime was unavailable or could not be observed.",
            repo=repo,
        ))

    return {
        "series_id": manifest["series_id"],
        "series_manifest_sha256": manifest_hash,
        "case_id": case_id,
        "harness": harness,
        "evidence_scope": "synthetic-regression-only" if synthetic else "fresh-native-trial",
        "success": not failures,
        "status": "pass" if not failures else "fail",
        "failure_reasons": failures,
        "diagnostics": diagnostics,
        "native_trace": {
            "trace_complete": normalized.get("trace_complete"),
            "events_observed": normalized.get("events_observed"),
            "actions_observed": len(actions),
            "raw_events_sha256": trial.get("raw_events_sha256"),
            "direct_suite_access_indices": raw_accesses,
            "direct_suite_access_audit": "detected" if raw_accesses else "complete_no_direct_access" if access_audit_complete else "unknown",
        },
        "validation": {
            "baseline_native_item_id_sha256": _native_key(baseline_action) if baseline_action else None,
            "final_native_item_id_sha256": _native_key(final_action) if final_action else None,
            "baseline_outcome_provenance": _validation_outcome_for_action(baseline_action, harness, workspace),
            "final_outcome_provenance": _validation_outcome_for_action(final_action, harness, workspace),
            "observed_validator_outcomes": _validation_outcome_records(actions, harness, workspace),
            "completion_check": completion_check_evidence,
            "independent_final": independent,
        },
        "entry_route": completion_result.get("entry_route"),
        "artifact_refs": {
            COMPLETION_REL: current.get(COMPLETION_REL),
            target: current.get(target),
        },
        "artifact_diff": changes,
        "metrics": metrics,
        "human_review": {
            "automated_quality_assessment": "not_performed",
            "summary": sanitized_review.get("summary"),
            "rationale": sanitized_review.get("rationale"),
        },
        "synthetic_regression": bool(synthetic),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, help="sanitized v2 trial record under ignored run evidence")
    parser.add_argument("--workspace", required=True, help="the isolated workspace for this run")
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--regrade-label", default=None,
                        help="write a separately labeled regrade; never replace the original verdict")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    trial_path = Path(args.trial)
    if not trial_path.is_absolute():
        trial_path = repo / trial_path
    try:
        if trial_path.is_symlink() or not trial_path.is_file() or not trial_path.resolve().is_relative_to(repo):
            raise GradeError("trial path must be a regular file inside the repository")
        relative = trial_path.resolve().relative_to(repo)
        if relative.parts[:3] != (".tmp", "native-contribution-wave", "runs"):
            raise GradeError("trial record must be inside the ignored v2 run directory")
        trial = read_json(trial_path)
        verdict = grade_trial(repo, Path(args.workspace), trial)
        if args.regrade_label is None:
            output = trial_path.parent / "verdict.json"
        else:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.regrade_label):
                raise GradeError("regrade label must be a short safe identifier")
            verdict["regrade_label"] = args.regrade_label
            output = trial_path.parent / f"verdict.regrade.{args.regrade_label}.json"
        exclusive_json(output, verdict)
    except (OSError, ValueError, GradeError, ManifestError, PathSafetyError) as exc:
        message = f"v2 grade failed: {type(exc).__name__}"
        diagnostics = build_failure_diagnostics(
            [message], [], "unknown", scope="docs", repo=repo,
        )
        print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
        return 2
    print(json.dumps({
        "success": verdict["success"],
        "verdict": output.relative_to(repo).as_posix(),
        "failure_count": len(verdict["failure_reasons"]),
        "diagnostics": verdict.get("diagnostics", []),
        "entry_route": verdict.get("entry_route"),
    }, indent=2))
    return 0 if verdict["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
