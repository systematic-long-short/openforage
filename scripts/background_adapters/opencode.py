"""OpenCode CLI adapter, extracted behind the shared adapter contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import (
    VARIANT_RE,
    AdapterError,
    PrimitiveDeclaration,
    SESSION_RE,
    event_reference,
    explicit_status,
    parse_jsonl_objects,
    tool_name,
    validate_binary,
    validate_common_argv,
    validate_model,
    validate_prompt,
    validate_session,
)


class OpenCodeAdapter:
    adapter_id = "opencode"
    product_name = "OpenCode"
    executable_name = "opencode"
    requires_fresh_session_id = False
    declared_primitives = (
        PrimitiveDeclaration(
            "goal_declaration", "unknown", basis="No goal-operation outcome is established by the retained proof."),
        PrimitiveDeclaration(
            "task_creation", "unknown", basis="The retained proof records a todowrite event, but not a distinct durable task creation and reread."),
        PrimitiveDeclaration(
            "task_list_write", "unknown", ("todowrite",),
            "The retained proof scoped todowrite only as a task/todo tool operation, not durable task storage.",
        ),
        PrimitiveDeclaration(
            "task_durable_reread", "unknown", basis="No correlated durable task reread is established by the retained proof."),
        PrimitiveDeclaration("memory_write", "unknown"),
        PrimitiveDeclaration("memory_durable_reread", "unknown"),
        PrimitiveDeclaration("native_scheduler_creation", "unknown"),
        PrimitiveDeclaration("native_scheduler_delivery", "unknown"),
    )

    def validate_selector(
        self, model: str, variant: str | None, reasoning_effort: str | None, effort: str | None,
    ) -> None:
        validate_model(model)
        if variant is None or VARIANT_RE.fullmatch(variant) is None:
            raise AdapterError("opencode_variant_required_or_invalid")
        if reasoning_effort is not None:
            raise AdapterError("codex_reasoning_effort_not_valid_for_opencode")
        if effort is not None:
            raise AdapterError("claude_effort_not_valid_for_opencode")

    def build_argv(
        self,
        *,
        binary: str,
        model: str,
        workspace: Path,
        prompt: str,
        variant: str | None,
        reasoning_effort: str | None,
        effort: str | None,
        session_id: str | None,
        fresh_session_id: str | None,
        profile: str,
    ) -> list[str]:
        self.validate_selector(model, variant, reasoning_effort, effort)
        validate_common_argv(
            binary=binary, model=model, prompt=prompt, session_id=session_id,
            fresh_session_id=fresh_session_id, profile=profile,
        )
        if fresh_session_id is not None:
            raise AdapterError("opencode_fresh_session_id_not_supported")
        argv = [binary, "run"]
        if profile == "isolated":
            argv.append("--pure")
        argv.extend([
            "--dir", str(workspace), "--model", model,
            "--variant", str(variant), "--agent", "build", "--format", "json",
        ])
        if session_id is not None:
            argv.extend(["--session", session_id])
        argv.append(prompt)
        return argv

    def version_probe_argv(self, binary: str) -> list[str]:
        validate_binary(binary)
        return [binary, "--version"]

    def help_probe_argv(self, binary: str) -> list[str]:
        validate_binary(binary)
        return [binary, "--help"]

    def parse_version(self, *, returncode: int, stdout: str, stderr: str) -> str:
        if returncode != 0:
            raise AdapterError("harness_version_probe_failed")
        lines = (stdout or stderr).strip().splitlines()
        if not lines:
            raise AdapterError("harness_version_unobserved")
        return lines[0][:120]

    def parse_probe(self, *, returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
        from .contract import probe_digest

        return {
            "outcome": "observed_success" if returncode == 0 else "observed_failure",
            "returncode": returncode,
            **probe_digest(stdout, stderr),
            "capability_use_inferred": False,
        }

    def extract_session_identity(self, events: list[dict[str, Any]]) -> tuple[str | None, bool]:
        found: list[str] = []
        for event in events:
            values = [
                candidate
                for key in ("sessionID", "session_id", "sessionId")
                if (candidate := _valid_session(event.get(key))) is not None
            ]
            if len(set(values)) > 1:
                return None, True
            if values:
                found.append(values[0])
        distinct = list(dict.fromkeys(found))
        return (distinct[0], False) if len(distinct) == 1 else (None, len(distinct) > 1)

    def normalize_trace(self, raw_text: str) -> dict[str, Any]:
        events, malformed = parse_jsonl_objects(raw_text)
        session_id, ambiguous = self.extract_session_identity(events)
        final_ack = _final_ack(events)
        native_tools = _tool_observations(events)
        return {
            "session_id": session_id,
            "identity_ambiguous": ambiguous,
            "final_ack": final_ack,
            "native_tools": native_tools,
            "event_count": len(events),
            "malformed_event_count": malformed,
            "final_message_present": final_ack is not None,
        }


def _valid_session(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if SESSION_RE.fullmatch(value) else None


def _final_ack(events: list[dict[str, Any]]) -> dict | None:
    groups: list[str] = []
    current = ""
    for event in events:
        part = event.get("part") if isinstance(event.get("part"), dict) else None
        text = None
        if event.get("type") == "text" and part and part.get("type") == "text":
            if isinstance(part.get("text"), str):
                text = part["text"]
        if text is None:
            if current:
                groups.append(current)
                current = ""
            continue
        current += text
    if current:
        groups.append(current)
    for message in reversed(groups):
        try:
            value = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _tool_observations(events: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        state = event.get("state") if isinstance(event.get("state"), dict) else {}
        part_state = part.get("state") if isinstance(part.get("state"), dict) else {}
        name = next(
            (
                source.get(key)
                for source in (event, part)
                for key in ("tool", "tool_name", "name")
                if isinstance(source.get(key), str)
            ),
            None,
        )
        normalized_name = tool_name(name)
        if normalized_name is None:
            continue
        status = next(
            (value for source in (event, state, part, part_state)
             if (value := explicit_status(source.get("status"))) is not None),
            None,
        )
        observations.append({
            "tool": normalized_name,
            "status": status,
            "event_type": event.get("type") if isinstance(event.get("type"), str) else "unknown",
            "event_ref": event_reference(event, index, part, state),
        })
        if len(observations) >= limit:
            break
    return observations
