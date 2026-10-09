"""Normalize OpenCode tool events into bounded, allowlisted action evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable

ACTION_COMMAND_LIMIT = 300
OUTPUT_HASH_BYTE_LIMIT = 1_048_576
OUTCOME_SCHEMA_VERSION = 1

PUBLIC_TOOL_NAMES = {
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "list",
    "lsp",
    "patch",
    "python",
    "question",
    "read",
    "shell",
    "skill",
    "task",
    "todowrite",
    "webfetch",
    "websearch",
    "write",
}
STATUS_ALIASES = {
    "completed": "completed",
    "error": "error",
    "failed": "error",
    "denied": "denied",
    "permission_denied": "denied",
    "rejected": "denied",
    "blocked": "denied",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "timeout": "timeout",
    "timed_out": "timeout",
    "running": "running",
    "pending": "pending",
    "skipped": "skipped",
}
TERMINAL_STATUSES = {"completed", "error", "denied", "cancelled", "timeout", "skipped"}
SHELL_TOOLS = {"bash", "shell"}
PATCH_FILE_HEADER = re.compile(r"(?m)^\*\*\* (?:Add|Update|Delete) File: ([^\r\n]+)$")


def is_native_tool_event(event: object) -> bool:
    if not isinstance(event, dict) or event.get("type") not in {"tool_use", "tool_result"}:
        return False
    part = event.get("part")
    return isinstance(part, dict) and part.get("type") == "tool" and isinstance(part.get("tool"), str)


def parse_native_events(output: str) -> list[dict]:
    """Parse newline-delimited OpenCode JSON without retaining non-JSON text."""
    events: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            events.append(payload)
    return events


def _safe_text(
    value: str,
    replacements: tuple[tuple[str, str], ...],
    redact: Callable[[str], str],
) -> str:
    text = redact(value)
    for original, replacement in replacements:
        if original:
            text = text.replace(original, replacement)
    return text


def _tool_input(tool: str, state: dict) -> str | None:
    value = state.get("input")
    if not isinstance(value, dict):
        return None
    if tool in SHELL_TOOLS:
        for key in ("command", "cmd"):
            command = value.get(key)
            if isinstance(command, str):
                return command
        return None
    if tool == "apply_patch":
        patch_text = value.get("patchText")
        if not isinstance(patch_text, str):
            return None
        paths = PATCH_FILE_HEADER.findall(patch_text)
        return paths[0] if len(paths) == 1 else None
    for key in ("filePath", "file_path", "path"):
        path = value.get(key)
        if isinstance(path, str):
            return path
    return None


def _status(state: dict) -> str:
    if "status" not in state:
        return "missing"
    value = state.get("status")
    if not isinstance(value, str):
        return "unknown"
    return STATUS_ALIASES.get(value.casefold(), "unknown")


def _output_digest(
    state: dict,
    replacements: tuple[tuple[str, str], ...],
    redact: Callable[[str], str],
) -> tuple[str, int, str] | None:
    output = state.get("output")
    if isinstance(output, str):
        safe_output = _safe_text(output, replacements, redact)
    elif isinstance(output, (dict, list)):
        try:
            serialized = json.dumps(
                output,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            return None
        safe_output = _safe_text(serialized, replacements, redact)
    else:
        return None
    encoded = safe_output.encode("utf-8")
    if len(encoded) > OUTPUT_HASH_BYTE_LIMIT:
        return None, len(encoded), "too_large"
    return hashlib.sha256(encoded).hexdigest(), len(encoded), "observed"


def _output_type(state: dict) -> str:
    """Record only the native output framing type, never its freeform value."""
    if "output" not in state:
        return "missing"
    value = state.get("output")
    if isinstance(value, str):
        return "text"
    if isinstance(value, (dict, list)):
        return "structured"
    if value is None:
        return "null"
    if type(value) in {bool, int, float}:
        return "scalar"
    return "unknown"


def normalize_tool_actions(
    events: list,
    *,
    replacements: tuple[tuple[str, str], ...] = (),
    redact: Callable[[str], str] | None = None,
) -> list[dict]:
    """Correlate OpenCode tool-state updates by callID and keep public fields only.

    Native `state.metadata.exit` is the only accepted shell exit-code source.
    In particular, a `completed` status or text output never implies exit 0.
    Calls lacking IDs remain separate event observations and are not joined by
    matching or truncated command strings.
    """
    redact = redact or (lambda value: value)
    grouped: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []

    for event_index, event in enumerate(events):
        if not is_native_tool_event(event):
            continue
        part = event["part"]
        raw_tool = part["tool"].casefold()
        tool = raw_tool if raw_tool in PUBLIC_TOOL_NAMES else "other"
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        raw_call_id = part.get("callID")
        has_call_id = isinstance(raw_call_id, str) and bool(raw_call_id)
        group_key = ("call", raw_call_id) if has_call_id else ("event", str(event_index))
        command = _tool_input(raw_tool, state)
        safe_command = (
            _safe_text(command, replacements, redact) if isinstance(command, str) else None
        )

        action = grouped.get(group_key)
        if action is None:
            action = {
                "tool": tool,
                "raw_tool": raw_tool,
                "native_tool_name": raw_tool if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", raw_tool) else "unknown",
                "raw_command": command,
                "command_conflict": False,
                "tool_conflict": False,
                "event_sequence": event_index,
                "native_tool_call_id_sha256": (
                    hashlib.sha256(raw_call_id.encode("utf-8")).hexdigest() if has_call_id else None
                ),
                "native_tool_status": "missing",
                "status_rank": 0,
                "shell_exit_code_status": "not_applicable" if raw_tool not in SHELL_TOOLS else "missing",
                "shell_exit_code": None,
                "exit_conflict": False,
                "output_sha256": None,
                "output_bytes": None,
                "output_hash_status": "missing",
                "native_output_type": "missing",
                "updates": 0,
            }
            grouped[group_key] = action
            order.append(group_key)
        else:
            if action["raw_tool"] != raw_tool:
                action["tool_conflict"] = True
            if command is not None:
                if action["raw_command"] is None:
                    action["raw_command"] = command
                elif action["raw_command"] != command:
                    action["command_conflict"] = True

        action["updates"] += 1
        output_type = _output_type(state)
        if output_type != "missing":
            if action["native_output_type"] == "missing":
                action["native_output_type"] = output_type
            elif action["native_output_type"] != output_type:
                action["native_output_type"] = "unknown"

        status = _status(state)
        rank = 2 if status in TERMINAL_STATUSES else 1 if status not in {"missing", "unknown"} else 0
        if rank and (action["status_rank"] < 2 or rank == 2):
            action["native_tool_status"] = status
            action["status_rank"] = rank
        elif status == "unknown" and action["native_tool_status"] == "missing":
            action["native_tool_status"] = "unknown"

        metadata = state.get("metadata")
        if raw_tool in SHELL_TOOLS and isinstance(metadata, dict) and "exit" in metadata:
            exit_code = metadata.get("exit")
            if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                if action["shell_exit_code_status"] == "observed" and action["shell_exit_code"] != exit_code:
                    action["shell_exit_code_status"] = "unknown"
                    action["shell_exit_code"] = None
                    action["exit_conflict"] = True
                elif not action["exit_conflict"]:
                    action["shell_exit_code_status"] = "observed"
                    action["shell_exit_code"] = exit_code
            else:
                action["shell_exit_code_status"] = "unknown"
                action["shell_exit_code"] = None

        digest = _output_digest(state, replacements, redact)
        if digest is not None:
            output_sha, output_bytes, output_status = digest
            action["output_sha256"] = output_sha
            action["output_bytes"] = output_bytes
            action["output_hash_status"] = output_status
        elif isinstance(state.get("output"), str):
            action["output_sha256"] = None
            action["output_bytes"] = len(state["output"].encode("utf-8"))
            action["output_hash_status"] = "too_large"

    results: list[dict] = []
    for group_key in order:
        item = grouped[group_key]
        item.pop("status_rank", None)
        raw_command = item.pop("raw_command")
        item.pop("raw_tool")
        command_conflict = bool(item.pop("command_conflict"))
        tool_conflict = bool(item.pop("tool_conflict"))
        exit_conflict = bool(item.pop("exit_conflict"))
        updates = item.pop("updates")

        safe_command = (
            _safe_text(raw_command, replacements, redact) if isinstance(raw_command, str) else None
        )
        if command_conflict or tool_conflict:
            safe_command = None
        if safe_command is not None:
            encoded_command = safe_command.encode("utf-8")
            item["command"] = safe_command[:ACTION_COMMAND_LIMIT]
            item["command_sha256"] = hashlib.sha256(encoded_command).hexdigest()
            item["command_bytes"] = len(encoded_command)
            item["command_truncated"] = len(safe_command) > ACTION_COMMAND_LIMIT
        else:
            item["command"] = None
        if command_conflict or tool_conflict or exit_conflict:
            item["correlation_conflict"] = True
        if updates > 1:
            item["correlated_event_updates"] = updates

        outcome = {
            "native_tool_status": item.pop("native_tool_status"),
            "shell_exit_code_status": item.pop("shell_exit_code_status"),
            "output_hash_status": item.pop("output_hash_status"),
            "output_bytes": item.pop("output_bytes"),
            "output_sha256": item.pop("output_sha256"),
            "native_output_type": item.pop("native_output_type"),
        }
        exit_status = outcome["shell_exit_code_status"]
        if exit_status == "observed":
            outcome["shell_exit_code"] = item.pop("shell_exit_code")
        else:
            item.pop("shell_exit_code")
        item["outcome_schema_version"] = OUTCOME_SCHEMA_VERSION
        item["outcome"] = outcome
        results.append(item)
    return results


def selfcheck() -> list[str]:
    """Focused synthetic regressions for IDs, exit metadata, and truncation."""
    failures: list[str] = []
    long_prefix = "x" * ACTION_COMMAND_LIMIT
    events = [
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": "call-a",
                "state": {"status": "running", "input": {"command": "python check.py"}},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": "call-a",
                "state": {
                    "status": "completed", "input": {"command": "python check.py"},
                    "metadata": {"exit": 1, "credential": "must-not-be-copied"},
                    "output": "tool output is hashed, never copied",
                },
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": "call-b",
                "state": {"status": "completed", "input": {"command": long_prefix + "A"}},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": "call-c",
                "state": {"status": "completed", "input": {"command": long_prefix + "B"}},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": "call-d",
                "state": {"status": "completed", "input": {"command": "true"}, "output": "exit 0"},
            },
        },
    ]
    actions = normalize_tool_actions(events)
    if len(actions) != 4:
        failures.append("callID updates were not correlated or distinct calls were collapsed")
        return failures
    if actions[0]["outcome"]["native_tool_status"] != "completed":
        failures.append("native completed tool status was not preserved")
    if any("status_rank" in action for action in actions):
        failures.append("internal status ranking leaked into the public normalized action projection")
    if actions[0]["outcome"].get("shell_exit_code") != 1:
        failures.append("explicit native shell exit metadata was not preserved")
    if actions[1]["command"] != actions[2]["command"] or actions[1]["command_sha256"] == actions[2]["command_sha256"]:
        failures.append("distinct long commands collided after bounded preview truncation")
    if actions[3]["outcome"]["shell_exit_code_status"] != "missing" or "shell_exit_code" in actions[3]["outcome"]:
        failures.append("completed status or echoed output was incorrectly upgraded to shell exit 0")
    if "credential" in repr(actions) or "tool output" in repr(actions):
        failures.append("provider/result text or unallowlisted metadata escaped the normalized action record")
    if actions[0]["native_tool_call_id_sha256"] == actions[1]["native_tool_call_id_sha256"]:
        failures.append("separate native tool-call IDs were not kept distinct")

    status_events = []
    for index, status in enumerate(("denied", "cancelled", "timeout", None, "unmapped-native-status")):
        state = {"input": {"command": "true"}}
        if status is not None:
            state["status"] = status
        status_events.append({
            "type": "tool_use",
            "part": {"type": "tool", "tool": "bash", "callID": f"status-{index}", "state": state},
        })
    statuses = [
        action["outcome"]["native_tool_status"]
        for action in normalize_tool_actions(status_events)
    ]
    if statuses != ["denied", "cancelled", "timeout", "missing", "unknown"]:
        failures.append("denial, cancellation, timeout, missing status, and unknown status were conflated")

    invalid_exit = normalize_tool_actions([{
        "type": "tool_use",
        "part": {
            "type": "tool", "tool": "bash", "callID": "bad-exit",
            "state": {"status": "completed", "input": {"command": "true"}, "metadata": {"exit": "0"}},
        },
    }])[0]
    if invalid_exit["outcome"]["shell_exit_code_status"] != "unknown" or "shell_exit_code" in invalid_exit["outcome"]:
        failures.append("malformed native exit metadata was converted to an integer success code")
    return failures
