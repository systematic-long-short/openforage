"""Explicit built-in background-agent CLI adapters and read-only probes."""

from __future__ import annotations

import subprocess
from typing import Any

from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .contract import AdapterError, HarnessAdapter
from .opencode import OpenCodeAdapter

OPEN_CODE = OpenCodeAdapter()
CODEX = CodexAdapter()
CLAUDE_CODE = ClaudeCodeAdapter()
ADAPTERS: dict[str, HarnessAdapter] = {
    adapter.adapter_id: adapter
    for adapter in (OPEN_CODE, CODEX, CLAUDE_CODE)
}


def adapter_ids() -> tuple[str, ...]:
    return tuple(ADAPTERS)


def get_adapter(adapter_id: str) -> HarnessAdapter:
    try:
        return ADAPTERS[adapter_id]
    except (KeyError, TypeError):
        raise AdapterError("unsupported_harness") from None


def observe_version(adapter_id: str, binary: str, *, timeout: int = 20) -> dict[str, Any]:
    """Run one adapter-declared version argv without starting a session."""
    adapter = get_adapter(adapter_id)
    argv = adapter.version_probe_argv(binary)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise AdapterError("harness_version_probe_failed") from None
    version = adapter.parse_version(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
    return {
        "adapter_id": adapter.adapter_id,
        "product": adapter.product_name,
        "version": version,
        "outcome": "observed_success",
        "argv_shape": ["<selected-binary>", *argv[1:]],
        "session_launched": False,
    }


def observe_read_only_probe(adapter_id: str, binary: str, *, timeout: int = 30) -> dict[str, Any]:
    """Read adapter version and local help; help text never promotes features."""
    adapter = get_adapter(adapter_id)
    version = observe_version(adapter_id, binary, timeout=min(timeout, 20))
    argv = adapter.help_probe_argv(binary)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise AdapterError("harness_help_probe_failed") from None
    parsed = adapter.parse_probe(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
    return {
        "schema_version": 1,
        "evidence_class": "read-only-adapter-probe",
        "adapter_id": adapter.adapter_id,
        "product": adapter.product_name,
        "version": version["version"],
        "help_probe": {
            "argv_shape": ["<selected-binary>", *argv[1:]],
            **parsed,
        },
        "declared_primitives": [
            {
                "operation": item.operation,
                "availability": item.availability,
                "tool_names": list(item.tool_names),
                "basis": item.basis,
            }
            for item in adapter.declared_primitives
        ],
        "limitations": [
            "A version/help probe is not a native-operation observation.",
            "No session was launched, no auth state was read, and no config was changed.",
        ],
        "session_launched": False,
        "credentials_inspected": False,
        "configuration_mutated": False,
    }


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "CLAUDE_CODE",
    "CODEX",
    "OPEN_CODE",
    "adapter_ids",
    "get_adapter",
    "observe_read_only_probe",
    "observe_version",
]
