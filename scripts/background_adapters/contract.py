"""Shared interface for explicit background-agent CLI adapters.

An adapter owns product-specific argv, version/help probes, event parsing, and
session extraction. It does not own process lifecycle, workspace ownership,
request queues, fencing, or cleanup; those remain in the existing controller.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,160}$")
VARIANT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
EFFORT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SESSION_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,256}$")
UUID_SESSION_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
PROFILES = {"native", "isolated"}

Availability = Literal["documented", "unknown", "unavailable"]


class AdapterError(RuntimeError):
    """A stable, local-safe adapter refusal code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PrimitiveDeclaration:
    """Parser vocabulary for a documented route, not a capability status registry.

    The canonical public capability authority remains harnesses.json through
    ``project_capability_records``. This declaration can label an absent event
    unknown/documented; it cannot promote a capability or prove an operation.
    """

    operation: str
    availability: Availability
    tool_names: tuple[str, ...] = ()
    basis: str = "No operation event has been observed for this adapter."


class HarnessAdapter(Protocol):
    """Product-specific launcher/probe/normalizer contract.

    ``session_id`` always means an exact previously observed target. A fresh
    session id is a separate argument so an adapter cannot turn a resume into a
    recent-session or implicit-parent continuation.
    """

    adapter_id: str
    product_name: str
    executable_name: str
    requires_fresh_session_id: bool
    declared_primitives: tuple[PrimitiveDeclaration, ...]

    def validate_selector(
        self,
        model: str,
        variant: str | None,
        reasoning_effort: str | None,
        effort: str | None,
    ) -> None: ...

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
    ) -> list[str]: ...

    def version_probe_argv(self, binary: str) -> list[str]: ...

    def help_probe_argv(self, binary: str) -> list[str]: ...

    def parse_version(self, *, returncode: int, stdout: str, stderr: str) -> str: ...

    def parse_probe(self, *, returncode: int, stdout: str, stderr: str) -> dict[str, Any]: ...

    def extract_session_identity(self, events: list[dict[str, Any]]) -> tuple[str | None, bool]: ...

    def normalize_trace(self, raw_text: str) -> dict[str, Any]: ...


def validate_common_argv(
    *,
    binary: str,
    model: str,
    prompt: str,
    session_id: str | None,
    fresh_session_id: str | None,
    profile: str,
) -> None:
    if not isinstance(binary, str) or not binary or "\x00" in binary:
        raise AdapterError("cli_binary_invalid")
    if not isinstance(prompt, str) or not prompt or "\x00" in prompt:
        raise AdapterError("prompt_invalid")
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        raise AdapterError("model_selector_invalid")
    if profile not in PROFILES:
        raise AdapterError("adapter_profile_invalid")
    if session_id is not None and SESSION_RE.fullmatch(session_id) is None:
        raise AdapterError("session_identity_invalid")
    if fresh_session_id is not None and SESSION_RE.fullmatch(fresh_session_id) is None:
        raise AdapterError("fresh_session_identity_invalid")
    if session_id is not None and fresh_session_id is not None:
        raise AdapterError("resume_and_fresh_session_id_conflict")


def validate_model(model: str) -> None:
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        raise AdapterError("model_selector_invalid")


def validate_binary(binary: str) -> None:
    if not isinstance(binary, str) or not binary or "\x00" in binary:
        raise AdapterError("cli_binary_invalid")


def validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt or "\x00" in prompt:
        raise AdapterError("prompt_invalid")


def validate_session(value: str | None) -> None:
    if value is not None and SESSION_RE.fullmatch(value) is None:
        raise AdapterError("session_identity_invalid")


def validate_uuid_session(value: str | None, *, fresh: bool = False) -> None:
    if value is None or UUID_SESSION_RE.fullmatch(value) is None:
        raise AdapterError("fresh_session_identity_invalid" if fresh else "session_identity_invalid")


def parse_jsonl_objects(text: str) -> tuple[list[dict[str, Any]], int]:
    """Keep JSON object lines only and count malformed JSON-looking lines."""
    import json

    events: list[dict[str, Any]] = []
    malformed = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            malformed += 1
    return events, malformed


def valid_event_text(value: object, *, maximum: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum or SESSION_RE.fullmatch(value) is None:
        return None
    return value


def event_reference(event: dict[str, Any], index: int, *nested: object) -> str:
    """Use a provider event/call id when present, else a private-trace line id."""
    sources = (event, *(item for item in nested if isinstance(item, dict)))
    for source in sources:
        for key in ("uuid", "event_id", "eventId", "callID", "call_id", "id"):
            value = valid_event_text(source.get(key))
            if value is not None:
                return value
    return f"line:{index}"


def explicit_status(value: object) -> str | None:
    aliases = {
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
    return aliases.get(value.casefold()) if isinstance(value, str) else None


def tool_name(value: object) -> str | None:
    return value if isinstance(value, str) and TOOL_RE.fullmatch(value) else None


def probe_digest(stdout: str, stderr: str) -> dict[str, Any]:
    """Return safe, transcript-free probe metadata; never infer capability use."""
    raw = (stdout + stderr).encode("utf-8", errors="replace")
    return {"output_sha256": hashlib.sha256(raw).hexdigest(), "output_bytes": len(raw)}
