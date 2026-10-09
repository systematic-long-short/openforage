"""Small allowlisted readers for Codex JSONL lifecycle fixture events."""

from __future__ import annotations

import json
import re

TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
STATUS_ALIASES = {
    "completed": "completed",
    "complete": "completed",
    "success": "completed",
    "succeeded": "completed",
    "failed": "failed",
    "error": "error",
    "denied": "denied",
    "rejected": "denied",
    "running": "running",
    "in_progress": "running",
    "pending": "pending",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
ITEM_STATUS_BY_EVENT = {
    "item.started": "running",
    "item.completed": "completed",
    "item.failed": "failed",
    "item.cancelled": "cancelled",
}
TOOL_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "function_call",
    "mcp_tool_call",
    "tool_call",
    "web_search_call",
}


def parse_jsonl(text: str) -> list[dict]:
    """Keep JSON object events only; prose and malformed lines are not evidence."""
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _valid_thread_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    thread_id = value.strip()
    if not thread_id or len(thread_id) > 256 or any(char.isspace() for char in thread_id):
        return None
    return thread_id


def extract_thread_id(events: list[dict]) -> str | None:
    """Read a thread id from a native thread event, preferring thread.started."""
    for event in events:
        if event.get("type") != "thread.started":
            continue
        thread_id = _valid_thread_id(event.get("thread_id", event.get("threadId")))
        if thread_id is not None:
            return thread_id
    for event in events:
        if not str(event.get("type", "")).startswith("thread."):
            continue
        thread_id = _valid_thread_id(event.get("thread_id", event.get("threadId")))
        if thread_id is not None:
            return thread_id
    return None


def _message_text(item: dict) -> str | None:
    for key in ("text", "message"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        if pieces:
            return "".join(pieces)
    return None


def extract_final_message(events: list[dict]) -> str | None:
    """Return the last completed native agent-message item, never model-side prose elsewhere."""
    final_message = None
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        message = _message_text(item)
        if isinstance(message, str):
            final_message = message
    return final_message


def parse_final_json(events: list[dict]) -> dict | None:
    message = extract_final_message(events)
    if message is None:
        return None
    try:
        value = json.loads(message)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _normalized_status(event: dict, item: dict | None) -> str | None:
    for source in (item, event):
        if not isinstance(source, dict):
            continue
        value = source.get("status")
        if isinstance(value, str):
            normalized = STATUS_ALIASES.get(value.casefold())
            if normalized is not None:
                return normalized
    event_type = event.get("type")
    if isinstance(event_type, str):
        return ITEM_STATUS_BY_EVENT.get(event_type)
    return None


def explicit_tool_observations(events: list[dict], *, limit: int = 100) -> list[dict]:
    """Project only native events with an explicit tool identity and status."""
    observations: list[dict] = []
    for event in events:
        event_type = event.get("type")
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        tool_name = None
        if isinstance(item, dict):
            item_type = item.get("type")
            identity = next(
                (item.get(key) for key in ("tool_name", "tool", "name") if isinstance(item.get(key), str)),
                None,
            )
            if identity is not None:
                tool_name = identity
            elif isinstance(item_type, str) and item_type in TOOL_ITEM_TYPES:
                tool_name = item_type
        if tool_name is None and isinstance(event.get("tool_name"), str):
            tool_name = event["tool_name"]
        if tool_name is None and isinstance(event.get("tool"), str):
            tool_name = event["tool"]
        if not isinstance(tool_name, str) or TOOL_NAME_RE.fullmatch(tool_name) is None:
            continue
        status = _normalized_status(event, item)
        if status is None:
            continue
        observations.append({"tool": tool_name, "status": status})
        if len(observations) >= limit:
            break
    return observations


def readiness_ack_matches(message: dict | None, event_id: str) -> bool:
    return (
        isinstance(message, dict)
        and set(message) == {"ready", "event_id"}
        and message.get("ready") is True
        and message.get("event_id") == event_id
    )


def history_ack_matches(message: dict | None, event_id: str, marker: str) -> bool:
    return (
        isinstance(message, dict)
        and set(message) == {"event_id", "recalled_marker"}
        and message.get("event_id") == event_id
        and message.get("recalled_marker") == marker
    )


def exact_resume_matches(target_id: str | None, recorded_child_id: str | None, returned_id: str | None) -> bool:
    return bool(
        target_id
        and recorded_child_id
        and returned_id
        and target_id == recorded_child_id
        and returned_id == target_id
    )


def selfcheck() -> list[str]:
    """Inert synthetic-event checks; never launches Codex or labels a native run."""
    failures: list[str] = []
    synthetic = [
        {"type": "thread.started", "thread_id": "thread-synthetic-child"},
        {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "name": "create_goal", "status": "completed"},
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"event_id":"evt-selfcheck","ready":true}'},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 999}},
    ]
    parsed = parse_jsonl("\n".join(json.dumps(event) for event in synthetic))
    if extract_thread_id(parsed) != "thread-synthetic-child":
        failures.append("synthetic native thread id was not extracted")
    if readiness_ack_matches(parse_final_json(parsed), "evt-selfcheck") is not True:
        failures.append("synthetic structured readiness acknowledgement did not validate")
    if extract_thread_id([{"type": "turn.completed", "thread_id": "not-a-thread-event"}]) is not None:
        failures.append("a thread id outside a native thread event was accepted")
    if extract_thread_id([]) is not None:
        failures.append("a missing native thread id was accepted")
    if readiness_ack_matches({"event_id": "wrong", "ready": True}, "evt-selfcheck"):
        failures.append("a wrong readiness event id was accepted")
    if readiness_ack_matches({"ready": True}, "evt-selfcheck"):
        failures.append("a missing readiness event id was accepted")
    if history_ack_matches(
        {"event_id": "evt-selfcheck", "recalled_marker": "mismatched"},
        "evt-selfcheck",
        "marker-expected",
    ):
        failures.append("a mismatched history marker acknowledgement was accepted")
    if history_ack_matches(
        {"event_id": "wrong", "recalled_marker": "marker-expected"},
        "evt-selfcheck",
        "marker-expected",
    ):
        failures.append("a wrong history event id was accepted")
    if exact_resume_matches("thread-wrong", "thread-child", "thread-child"):
        failures.append("a resume to a non-recorded target was accepted")
    if exact_resume_matches("thread-child", "thread-child", "thread-wrong"):
        failures.append("a mismatched returned thread id was accepted")
    if exact_resume_matches("thread-child", "thread-child", "thread-child") is not True:
        failures.append("a matching exact-thread continuation was rejected")
    observations = explicit_tool_observations(parsed)
    if observations != [{"tool": "create_goal", "status": "completed"}]:
        failures.append("native tool identity/status projection retained missing or unrelated event fields")
    if "usage" in repr(observations) or "999" in repr(observations):
        failures.append("provider usage metadata escaped the native-tool allowlist")
    if explicit_tool_observations([{
        "type": "item.output",
        "item": {"type": "mcp_tool_call", "name": "create_goal"},
    }]):
        failures.append("a native tool event without an explicit status was accepted")
    malformed_items = [
        {"type": "item.completed", "item": {"type": ["mcp_tool_call"], "status": "completed"}},
        {"type": "item.completed", "item": {"type": {"kind": "mcp_tool_call"}, "status": "completed"}},
    ]
    try:
        malformed_observations = explicit_tool_observations(malformed_items)
    except TypeError:
        failures.append("malformed native item.type prevented a failed fixture record")
    else:
        if malformed_observations:
            failures.append("malformed native item.type was treated as known tool evidence")
    return failures
