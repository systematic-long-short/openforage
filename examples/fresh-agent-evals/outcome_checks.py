"""Grade claimed baseline outcomes against versioned native tool evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "opencode-lifecycle"))

from discovery_checks import is_standalone_shell_command, matching_validator_actions  # noqa: E402
from trace_evidence import normalized_actions_match, retained_raw_actions  # noqa: E402

OUTCOME_SCHEMA_VERSION = 1
NATIVE_EXECUTION_MODES = {"fresh-opencode-run", "events-file-replay"}
SYNTHETIC_EXECUTION_MODE = "inert-grader-regression"


def _versioned_shell_result(action: object) -> tuple[int | None, str | None]:
    if (
        not isinstance(action, dict)
        or type(action.get("outcome_schema_version")) is not int
        or action.get("outcome_schema_version") != OUTCOME_SCHEMA_VERSION
    ):
        return None, "unknown: action has no versioned native result fields"
    tool = action.get("tool")
    if not isinstance(tool, str) or tool not in {"bash", "shell"}:
        return None, "unknown: result action is not a native shell invocation"
    if action.get("correlation_conflict", False) is not False:
        return None, "unknown: native action has a command, tool, or result correlation conflict"
    command = action.get("command")
    if action.get("command_truncated") is not False or not is_standalone_shell_command(command):
        return None, "unknown: full standalone validator command cannot be established"
    encoded_command = command.encode("utf-8")
    if (
        action.get("command_sha256") != hashlib.sha256(encoded_command).hexdigest()
        or type(action.get("command_bytes")) is not int
        or action.get("command_bytes") != len(encoded_command)
    ):
        return None, "unknown: command preview does not match its recorded digest and length"
    outcome = action.get("outcome")
    if not isinstance(outcome, dict):
        return None, "unknown: versioned native outcome object is absent"
    status = outcome.get("native_tool_status")
    if not isinstance(status, str) or status not in {"completed", "error"}:
        return None, f"unknown: native tool status is {status!r}, not a command result"
    if outcome.get("shell_exit_code_status") != "observed":
        return None, "unknown: native result metadata did not expose a shell exit code"
    code = outcome.get("shell_exit_code")
    if not isinstance(code, int) or isinstance(code, bool):
        return None, "unknown: shell exit-code field is missing or malformed"
    if (
        not isinstance(action.get("native_tool_call_id_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", action["native_tool_call_id_sha256"]) is None
    ):
        return None, "unknown: native tool-call ID is absent, so command/result correlation is not proven"
    return code, None


def _find_first_match(
    command: str,
    actions: list,
    repo: Path,
    inspection_only: Callable[[str], bool],
) -> dict | None:
    matches = matching_validator_actions(
        command,
        actions,
        repo,
        inspection_only,
        standalone_only=True,
    )
    return matches[0] if matches else None


def check_baseline_outcome(
    repo: Path,
    case: dict,
    workspace: Path,
    trial: dict | None,
    inspection_only: Callable[[str], bool],
) -> list[str]:
    """Require baseline status and exit code to pair with one observed command.

    Legacy action-only records stay unknown: a validator invocation is not a
    result. Actual runner/replay records additionally have their normalized
    evidence checked against the retained raw OpenCode event stream.
    """
    if case.get("case_id") != "discovery-contribution-cold-start":
        return []
    artifact = workspace / "trial-output/contribution-discovery.json"
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["baseline outcome is unknown: contribution artifact is missing or invalid JSON"]
    if not isinstance(data, dict):
        return ["baseline outcome is unknown: contribution artifact is not an object"]
    command = data.get("baseline_command")
    if not isinstance(command, str) or not command.strip():
        return ["baseline outcome is unknown: no baseline validator command was reported"]
    if not isinstance(trial, dict):
        return ["baseline outcome is unknown: trial record has no recognized execution mode"]
    mode = trial.get("execution_mode")
    synthetic = mode == SYNTHETIC_EXECUTION_MODE and trial.get("grader_regression_only") is True
    if not synthetic and (not isinstance(mode, str) or mode not in NATIVE_EXECUTION_MODES):
        return ["baseline outcome is unknown: execution mode cannot establish a native result"]
    if mode == "fresh-opencode-run" and trial.get("fresh_separate_invocation") is not True:
        return ["baseline outcome is unknown: fresh native invocation is not established"]
    actions = trial.get("observed_actions", [])
    if not isinstance(actions, list):
        return ["baseline outcome is unknown: normalized action list is malformed"]
    recorded = _find_first_match(command, actions, repo, inspection_only)
    if recorded is None:
        return ["baseline command/result pairing failed: no matching native validator invocation was observed"]
    code, unknown = _versioned_shell_result(recorded)
    if unknown:
        return [f"baseline outcome is {unknown}"]

    claimed_code = data.get("baseline_shell_exit_code")
    claimed_status = data.get("baseline_result")
    actual_status = "pass" if code == 0 else "fail"
    if not isinstance(claimed_code, int) or isinstance(claimed_code, bool) or claimed_code != code:
        return ["baseline command/result pairing failed: reported shell exit code differs from its native result"]
    if claimed_status != actual_status:
        return [
            f"baseline command/result pairing failed: native exit code indicates {actual_status}, "
            f"but artifact reports {claimed_status!r}"
        ]

    if not synthetic:
        raw_actions, raw_error = retained_raw_actions(repo, workspace, trial)
        if raw_error:
            return [f"baseline native result is unverified: {raw_error}"]
        raw_action = _find_first_match(command, raw_actions or [], repo, inspection_only)
        if raw_action is None:
            return ["baseline native result is unverified: retained raw trace has no matching validator invocation"]
        raw_code, raw_unknown = _versioned_shell_result(raw_action)
        if raw_unknown:
            return [f"baseline native result is unverified: retained trace {raw_unknown}"]
        if not normalized_actions_match(raw_action, recorded) or raw_code != code:
            return ["baseline command/result pairing failed: normalized result does not match the retained native event"]
    return []
