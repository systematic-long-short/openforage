"""Inert contract checks for built-in adapters and reusable conformance."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import adapter_ids, get_adapter
from .contract import AdapterError
from .conformance import selfcheck as conformance_selfcheck, validate_proof_v2
from .native_evidence import native_primitive_observations
from .scaffold import ScaffoldError, create_scaffold

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_selfcheck() -> list[str]:
    failures: list[str] = []
    expected = {"opencode", "codex", "claude-code"}
    if set(adapter_ids()) != expected:
        failures.append("built-in adapter identities are not exactly registered")

    workspace = Path("/synthetic workspace")
    cases = {
        "opencode": {
            "model": "provider/model", "variant": "max", "reasoning_effort": None,
            "effort": None, "session_id": "child-session", "fresh_session_id": None,
        },
        "codex": {
            "model": "model-name", "variant": None, "reasoning_effort": "medium",
            "effort": None, "session_id": "thread-id", "fresh_session_id": None,
        },
        "claude-code": {
            "model": "claude-test-model", "variant": None, "reasoning_effort": None,
            "effort": "medium", "session_id": None,
            "fresh_session_id": "00000000-0000-4000-8000-000000000001",
        },
    }
    for adapter_id, selectors in cases.items():
        adapter = get_adapter(adapter_id)
        argv = adapter.build_argv(
            binary="/synthetic/bin",
            model=selectors["model"],
            workspace=workspace,
            prompt="synthetic only",
            variant=selectors["variant"],
            reasoning_effort=selectors["reasoning_effort"],
            effort=selectors["effort"],
            session_id=selectors["session_id"],
            fresh_session_id=selectors["fresh_session_id"],
            profile="native",
        )
        if not argv or argv[0] != "/synthetic/bin":
            failures.append(f"{adapter_id}: argv did not begin with the selected binary")
        if any(token in {"--continue", "--last", "--fork-session", "--bg", "--background",
                         "--bare", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}
               for token in argv):
            failures.append(f"{adapter_id}: unsafe or implicit session flag appeared")
        if adapter.version_probe_argv("binary") != ["binary", "--version"]:
            failures.append(f"{adapter_id}: version probe argv was not explicit")
        if adapter.help_probe_argv("binary") != ["binary", "--help"]:
            failures.append(f"{adapter_id}: help probe argv was not explicit")

    opencode = get_adapter("opencode")
    opencode_event_cases = (
        (
            "absent-top-level-state-keeps-part-completed",
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "completed"},
                },
            },
            "completed",
        ),
        (
            "null-top-level-state-keeps-part-failed",
            {
                "type": "tool_use",
                "state": None,
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "failed"},
                },
            },
            "failed",
        ),
        (
            "non-object-top-level-state-keeps-part-completed",
            {
                "type": "tool_use",
                "state": ["not", "an", "object"],
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "completed"},
                },
            },
            "completed",
        ),
        (
            "null-part-state-remains-unknown",
            {
                "type": "tool_use",
                "state": None,
                "part": {"type": "tool", "tool": "read", "state": None},
            },
            None,
        ),
        (
            "non-object-part-state-remains-unknown",
            {
                "type": "tool_use",
                "state": None,
                "part": {"type": "tool", "tool": "read", "state": "completed"},
            },
            None,
        ),
        (
            "missing-status-remains-unknown",
            {"type": "tool_use", "part": {"type": "tool", "tool": "read"}},
            None,
        ),
        (
            "unrecognized-status-remains-unknown",
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "unrecognized"},
                },
            },
            None,
        ),
        (
            "null-part-container-remains-unknown",
            {"type": "tool_use", "tool": "read", "state": None, "part": None},
            None,
        ),
    )
    for label, event, expected_status in opencode_event_cases:
        trace = opencode.normalize_trace("\n".join((
            json.dumps(event),
            json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "The read completed successfully."},
            }),
        )))
        native_tools = trace.get("native_tools")
        if not isinstance(native_tools, list) or len(native_tools) != 1:
            failures.append(f"OpenCode {label}: expected exactly one tool observation")
        elif "status" not in native_tools[0] or native_tools[0].get("status") != expected_status:
            failures.append(f"OpenCode {label}: explicit or unknown status was not preserved")
        if trace.get("final_ack") is not None:
            failures.append(f"OpenCode {label}: final prose was treated as a structured acknowledgement")

    claude = get_adapter("claude-code")
    exact_resume = claude.build_argv(
        binary="claude", model="model", workspace=workspace, prompt="resume",
        variant=None, reasoning_effort=None, effort=None,
        session_id="00000000-0000-4000-8000-000000000001",
        fresh_session_id=None, profile="native",
    )
    if "--resume" not in exact_resume or "--session-id" in exact_resume or "--continue" in exact_resume:
        failures.append("Claude continuation did not use only the exact recorded session id")
    if "--bg" in exact_resume or "--print" not in exact_resume:
        failures.append("Claude print-mode argv does not preserve the documented --bg conflict")
    try:
        claude.build_argv(
            binary="claude", model="model", workspace=workspace, prompt="resume",
            variant=None, reasoning_effort=None, effort=None,
            session_id="not-a-uuid", fresh_session_id=None, profile="native",
        )
    except AdapterError:
        pass
    else:
        failures.append("Claude accepted a non-UUID resume id")

    fixture_path = REPO_ROOT / "examples/background-agent/fixtures/claude-code-stream.synthetic.jsonl"
    try:
        fixture_text = fixture_path.read_text(encoding="utf-8")
        claude_trace = claude.normalize_trace(fixture_text)
    except OSError:
        failures.append("Claude synthetic stream fixture is missing")
    else:
        if claude_trace.get("session_id") != "00000000-0000-4000-8000-000000000001":
            failures.append("Claude event parser missed the documented init/result session identity")
        if claude_trace.get("final_ack", {}).get("kind") != "ownership_ack":
            failures.append("Claude event parser did not require the structured result field")
        if claude_trace.get("native_tools", [{}])[0].get("status") is not None:
            failures.append("Claude tool-use start was upgraded to an operation outcome")

    declarations = claude.declared_primitives
    undeclared_outcome = native_primitive_observations(
        "claude-code",
        [{"tool": "TaskCreate", "status": None, "event_type": "content_block_start", "event_ref": "synthetic:1"}],
        declarations=declarations,
        scope={"product_version": "synthetic", "os": "synthetic", "profile": "synthetic"},
    )
    if undeclared_outcome["operations"]["task_creation"]["outcome"] != "unknown":
        failures.append("declared TaskCreate availability established successful execution")
    if undeclared_outcome["operations"]["native_scheduler_delivery"]["outcome"] != "unknown":
        failures.append("absent native scheduler delivery did not remain unknown")

    failures.extend(conformance_selfcheck(REPO_ROOT / "examples/background-agent/fixtures"))
    proof_fixture_path = REPO_ROOT / "examples/background-agent/fixtures/proof-v2-envelope.synthetic.json"
    try:
        proof_fixture = json.loads(proof_fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        failures.append("synthetic proof-v2 envelope fixture is unreadable")
    else:
        if proof_fixture.get("synthetic") is not True or validate_proof_v2(proof_fixture):
            failures.append("synthetic proof-v2 envelope did not satisfy the typed schema")

    scratch_root = REPO_ROOT / ".tmp"
    if scratch_root.is_symlink():
        failures.append("adapter selfcheck scratch root is a symlink")
    else:
        try:
            scratch_root.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            failures.append("adapter selfcheck scratch root is unavailable")
        else:
            with tempfile.TemporaryDirectory(prefix="of-adapter-scaffold-", dir=scratch_root) as tmp:
                target = Path(tmp) / "new-adapter"
                try:
                    result = create_scaffold(target)
                    if result.get("session_launched") is not False or result.get("configuration_mutated") is not False:
                        failures.append("scaffold was not inert")
                    if not (target / "adapter.py").is_file() or not (target / "evidence.json").is_file():
                        failures.append("scaffold omitted its contract or evidence template")
                    try:
                        create_scaffold(target)
                    except ScaffoldError:
                        pass
                    else:
                        failures.append("scaffold overwrote an existing directory")
                except ScaffoldError:
                    failures.append("fresh caller-selected scaffold could not be created")

    return failures
