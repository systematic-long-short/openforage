"""Claude Code CLI adapter using documented print/stream-json/exact-ID routes.

The external controller intentionally does not combine ``--bg`` with
``--print``. Native ``claude agents`` management is a separate documented
product route and is not treated as a verified controller implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import (
    EFFORT_RE,
    UUID_SESSION_RE,
    AdapterError,
    PrimitiveDeclaration,
    event_reference,
    parse_jsonl_objects,
    tool_name,
    validate_binary,
    validate_common_argv,
    validate_model,
    validate_prompt,
    validate_session,
    validate_uuid_session,
)

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class ClaudeCodeAdapter:
    adapter_id = "claude-code"
    product_name = "Claude Code"
    executable_name = "claude"
    requires_fresh_session_id = True

    # These declarations describe documented routes only. Runtime operation
    # evidence is generated solely from explicit native events with outcomes.
    declared_primitives = (
        PrimitiveDeclaration(
            "goal_declaration", "documented", basis="Official Claude Code goal documentation describes session-scoped /goal controls; no goal use is inferred here."),
        PrimitiveDeclaration(
            "task_creation", "documented", ("TaskCreate",),
            "Official tools reference names TaskCreate; models differ in default availability.",
        ),
        PrimitiveDeclaration(
            "task_durable_reread", "documented", ("TaskGet", "TaskList"),
            "Official tools reference names TaskGet and TaskList; durable reread still requires correlated observed results.",
        ),
        PrimitiveDeclaration(
            "task_state_update", "documented", ("TaskUpdate",),
            "Official tools reference names TaskUpdate; availability and effect require an observed result event.",
        ),
        PrimitiveDeclaration("memory_write", "documented", basis="Claude Code documents auto memory, but no matching write outcome is inferred from session history."),
        PrimitiveDeclaration("memory_durable_reread", "documented", basis="Claude Code documents auto memory; this adapter has no observed durable reread event."),
        PrimitiveDeclaration(
            "native_scheduler_creation", "documented", ("CronCreate",),
            "Official tools reference documents CronCreate; declaration is not scheduler use.",
        ),
        PrimitiveDeclaration(
            "native_scheduler_delivery", "documented", (),
            "Native delivery requires a real exact scheduler wakeup and consumed task event; no CLI availability flag or external due event qualifies.",
        ),
    )

    def validate_selector(
        self, model: str, variant: str | None, reasoning_effort: str | None, effort: str | None,
    ) -> None:
        validate_model(model)
        if variant is not None:
            raise AdapterError("opencode_variant_not_valid_for_claude_code")
        if reasoning_effort is not None:
            raise AdapterError("codex_reasoning_effort_not_valid_for_claude_code")
        if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS):
            raise AdapterError("claude_effort_invalid")

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
        if session_id is not None:
            validate_uuid_session(session_id)
            if fresh_session_id is not None:
                raise AdapterError("resume_and_fresh_session_id_conflict")
        else:
            validate_uuid_session(fresh_session_id, fresh=True)

        argv = [
            binary,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
        ]
        if effort is not None:
            argv.extend(["--effort", effort])
        if session_id is None:
            argv.extend(["--session-id", str(fresh_session_id)])
        else:
            # Exact observed session only; never --continue, --fork-session,
            # --last, or a name/picker fallback.
            argv.extend(["--resume", session_id])

        # The isolated profile scopes the fixture workspace only. No --bare,
        # settings override, auth change, allowlist, or permission bypass is
        # applied; native safety/configuration remains inherited.
        argv.append(prompt)
        if any(token in {"--bg", "--background", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--bare"}
               for token in argv):
            raise AdapterError("claude_unsafe_or_conflicting_flag_refused")
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
        """Read only documented ``system/init`` and final ``result`` identities."""
        identities: list[str] = []
        for event in events:
            event_type = event.get("type")
            subtype = event.get("subtype")
            if not (
                (event_type == "system" and subtype == "init")
                or event_type == "result"
            ):
                continue
            value = event.get("session_id")
            if isinstance(value, str) and UUID_SESSION_RE.fullmatch(value):
                identities.append(value.lower())
        distinct = list(dict.fromkeys(identities))
        if len(distinct) > 1:
            return None, True
        return (distinct[0], False) if distinct else (None, False)

    def normalize_trace(self, raw_text: str) -> dict[str, Any]:
        events, malformed = parse_jsonl_objects(raw_text)
        session_id, ambiguous = self.extract_session_identity(events)
        result_events = [event for event in events if event.get("type") == "result"]
        final_ack = _parse_result_ack(result_events[-1]) if result_events else None
        native_tools = _documented_tool_invocations(events)
        return {
            "session_id": None if ambiguous else session_id,
            "identity_ambiguous": ambiguous,
            "final_ack": final_ack,
            "native_tools": native_tools,
            "event_count": len(events),
            "malformed_event_count": malformed,
            "final_message_present": bool(result_events),
        }


def _parse_result_ack(event: dict[str, Any]) -> dict | None:
    """Accept only the structured final result field, never surrounding prose."""
    if event.get("is_error") is True:
        return None
    result = event.get("result")
    if not isinstance(result, str):
        return None
    try:
        value = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _documented_tool_invocations(events: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    """Record tool-use starts as unconfirmed; no success is inferred from them."""
    observations: list[dict[str, Any]] = []
    for index, message in enumerate(events):
        if message.get("type") != "stream_event":
            continue
        event = message.get("event")
        if not isinstance(event, dict) or event.get("type") != "content_block_start":
            continue
        block = event.get("content_block")
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = tool_name(block.get("name"))
        if name is None:
            continue
        observations.append({
            "tool": name,
            "status": None,
            "event_type": "content_block_start",
            "event_ref": event_reference(event, index, block),
            "event_scope": "claude-code-main-session-stream",
            "outcome_note": "tool_use event identifies a requested call; no completion/result status is present",
        })
        if len(observations) >= limit:
            break
    return observations
