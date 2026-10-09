"""Consistency checks between retained native events and normalized actions."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "opencode-lifecycle"))

from bridge_core import redact_secret_shapes
from tool_outcomes import normalize_tool_actions, parse_native_events


ACTION_COMPARISON_FIELDS = (
    "tool",
    "command",
    "command_sha256",
    "command_bytes",
    "command_truncated",
    "native_tool_call_id_sha256",
    "event_sequence",
    "outcome_schema_version",
    "outcome",
    "correlation_conflict",
    "correlated_event_updates",
)


def retained_raw_actions(
    repo: Path,
    workspace: Path,
    trial: dict,
) -> tuple[list[dict] | None, str | None]:
    """Load normalized actions only when the retained raw stream matches its digest."""
    extraction = trial.get("action_extraction")
    if not isinstance(extraction, dict):
        return None, "raw native trace reference is absent"
    relpath = extraction.get("raw_events_scratch")
    if not isinstance(relpath, str):
        return None, "raw native trace reference is absent"
    relative = Path(relpath)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != (".tmp", "fresh-agent-trials")
    ):
        return None, "raw native trace reference is outside the retained trial scratch directory"
    repo_root = repo.resolve()
    raw_path = (repo_root / relative).resolve()
    if not raw_path.is_relative_to(repo_root):
        return None, "raw native trace reference escapes the repository"
    try:
        raw_bytes = raw_path.read_bytes()
    except OSError:
        return None, "retained raw native trace is unavailable"
    expected = extraction.get("raw_events_sha256")
    if not isinstance(expected, str) or hashlib.sha256(raw_bytes).hexdigest() != expected:
        return None, "retained raw native trace does not match the recorded digest"
    try:
        events = parse_native_events(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return None, "retained raw native trace is not UTF-8 JSON output"
    replacements = (
        (str(workspace.resolve()), "<WORKSPACE>"),
        (str(repo_root), "<REPO>"),
        (str(Path.home()), "<HOME>"),
    )
    return normalize_tool_actions(
        events,
        replacements=replacements,
        redact=redact_secret_shapes,
    ), None


def _action_key(action: dict) -> tuple[str, object] | None:
    call_id = action.get("native_tool_call_id_sha256")
    if isinstance(call_id, str) and call_id:
        return "call", call_id
    sequence = action.get("event_sequence")
    if isinstance(sequence, int) and not isinstance(sequence, bool):
        return "event", sequence
    return None


def normalized_actions_match(expected: dict, recorded: dict) -> bool:
    missing = object()
    return all(
        expected.get(field, missing) == recorded.get(field, missing)
        for field in ACTION_COMPARISON_FIELDS
    )


def action_trace_consistency_problems(
    normalized_actions: object,
    raw_actions: object,
    tools: set[str],
) -> list[str]:
    """Require a one-to-one match for every normalized action in the selected tools."""
    if not isinstance(normalized_actions, list) or not isinstance(raw_actions, list):
        return ["normalized or retained action list is malformed"]

    def selected(actions: list) -> list[dict]:
        return [
            action
            for action in actions
            if isinstance(action, dict)
            and isinstance(action.get("tool"), str)
            and action["tool"].casefold() in tools
        ]

    normalized = selected(normalized_actions)
    raw = selected(raw_actions)
    if len(normalized) != len(raw):
        return ["normalized file-action count does not match the retained native trace"]

    raw_by_key: dict[tuple[str, object], list[dict]] = {}
    for action in raw:
        key = _action_key(action)
        if key is None:
            return ["retained file action has no native call identity"]
        raw_by_key.setdefault(key, []).append(action)

    used: set[tuple[str, object]] = set()
    for action in normalized:
        key = _action_key(action)
        if key is None or key in used:
            return ["normalized file action has a missing or duplicated native call identity"]
        candidates = raw_by_key.get(key, [])
        if len(candidates) != 1:
            return ["normalized file action has no unique retained native event"]
        if not normalized_actions_match(candidates[0], action):
            return ["normalized file action does not match its retained native event"]
        used.add(key)
    return []
