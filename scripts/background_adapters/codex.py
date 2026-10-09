"""Codex CLI adapter, extracted behind the shared adapter contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .contract import (
    EFFORT_RE,
    AdapterError,
    PrimitiveDeclaration,
    event_reference,
    parse_jsonl_objects,
    validate_binary,
    validate_common_argv,
    validate_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEX_FIXTURE = REPO_ROOT / "examples" / "codex-lifecycle"
if str(CODEX_FIXTURE) not in sys.path:
    sys.path.insert(0, str(CODEX_FIXTURE))

from codex_events import (  # noqa: E402
    extract_final_message,
    extract_thread_id,
    explicit_tool_observations,
    parse_final_json,
    parse_jsonl,
)


class CodexAdapter:
    adapter_id = "codex"
    product_name = "Codex"
    executable_name = "codex"
    requires_fresh_session_id = False
    declared_primitives = (
        PrimitiveDeclaration("goal_declaration", "documented", basis="Codex goal surfaces are documented, but no goal-operation event was observed in the retained controller proof."),
        PrimitiveDeclaration("task_creation", "unknown", basis="No product-specific task-creation event was observed in the retained controller proof."),
        PrimitiveDeclaration("task_durable_reread", "unknown"),
        PrimitiveDeclaration("memory_write", "unknown"),
        PrimitiveDeclaration("memory_durable_reread", "unknown"),
        PrimitiveDeclaration("native_scheduler_creation", "unknown"),
        PrimitiveDeclaration("native_scheduler_delivery", "unknown"),
    )

    def validate_selector(
        self, model: str, variant: str | None, reasoning_effort: str | None, effort: str | None,
    ) -> None:
        validate_model(model)
        if reasoning_effort is None or EFFORT_RE.fullmatch(reasoning_effort) is None:
            raise AdapterError("codex_reasoning_effort_required_or_invalid")
        if variant is not None:
            raise AdapterError("opencode_variant_not_valid_for_codex")
        if effort is not None:
            raise AdapterError("claude_effort_not_valid_for_codex")

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
            raise AdapterError("codex_fresh_session_id_not_supported")
        argv = [
            binary,
            "--no-daemon",
            "--ask-for-approval", "never",
            "--model", model,
            "--cd", str(workspace),
            "--sandbox", "workspace-write",
            "-c", f"model_reasoning_effort={reasoning_effort}",
        ]
        if profile == "isolated":
            argv.extend(["-c", "features.goals=true"])
        argv.append("exec")
        isolated_flag = ["--ignore-user-config"] if profile == "isolated" else []
        if session_id is None:
            argv.extend(["--json", *isolated_flag, prompt])
        else:
            argv.extend(["resume", "--json", *isolated_flag, session_id, prompt])
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
        thread_ids: list[str] = []
        for event in events:
            if not str(event.get("type", "")).startswith("thread."):
                continue
            value = event.get("thread_id", event.get("threadId"))
            if isinstance(value, str) and value.strip():
                thread_ids.append(value.strip())
        distinct = list(dict.fromkeys(thread_ids))
        if len(distinct) > 1:
            return None, True
        return (extract_thread_id(events), False) if distinct else (None, False)

    def normalize_trace(self, raw_text: str) -> dict[str, Any]:
        events, malformed = parse_jsonl_objects(raw_text)
        parsed = parse_jsonl(raw_text)
        session_id, ambiguous = self.extract_session_identity(parsed)
        final_json = parse_final_json(parsed)
        return {
            "session_id": None if ambiguous else session_id,
            "identity_ambiguous": ambiguous,
            "final_ack": final_json,
            "native_tools": _tool_observations(parsed),
            "event_count": len(events),
            "malformed_event_count": malformed,
            "final_message_present": extract_final_message(parsed) is not None,
        }


def _tool_observations(events: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        normalized = explicit_tool_observations([event])
        if not normalized:
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        for observation in normalized:
            observations.append({
                **observation,
                "event_type": event.get("type") if isinstance(event.get("type"), str) else "unknown",
                "event_ref": event_reference(event, index, item),
            })
            if len(observations) >= limit:
                return observations
    return observations
