"""Inert parser and subprocess regressions for ``background_agent.py``.

No OpenCode/Codex/Claude Code native session, network request, OpenForage API,
or worker is started. The fake executable is a local Python process with
JSONL-shaped events.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from background_agent import (  # noqa: E402
    AgentError,
    _control_signal,
    _recover,
    _resume,
    _turn,
    build_parser,
)
from background_agent_adapters import (  # noqa: E402
    AdapterError,
    build_argv,
    get_adapter,
    native_primitive_observations,
    parse_trace,
    validate_ack,
)
from background_agent_store import (  # noqa: E402
    StoreError,
    diagnose_run,
    identity_alive,
    load_state,
    process_identity,
    process_group_observation,
    process_identity_status,
    public_status,
    task_sha256,
    terminate_identity_checked,
    validate_start_paths,
    valid_process_identity,
)
from background_agent_ownership import (  # noqa: E402
    locked_workspace,
    owned_processes_gone,
    release_stopped_workspace,
    update_workspace_owner,
)

FAKE_CLI = r'''#!/usr/bin/env python3
import fcntl, json, os, re, subprocess, sys, time, uuid
from pathlib import Path

args = sys.argv[1:]
harness = os.environ["BA_FAKE_HARNESS"]
db = Path(os.environ["BA_FAKE_DB"])
db.mkdir(parents=True, exist_ok=True)
prompt = args[-1] if args else ""
lock_fd = os.open(os.environ["BA_FAKE_ACTIVE_LOCK"], os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    Path(os.environ["BA_FAKE_OVERLAP_FILE"]).write_text("overlap\n", encoding="utf-8")
    sys.exit(91)

def field(name, pattern):
    match = re.search(pattern, prompt)
    return match.group(1) if match else None

if harness == "opencode":
    target = args[args.index("--session") + 1] if "--session" in args else None
else:
    target = None
    if "resume" in args:
        index = args.index("resume") + 1
        while index < len(args) and args[index].startswith("--"):
            index += 1
        target = args[index] if index < len(args) - 1 else None

parent = "parent-" + harness
if target is None:
    session_id = parent if os.environ.get("BA_FAKE_PARENT_REUSE") == "1" else "fake-" + harness + "-" + uuid.uuid4().hex
else:
    session_id = "fake-wrong-target" if "FAKE_WRONG_RESUME_ID" in prompt else target
memory_path = db / (session_id.replace("/", "_") + ".json")
memory = None
if memory_path.is_file():
    try:
        memory = json.loads(memory_path.read_text(encoding="utf-8")).get("memory")
    except Exception:
        memory = None
if "FAKE_MEMORY=" in prompt and target is None:
    match = re.search(r"FAKE_MEMORY=([A-Za-z0-9_-]+)", prompt)
    if match:
        memory = match.group(1)
        memory_path.write_text(json.dumps({"memory": memory}), encoding="utf-8")
if "FAKE_WRITE_STAGE_ONE" in prompt:
    Path.cwd().joinpath("proof.txt").write_text("stage-one\n", encoding="utf-8")
if "FAKE_WRITE_STAGE_TWO" in prompt:
    Path.cwd().joinpath("proof.txt").write_text("stage-two\n", encoding="utf-8")
if "FAKE_SLEEP" in prompt:
    time.sleep(float(os.environ.get("BA_FAKE_SLEEP_SECONDS", "8")))
if "FAKE_GROUP_SURVIVOR" in prompt:
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"])

run_match = re.search(r'run_id=("[^"\n]+")', prompt)
hash_match = re.search(r'task_sha256=("[0-9a-f]{64}")', prompt)
turn_match = re.search(r'turn_id=("[^"\n]+")', prompt)
fence_match = re.search(r"fence=([0-9]+)", prompt)
run_id = json.loads(run_match.group(1)) if run_match else "missing-run"
task_hash = json.loads(hash_match.group(1)) if hash_match else "missing-hash"
turn_id = json.loads(turn_match.group(1)) if turn_match else "missing-turn"
fence = int(fence_match.group(1)) if fence_match else 0
ack_kind = "ownership_ack" if 'kind="ownership_ack"' in prompt else "turn_ack"
ack = {
    "kind": ack_kind,
    "run_id": run_id,
    "task_sha256": task_hash,
    "turn_id": turn_id,
    "fence": fence,
    "task_status": "in_progress",
    "summary": "raw-transcript-sentinel-inert marker acknowledged",
}
if "FAKE_BLOCKED_STATUS" in prompt:
    ack["task_status"] = "blocked"
if "FAKE_COMPLETE_STATUS" in prompt:
    ack["task_status"] = "agent_reported_complete"
if "FAKE_RECURRENCE_ACTION" in prompt:
    marker = Path.cwd() / "recurrence.log"
    existing = marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
    if turn_id not in existing:
        marker.write_text("".join(item + "\n" for item in [*existing, turn_id]), encoding="utf-8")
if "FAKE_RECALL_MEMORY" in prompt:
    ack["recalled_marker"] = memory
if "FAKE_NATIVE_TOOL" in prompt:
    tool_event = {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "name": "todowrite", "status": "completed",
    }}
else:
    tool_event = None
if "FAKE_NO_ACK" in prompt:
    ack = None
elif "FAKE_WRONG_ACK" in prompt:
    ack["task_sha256"] = "0" * 64

def emit(event):
    print(json.dumps(event, separators=(",", ":")), flush=True)

if harness == "opencode":
    emit({"type": "run", "sessionID": session_id})
    if tool_event:
        emit({"type": "tool", "tool": "todowrite", "state": {"status": "completed"}})
    if ack is not None:
        emit({"type": "text", "part": {"type": "text", "text": json.dumps(ack, separators=(",", ":"))}})
else:
    emit({"type": "thread.started", "thread_id": session_id})
    if tool_event:
        emit(tool_event)
    if ack is not None:
        emit({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(ack, separators=(",", ":")),
        }})
    emit({"type": "turn.completed", "usage": {"input_tokens": 12345}})
if "FAKE_NONZERO" in prompt:
    sys.exit(3)
'''


def _assert(condition: bool, label: str, failures: list[str], checks: list[str]) -> None:
    if condition:
        checks.append(label)
    else:
        failures.append(label)


@contextmanager
def _fake_env(harness: str, db: Path, active_lock: Path, overlap_file: Path, sleep: float = 8):
    names = {
        "BA_FAKE_HARNESS": harness,
        "BA_FAKE_DB": str(db),
        "BA_FAKE_ACTIVE_LOCK": str(active_lock),
        "BA_FAKE_OVERLAP_FILE": str(overlap_file),
        "BA_FAKE_SLEEP_SECONDS": str(sleep),
        "HOME": str(db.parent / "private-home"),
    }
    prior = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _new_fixture(root: Path, label: str) -> tuple[Path, Path, Path]:
    workspace = root / f"workspace-{label}"
    workspace.mkdir()
    task = workspace / "TASK.md"
    task.write_text("# Inert durable task\n\nWrite only a local marker file.\n", encoding="utf-8")
    state_dir = root / f"state-{label}"
    return workspace, task, state_dir


def _start_fixture(harness: str, workspace: Path, task: Path, state_dir: Path,
                   binary: Path, command: str, *, interval_seconds: float | None = None,
                   continuation_command: str | None = None) -> tuple[int, dict]:
    argv = [
        sys.executable, str(REPO_ROOT / "scripts" / "background_agent.py"), "start",
        "--harness", harness, "--parent-session", "parent-" + harness,
        "--model", "local/inert-fixture", "--workspace", str(workspace),
        "--task", str(task), "--state-dir", str(state_dir), "--command", command,
        "--binary", str(binary), "--profile", "native",
        "--run-timeout-seconds", "12", "--startup-timeout-seconds", "12",
    ]
    if harness == "opencode":
        argv.extend(["--variant", "max"])
    else:
        argv.extend(["--reasoning-effort", "medium"])
    if interval_seconds is not None:
        argv.extend(["--interval-seconds", str(interval_seconds), "--continuation-command",
                     str(continuation_command or "")])
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except (OSError, subprocess.TimeoutExpired):
        raise StoreError("inert_start_launcher_failed") from None
    try:
        status = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        status = {}
    if not isinstance(status, dict) or "status" not in status:
        try:
            error = json.loads(result.stderr).get("error", "inert_start_refused")
        except (TypeError, json.JSONDecodeError, AttributeError):
            error = "inert_start_refused"
        raise StoreError(str(error))
    return result.returncode, status


def _wait_active(run, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        active = state.get("active")
        if active and active.get("child_identity"):
            return active
        if state.get("status") in {"failed", "needs_reconciliation", "stopped"}:
            return None
        time.sleep(0.02)
    return None


def _wait_due_acknowledgements(run, count: int, timeout: float = 8.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        rows = [item for item in state.get("turns", [])
                if item.get("delivery") == "external_due_time"]
        if len(rows) >= count and all(item.get("status") == "acknowledged" for item in rows[:count]):
            return rows[:count]
        if not identity_alive(state.get("controller_identity")):
            break
        time.sleep(0.03)
    with run.lock():
        state = load_state(run)
    return [item for item in state.get("turns", []) if item.get("delivery") == "external_due_time"]


def _wait_service_dead(run, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        if not identity_alive(state.get("controller_identity")):
            return True
        time.sleep(0.05)
    return False


def _reap_local_child(pid: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited == pid:
            return
        time.sleep(0.02)


def _kill_controller(run) -> bool:
    with run.lock():
        state = load_state(run)
    identity = state.get("controller_identity")
    if not valid_process_identity(identity) or process_identity_status(identity) != "alive":
        return False
    current = process_identity(identity["pid"])
    if (not valid_process_identity(current)
            or any(current.get(key) != identity.get(key) for key in ("pid", "boot_id", "starttime", "pgid"))
            or process_group_observation(identity) != "alive"):
        return False
    os.kill(identity["pid"], signal.SIGKILL)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            waited, _ = os.waitpid(identity["pid"], os.WNOHANG)
        except ChildProcessError:
            break
        if waited == identity["pid"]:
            break
        time.sleep(0.02)
    return _wait_service_dead(run)


def _trace_for_turn(run, request_id: str, harness: str) -> dict:
    path = run.dir / "raw" / f"{request_id}.jsonl"
    return parse_trace(harness, path.read_text(encoding="utf-8", errors="replace"))


def _start_duplicate_service(run) -> int:
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "background_agent.py"),
         "__service", "--state-dir", str(run.dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.wait(timeout=5)


def _stop_cli(state_dir: Path) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "background_agent.py"),
         "stop", "--state-dir", str(state_dir), "--wait-seconds", "3"],
        capture_output=True, text=True, timeout=8,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        status = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        status = {}
    return result.returncode, status if isinstance(status, dict) else {}


def run_selfcheck() -> int:
    failures: list[str] = []
    checks: list[str] = []
    runs = []

    # ---- Exact argv and observed-event parser regressions -----------------
    workspace = Path("/workspace with spaces")
    opencode_argv = build_argv(
        harness="opencode", binary="/bin/opencode", model="provider/model", variant="max",
        workspace=workspace, prompt="bounded prompt", session_id="child-session",
    )
    codex_argv = build_argv(
        harness="codex", binary="/bin/codex", model="model-name", reasoning_effort="medium",
        workspace=workspace, prompt="bounded prompt", session_id="thread-uuid",
    )
    opencode_isolated = build_argv(
        harness="opencode", binary="/bin/opencode", model="provider/model", variant="max",
        workspace=workspace, prompt="bounded prompt", session_id="child-session", profile="isolated",
    )
    codex_isolated = build_argv(
        harness="codex", binary="/bin/codex", model="model-name", reasoning_effort="medium",
        workspace=workspace, prompt="bounded prompt", session_id="thread-uuid", profile="isolated",
    )
    claude_fresh_id = "00000000-0000-4000-8000-000000000001"
    claude_child_id = "00000000-0000-4000-8000-000000000002"
    claude_argv = build_argv(
        harness="claude-code", binary="/bin/claude", model="claude-test-model", effort="medium",
        workspace=workspace, prompt="bounded prompt", fresh_session_id=claude_fresh_id,
    )
    claude_resume_argv = build_argv(
        harness="claude-code", binary="/bin/claude", model="claude-test-model", effort="medium",
        workspace=workspace, prompt="bounded prompt", session_id=claude_child_id,
    )
    codex_target_index = codex_argv.index("resume") + 1
    while codex_argv[codex_target_index].startswith("--"):
        codex_target_index += 1
    _assert("--session" in opencode_argv and opencode_argv[-1] == "bounded prompt"
            and "--pure" not in opencode_argv and "--continue" not in opencode_argv
            and "--last" not in opencode_argv,
            "opencode-native-profile-retains-plugins-and-explicit-session", failures, checks)
    _assert("--pure" in opencode_isolated,
            "opencode-isolated-profile-explicitly-disables-plugins", failures, checks)
    _assert("resume" in codex_argv and "--ignore-user-config" not in codex_argv
            and codex_argv[codex_target_index] == "thread-uuid"
            and "--sandbox" in codex_argv and codex_argv[codex_argv.index("--sandbox") + 1] == "workspace-write"
            and "features.goals=true" not in codex_argv
            and not any("dangerously" in token or token in {"--last", "--continue", "--ignore-rules"}
                        for token in codex_argv),
            "codex-native-profile-retains-config-and-workspace-sandbox", failures, checks)
    _assert("--ignore-user-config" in codex_isolated and "features.goals=true" in codex_isolated
            and codex_isolated[codex_isolated.index("--ignore-user-config") + 1] == "thread-uuid"
            and codex_isolated[codex_isolated.index("--sandbox") + 1] == "workspace-write"
            and "--ignore-rules" not in codex_isolated
            and not any("dangerously" in token or token in {"--last", "--continue"}
                        for token in codex_isolated),
            "codex-isolated-profile-is-explicit-and-keeps-sandbox", failures, checks)
    _assert(
        "--print" in claude_argv and "--output-format" in claude_argv
        and claude_argv[claude_argv.index("--output-format") + 1] == "stream-json"
        and "--verbose" in claude_argv and "--include-partial-messages" in claude_argv
        and claude_argv[claude_argv.index("--session-id") + 1] == claude_fresh_id
        and not any(token in {"--bg", "--background", "--bare", "--continue", "--fork-session",
                              "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                              "--allowedTools", "--tools", "--settings"} for token in claude_argv),
        "claude-print-stream-json-fresh-session-keeps-inherited-config-and-permissions", failures, checks)
    _assert(
        "--resume" in claude_resume_argv
        and claude_resume_argv[claude_resume_argv.index("--resume") + 1] == claude_child_id
        and "--session-id" not in claude_resume_argv
        and not any(token in {"--continue", "--fork-session", "--bg", "--background"}
                    for token in claude_resume_argv),
        "claude-continuation-names-only-the-exact-recorded-session", failures, checks)
    claude_adapter = get_adapter("claude-code")
    _assert(claude_adapter.version_probe_argv("claude") == ["claude", "--version"]
            and claude_adapter.help_probe_argv("claude") == ["claude", "--help"],
            "claude-version-and-read-only-help-probes-are-explicit", failures, checks)
    parsed_start = build_parser().parse_args([
        "start", "--harness", "codex", "--parent-session", "parent", "--model", "model",
        "--reasoning-effort", "medium", "--workspace", "/tmp/work", "--task", "/tmp/work/TASK.md",
        "--state-dir", "/tmp/state/run", "--command", "perform one bounded action",
    ])
    _assert(parsed_start.action == "start" and parsed_start.command == "perform one bounded action",
            "start-parser-separates-action-from-explicit-command", failures, checks)
    try:
        build_argv(harness="codex", binary="codex", model="x", reasoning_effort="medium",
                   workspace=workspace, prompt="x\x00y")
        failures.append("argv-nul-was-not-refused")
    except AdapterError:
        checks.append("argv-nul-refused")
    try:
        build_argv(harness="opencode", binary="opencode", model="x", variant="max",
                   workspace=workspace, prompt="p", session_id="bad child")
        failures.append("session-whitespace-was-not-refused")
    except AdapterError:
        checks.append("session-identity-shape-refused")

    ack = {
        "kind": "turn_ack", "run_id": "run-x", "task_sha256": "a" * 64,
        "turn_id": "turn-x", "fence": 4, "task_status": "in_progress", "summary": "marker updated",
    }
    opencode_events = "\n".join((
        json.dumps({"type": "run", "sessionID": "child-x"}),
        json.dumps({"type": "tool", "tool": "todowrite", "state": {"status": "completed"}}),
        "{ malformed",
        json.dumps({"type": "text", "part": {"type": "text", "text": json.dumps(ack)}}),
    ))
    op_trace = parse_trace("opencode", opencode_events)
    _assert(op_trace["session_id"] == "child-x" and op_trace["final_ack"] == ack
            and len(op_trace["native_tools"]) == 1
            and op_trace["native_tools"][0].get("tool") == "todowrite"
            and op_trace["native_tools"][0].get("status") == "completed"
            and op_trace["native_tools"][0].get("event_ref") == "line:1"
            and op_trace["malformed_event_count"] == 1,
            "opencode-actual-json-fields-malformed-line-and-explicit-tool", failures, checks)
    ambiguous = parse_trace("opencode", "\n".join((
        json.dumps({"type": "run", "sessionID": "child-a"}),
        json.dumps({"type": "run", "session_id": "child-b"}),
    )))
    _assert(ambiguous["identity_ambiguous"] is True,
            "opencode-conflicting-session-identities-refused", failures, checks)
    prose_only = parse_trace("opencode", json.dumps({"type": "text", "part": {"type": "text", "text": "I created a goal"}}))
    _assert(prose_only["native_tools"] == [] and prose_only["final_ack"] is None,
            "opencode-prose-does-not-prove-native-tool-or-ack", failures, checks)

    claude_fixture = REPO_ROOT / "examples" / "background-agent" / "fixtures" / "claude-code-stream.synthetic.jsonl"
    claude_trace = parse_trace("claude-code", claude_fixture.read_text(encoding="utf-8"))
    _assert(
        claude_trace["session_id"] == claude_fresh_id
        and claude_trace["final_ack"].get("kind") == "ownership_ack"
        and claude_trace["native_tools"][0].get("tool") == "TaskCreate"
        and claude_trace["native_tools"][0].get("status") is None
        and claude_trace["identity_ambiguous"] is False,
        "claude-documented-event-fields-extract-session-and-structured-result-without-tool-success-inference",
        failures, checks)
    claude_ack_failure, _ = validate_ack(
        claude_trace, exit_code=0, kind="ownership_ack", run_id="synthetic-run",
        task_sha256="a" * 64, turn_id="bootstrap", fence=1,
        target_session=claude_fresh_id,
    )
    claude_wrong_target, _ = validate_ack(
        claude_trace, exit_code=0, kind="ownership_ack", run_id="synthetic-run",
        task_sha256="a" * 64, turn_id="bootstrap", fence=1,
        target_session=claude_child_id,
    )
    _assert(claude_ack_failure is None and claude_wrong_target == "mismatched_resume_identity",
            "claude-session-id-and-resume-ack-must-match-the-exact-recorded-id", failures, checks)
    claude_prose_only = parse_trace(
        "claude-code",
        json.dumps({"type": "result", "result": "I created a task", "session_id": claude_fresh_id}),
    )
    _assert(claude_prose_only["final_ack"] is None,
            "claude-result-prose-does-not-satisfy-structured-ownership-ack", failures, checks)

    codex_lines = [
        {"type": "thread.started", "thread_id": "thread-x"},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "name": "create_goal", "status": "completed"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(ack)}},
        {"type": "turn.completed", "usage": {"input_tokens": 4321}},
        {"type": "item.completed", "item": {"type": ["mcp_tool_call"], "status": "completed"}},
    ]
    co_trace = parse_trace("codex", "\n".join(json.dumps(item) for item in codex_lines) + "\n{bad")
    _assert(co_trace["session_id"] == "thread-x" and co_trace["final_ack"] == ack
            and len(co_trace["native_tools"]) == 1
            and co_trace["native_tools"][0].get("tool") == "create_goal"
            and co_trace["native_tools"][0].get("status") == "completed"
            and co_trace["native_tools"][0].get("event_ref") == "line:1"
            and co_trace["malformed_event_count"] == 1 and "usage" not in repr(co_trace),
            "codex-thread-final-agent-message-tool-allowlist-and-malformed-items", failures, checks)
    wrong_resume = dict(co_trace, session_id="thread-sibling")
    failure, _ = validate_ack(wrong_resume, exit_code=0, kind="turn_ack", run_id="run-x",
                              task_sha256="a" * 64, turn_id="turn-x", fence=4,
                              target_session="thread-x")
    _assert(failure == "mismatched_resume_identity", "mismatched-resume-id-refused", failures, checks)
    failure, _ = validate_ack(dict(co_trace, final_ack=None), exit_code=0, kind="turn_ack", run_id="run-x",
                              task_sha256="a" * 64, turn_id="turn-x", fence=4)
    _assert(failure == "missing_native_ack", "zero-exit-without-real-ack-is-not-success", failures, checks)
    failure, _ = validate_ack(co_trace, exit_code=0, kind="turn_ack", run_id="run-x",
                              task_sha256="b" * 64, turn_id="turn-x", fence=4)
    _assert(failure == "native_ack_mismatch", "task-digest-ack-mismatch-refused", failures, checks)
    bool_fence_ack = dict(ack, fence=True)
    failure, _ = validate_ack(dict(co_trace, final_ack=bool_fence_ack), exit_code=0, kind="turn_ack",
                              run_id="run-x", task_sha256="a" * 64, turn_id="turn-x", fence=1)
    _assert(failure == "native_ack_field_types_invalid", "boolean-fence-is-not-an-integer-ack", failures, checks)
    bad_status_ack = dict(ack, task_status=[])
    failure, _ = validate_ack(dict(co_trace, final_ack=bad_status_ack), exit_code=0, kind="turn_ack",
                              run_id="run-x", task_sha256="a" * 64, turn_id="turn-x", fence=4)
    _assert(failure == "native_ack_task_status_invalid", "ack-task-status-type-is-validated", failures, checks)
    opencode_adapter = get_adapter("opencode")
    task_only = native_primitive_observations(
        "opencode",
        [{"tool": "todowrite", "status": "completed", "event_type": "tool", "event_ref": "event-task-1"}],
        declarations=opencode_adapter.declared_primitives,
        scope={"request_id": "request-task", "turn_id": "turn-task", "os": "linux", "profile": "isolated"},
    )
    goal_failed = native_primitive_observations(
        "synthetic-adapter",
        [{"operation": "goal_declaration", "tool": "synthetic-goal", "status": "error",
          "event_type": "synthetic.tool.completed", "event_ref": "event-goal-1"}],
        declarations=(),
        scope={"request_id": "request-goal", "turn_id": "turn-goal", "os": "linux", "profile": "synthetic"},
    )
    planning_only = native_primitive_observations(
        "synthetic-adapter",
        [{"event_type": "plan.updated", "status": "completed", "event_ref": "plan-1"}],
        declarations=(), scope={"request_id": "request-plan", "turn_id": "turn-plan"},
    )
    claude_adapter = get_adapter("claude-code")
    scheduler_create = native_primitive_observations(
        "claude-code",
        [{"tool": "CronCreate", "status": "completed", "event_type": "tool_result", "event_ref": "cron-create-1"}],
        declarations=claude_adapter.declared_primitives,
        scope={"request_id": "request-cron", "turn_id": "turn-cron", "os": "linux", "profile": "synthetic"},
    )
    external_due_only = native_primitive_observations(
        "claude-code",
        [{"delivery": "external_due_time", "status": "completed", "event_type": "external_due_time", "event_ref": "due-1"}],
        declarations=claude_adapter.declared_primitives,
        scope={"request_id": "request-external-due", "turn_id": "turn-due", "profile": "synthetic"},
    )
    mismatched_reread = native_primitive_observations(
        "synthetic-adapter",
        [{"operation": "task_durable_reread", "status": "completed", "durable_readback": True,
          "resource_id": "task-a", "result_resource_id": "task-b", "event_ref": "readback-bad"}],
        declarations=(), scope={"request_id": "request-readback", "turn_id": "turn-readback"},
    )
    matching_reread = native_primitive_observations(
        "synthetic-adapter",
        [{"operation": "task_durable_reread", "status": "completed", "durable_readback": True,
          "resource_id": "task-a", "result_resource_id": "task-a", "event_ref": "readback-good"}],
        declarations=(), scope={"request_id": "request-readback", "turn_id": "turn-readback"},
    )
    native_delivery = native_primitive_observations(
        "synthetic-adapter",
        [{"operation": "native_scheduler_delivery", "native_delivery": True, "delivery_id": "native-tick-1",
          "status": "completed", "event_ref": "native-delivery-1"}],
        declarations=(), scope={"request_id": "request-native-tick", "turn_id": "turn-native-tick"},
    )
    _assert(
        task_only["operations"]["task_list_write"]["outcome"] == "observed_success"
        and task_only["operations"]["task_creation"]["outcome"] == "unknown"
        and task_only["operations"]["task_durable_reread"]["outcome"] == "unknown"
        and task_only["operations"]["goal_declaration"]["outcome"] == "unknown"
        and task_only["operations"]["memory_write"]["outcome"] == "unknown"
        and task_only["operations"]["task_list_write"]["evidence"][0]["event_ref"] == "event-task-1"
        and task_only["operations"]["task_list_write"]["evidence"][0]["scope"]["request_id"] == "request-task"
        and goal_failed["operations"]["goal_declaration"]["outcome"] == "observed_failure"
        and planning_only["operations"]["goal_declaration"]["outcome"] == "unknown"
        and planning_only["operations"]["task_creation"]["outcome"] == "unknown"
        and scheduler_create["operations"]["native_scheduler_creation"]["outcome"] == "observed_success"
        and scheduler_create["operations"]["native_scheduler_delivery"]["outcome"] == "unknown"
        and external_due_only["operations"]["native_scheduler_delivery"]["outcome"] == "unknown"
        and mismatched_reread["operations"]["task_durable_reread"]["outcome"] == "unknown"
        and matching_reread["operations"]["task_durable_reread"]["outcome"] == "observed_success"
        and native_delivery["operations"]["native_scheduler_delivery"]["outcome"] == "observed_success",
        "native-operation-evidence-is-typed-correlated-and-excludes-plans-and-external-due-events",
        failures, checks,
    )

    # ---- Path safety and PID identity ------------------------------------
    with tempfile.TemporaryDirectory(prefix="of-background-selfcheck-") as tmp:
        root = Path(tmp)
        workspace_root = root / "paths"
        workspace_root.mkdir()
        task_path = workspace_root / "TASK.md"
        task_path.write_text("Durable inert task.\n", encoding="utf-8")
        valid = validate_start_paths(str(workspace_root), str(task_path), str(root / "state"))
        _assert(valid[0] == workspace_root and valid[2] == root / "state",
                "fresh-disjoint-paths-admitted", failures, checks)
        prior_cwd = Path.cwd()
        try:
            os.chdir(root)
            relative = validate_start_paths("paths", "paths/TASK.md", "relative-state")
        finally:
            os.chdir(prior_cwd)
        _assert(relative[0] == workspace_root and relative[1] == task_path
                and relative[2] == root / "relative-state",
                "relative-cli-paths-resolve-from-invoking-cwd", failures, checks)
        (root / "state").mkdir()
        try:
            validate_start_paths(str(workspace_root), str(task_path), str(root / "state"))
            failures.append("colliding-state-directory-was-not-refused")
        except StoreError:
            checks.append("colliding-state-directory-refused")
        outside = root / "outside"
        outside.mkdir()
        (outside / "TASK.md").write_text("external task\n", encoding="utf-8")
        link = root / "workspace-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
            try:
                validate_start_paths(str(link), str(task_path), str(root / "state-2"))
                failures.append("workspace-symlink-escape-was-not-refused")
            except StoreError:
                checks.append("workspace-symlink-refused")
            task_link = workspace_root / "TASK-link.md"
            task_link.symlink_to(outside / "TASK.md")
            try:
                validate_start_paths(str(workspace_root), str(task_link), str(root / "state-3"))
                failures.append("task-symlink-escape-was-not-refused")
            except StoreError:
                checks.append("task-symlink-refused")
            state_parent_link = root / "state-parent-link"
            state_parent_link.symlink_to(outside, target_is_directory=True)
            try:
                validate_start_paths(str(workspace_root), str(task_path), str(state_parent_link / "state-4"))
                failures.append("state-symlink-escape-was-not-refused")
            except StoreError:
                checks.append("state-symlink-refused")
        except OSError:
            failures.append("symlink-regression-could-not-create-fixture")

        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True,
        )
        identity = process_identity(sleeper.pid)
        if valid_process_identity(identity):
            malformed = [
                {},
                dict(identity, starttime=None),
                dict(identity, pgid=identity["pgid"] + 1),
                dict(identity, pid=True),
            ]
            malformed_results = [
                (process_identity_status(item), terminate_identity_checked(item, grace_seconds=0))
                for item in malformed
            ]
            _assert(all(status == "unknown" and not result.get("attempted")
                        for status, result in malformed_results)
                    and identity_alive(identity),
                    "malformed-identities-are-unknown-and-never-signalled", failures, checks)
            stale = dict(identity, starttime=identity["starttime"] + 1)
            refusal = terminate_identity_checked(stale, grace_seconds=0)
            _assert(process_identity_status(stale) == "dead" and not refusal.get("attempted"),
                    "pid-reuse-identity-mismatch-never-signalled", failures, checks)
            termination = terminate_identity_checked(identity, grace_seconds=0.2)
            try:
                sleeper.wait(timeout=4)
            except subprocess.TimeoutExpired:
                failures.append("identity-checked-sleeper-cleanup-timed-out")
            else:
                checks.append("identity-checked-process-group-cleanup")
        else:
            try:
                sleeper.wait(timeout=35)
            except subprocess.TimeoutExpired:
                failures.append("unknown-process-identity-left-sleeper-unreaped")
            failures.append("process-identity-not-observed-for-regression")

        # ---- Full inert service lifecycle for both adapter argv surfaces ---
        fake = root / "fake-harness"
        fake.write_text(FAKE_CLI, encoding="utf-8")
        fake.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        fake_db = root / "session-db"
        active_lock = root / "native-active.lock"
        overlap_file = root / "native-overlap"

        with _fake_env("codex", fake_db, active_lock, overlap_file, sleep=0.1):
            guard_workspace, guard_task, _ = _new_fixture(root, "guard-paths")
            previous_home = os.environ.get("HOME")
            metadata_home = root / "guard-metadata-home"
            os.environ["HOME"] = str(metadata_home)
            try:
                with locked_workspace(guard_workspace) as guard:
                    metadata_path = guard.metadata_path
                try:
                    with locked_workspace(guard_workspace, root=REPO_ROOT / "scripts"):
                        failures.append("tracked-checkout-was-accepted-for-private-owner-metadata")
                except StoreError as exc:
                    _assert(exc.code == "workspace_guard_inside_checkout_must_be_ignored",
                            "private-owner-metadata-refuses-tracked-checkout-path", failures, checks)
                external_metadata = root / "external-owner.json"
                external_metadata.write_text("{}", encoding="utf-8")
                metadata_path.symlink_to(external_metadata)
                try:
                    _start_fixture("codex", guard_workspace, guard_task, root / "guard-meta-run", fake,
                                   "FAKE_WRITE_STAGE_ONE")
                    failures.append("workspace-owner-metadata-symlink-was-not-refused")
                except StoreError as exc:
                    _assert(exc.code == "workspace_guard_metadata_symlink_refused",
                            "workspace-owner-metadata-symlink-refused", failures, checks)
            finally:
                if previous_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = previous_home

            guard_link_workspace, guard_link_task, _ = _new_fixture(root, "guard-root-link")
            linked_home = root / "guard-root-link-home"
            linked_state_parent = linked_home / ".local" / "state"
            linked_state_parent.mkdir(parents=True)
            link_target = root / "linked-state-target"
            link_target.mkdir()
            (linked_state_parent / "openforage-background-agent").symlink_to(
                link_target, target_is_directory=True,
            )
            previous_home = os.environ.get("HOME")
            os.environ["HOME"] = str(linked_home)
            try:
                _start_fixture("codex", guard_link_workspace, guard_link_task,
                               root / "guard-link-run", fake, "FAKE_WRITE_STAGE_ONE")
                failures.append("workspace-owner-root-symlink-was-not-refused")
            except StoreError as exc:
                _assert(exc.code in {
                    "workspace_guard_symlink_refused",
                    "workspace_guard_inside_checkout_must_be_ignored",
                },
                        "workspace-owner-root-symlink-refused", failures, checks)
            finally:
                if previous_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = previous_home

        race_workspace, race_task, _ = _new_fixture(root, "start-start-race")
        race_states = [root / "race-start-a", root / "race-start-b"]
        with _fake_env("codex", fake_db, active_lock, overlap_file, sleep=0.1):
            race_results: dict[str, object] = {}
            race_barrier = threading.Barrier(3)

            def race_start(label: str, state_path: Path) -> None:
                race_barrier.wait()
                try:
                    race_results[label] = _start_fixture(
                        "codex", race_workspace, race_task, state_path, fake,
                        "FAKE_WRITE_STAGE_ONE",
                    )
                except Exception as exc:
                    race_results[label] = exc

            race_threads = [
                threading.Thread(target=race_start, args=("a", race_states[0])),
                threading.Thread(target=race_start, args=("b", race_states[1])),
            ]
            for thread in race_threads:
                thread.start()
            race_barrier.wait()
            for thread in race_threads:
                thread.join(timeout=20)
            successful_starts = [label for label, result in race_results.items()
                                 if isinstance(result, tuple) and result[0] == 0 and result[1].get("status") == "ready"]
            refused_starts = [result for result in race_results.values()
                              if isinstance(result, StoreError) and result.code == "workspace_owned_by_existing_run"]
            _assert(len(successful_starts) == 1 and len(refused_starts) == 1,
                    "concurrent-starts-claim-one-canonical-workspace-across-run-dirs", failures, checks)
            race_state_by_label = {"a": race_states[0], "b": race_states[1]}
            for label, result in race_results.items():
                state_path = race_state_by_label[label]
                if isinstance(result, tuple) and state_path.exists():
                    race_run = __import__("background_agent_store").validate_existing_run(
                        str(state_path)
                    )
                    runs.append(race_run)
                    stop_code, stop_status = _stop_cli(state_path)
                    _assert(stop_code == 0 and stop_status.get("status") == "stopped",
                            "start-race-winning-owner-stopped-cleanly", failures, checks)
            released_state = root / "race-after-terminal-release"
            released_code, released_status = _start_fixture(
                "codex", race_workspace, race_task, released_state, fake, "FAKE_WRITE_STAGE_ONE",
            )
            _assert(released_code == 0 and released_status.get("status") == "ready",
                    "proven-terminal-stop-releases-workspace-for-new-run", failures, checks)
            if released_state.exists():
                released_run = __import__("background_agent_store").validate_existing_run(str(released_state))
                runs.append(released_run)
                release_stop_code, release_stop = _stop_cli(released_state)
                _assert(release_stop_code == 0 and release_stop.get("status") == "stopped",
                        "post-release-run-cleanly-stopped", failures, checks)

        survivor_workspace, survivor_task, survivor_state_dir = _new_fixture(root, "group-survivor")
        runs.append(survivor_state_dir)
        with _fake_env("codex", fake_db, active_lock, overlap_file, sleep=0.1):
            survivor_code, survivor_start = _start_fixture(
                "codex", survivor_workspace, survivor_task, survivor_state_dir, fake,
                "FAKE_GROUP_SURVIVOR",
            )
            survivor_run = __import__("background_agent_store").validate_existing_run(str(survivor_state_dir))
            runs[-1] = survivor_run
            live_group_status = survivor_start.get("last_child_process_group", {}).get("status")
            _assert(survivor_code != 0 and survivor_start.get("status") == "needs_reconciliation"
                    and survivor_start.get("active") is None
                    and live_group_status in {"alive", "present_unverified"}
                    and survivor_start.get("last_child_process_group", {}).get("alive_after_exit") is True,
                    "status-retains-observed-surviving-group-after-active-clears", failures, checks)
            time.sleep(1.1)
            _assert(public_status(survivor_run).get("last_child_process_group", {}).get("status") == "gone",
                    "last-group-observation-updates-after-survivor-exits", failures, checks)
            survivor_stop_code, survivor_stop = _stop_cli(survivor_state_dir)
            _assert(survivor_stop_code == 0 and survivor_stop.get("status") == "stopped",
                    "surviving-group-evidence-reconciled-before-terminal-release", failures, checks)

        for harness in ("opencode", "codex"):
            ws, task, state_dir = _new_fixture(root, f"basic-{harness}")
            runs.append(state_dir)
            memory = "marker-" + uuid.uuid4().hex[:12]
            with _fake_env(harness, fake_db, active_lock, overlap_file, sleep=1):
                code, status = _start_fixture(
                    harness, ws, task, state_dir, fake,
                    f"FAKE_MEMORY={memory}; FAKE_WRITE_STAGE_ONE; FAKE_NATIVE_TOOL",
                )
                _assert(code == 0 and status.get("status") == "ready"
                        and status.get("ownership_acknowledgement") == "observed"
                        and status.get("scheduler", {}).get("mode") == "manual"
                        and status.get("child_session_identity") != status.get("parent_session_identity"),
                        f"{harness}-detached-start-ownership-ack", failures, checks)
                run = __import__("background_agent_store").validate_existing_run(str(state_dir))
                state = load_state(run)
                _assert(identity_alive(state.get("controller_identity")),
                        f"{harness}-launcher-exit-controller-remains-live", failures, checks)
                public_text = json.dumps(public_status(run), sort_keys=True)
                _assert("raw-transcript-sentinel-inert" not in public_text
                        and "FAKE_WRITE_STAGE_ONE" not in public_text
                        and str(ws) not in public_text,
                        f"{harness}-publishable-status-hides-transcript-command-and-host-path", failures, checks)
                runs[-1] = run
                child_id = state["child_session_identity"]
                _assert((ws / "proof.txt").read_text(encoding="utf-8") == "stage-one\n",
                        f"{harness}-initial-real-marker-effect", failures, checks)
                native_status = public_status(run)["native_goal_task_memory"]
                expected_task_outcome = "observed_success" if harness == "opencode" else "unknown"
                _assert(native_status["task"]["status"] == expected_task_outcome
                        and native_status["goal"]["status"] == "unknown"
                        and native_status["memory"]["status"] == "unknown",
                        f"{harness}-native-capabilities-reported-independently-from-trace", failures, checks)

                # A duplicate service must refuse without changing the live owner.
                before = public_status(run)
                duplicate_code = _start_duplicate_service(run)
                after = public_status(run)
                _assert(duplicate_code != 0 and before["controller"] == after["controller"]
                        and after["status"] == "ready",
                        f"{harness}-duplicate-controller-refused-without-state-mutation", failures, checks)

                # A launch path is exclusive; the durable task digest cannot drift silently.
                try:
                    _start_fixture(harness, ws, task, state_dir, fake, "FAKE_WRITE_STAGE_ONE")
                    failures.append(f"{harness}-duplicate-start-was-not-refused")
                except StoreError:
                    checks.append(f"{harness}-duplicate-start-refused")
                try:
                    _start_fixture(harness, ws, task, root / f"second-state-{harness}", fake,
                                   "FAKE_WRITE_STAGE_ONE")
                    failures.append(f"{harness}-same-workspace-different-state-start-was-not-refused")
                except StoreError as exc:
                    _assert(exc.code == "workspace_owned_by_existing_run",
                            f"{harness}-workspace-ownership-spans-state-directories", failures, checks)
                saved_task = task.read_text(encoding="utf-8")
                task.write_text(saved_task + "changed\n", encoding="utf-8")
                try:
                    _turn(__import__("argparse").Namespace(state_dir=str(state_dir), command="do not run", wait_seconds=0))
                    failures.append(f"{harness}-mutated-task-digest-was-not-refused")
                except AgentError as exc:
                    _assert(exc.code == "task_digest_changed_start_new_run_or_reconcile",
                            f"{harness}-task-digest-change-refused", failures, checks)
                task.write_text(saved_task, encoding="utf-8")

                # Two concurrent request producers may never overlap native CLI owners.
                os.environ["BA_FAKE_SLEEP_SECONDS"] = "1"
                results: list[tuple[int, dict] | Exception] = []
                barrier = threading.Barrier(3)

                def submit_race() -> None:
                    barrier.wait()
                    try:
                        results.append(_turn(__import__("argparse").Namespace(
                            state_dir=str(state_dir), command="FAKE_SLEEP", wait_seconds=0,
                        )))
                    except Exception as exc:  # record race refusal without hiding it
                        results.append(exc)

                threads = [threading.Thread(target=submit_race) for _ in range(2)]
                for thread in threads:
                    thread.start()
                barrier.wait()
                for thread in threads:
                    thread.join(timeout=4)
                accepted = [item for item in results if isinstance(item, tuple) and item[0] == 0]
                _assert(len(accepted) in {1, 2}, f"{harness}-concurrent-turn-producers-completed", failures, checks)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    with run.lock():
                        state = load_state(run)
                    if not any(item.get("status") in {"queued", "running"} for item in state["turns"]):
                        break
                    time.sleep(0.03)
                _assert(not overlap_file.exists(), f"{harness}-turn-race-has-no-overlapping-native-child", failures, checks)

                # Pause an active process group, refuse a sibling resume id, then reconcile exact history.
                settled_state = load_state(run)
                if settled_state.get("status") != "ready":
                    failures.append(f"{harness}-concurrent-turn-finalized-as-{settled_state.get('status')}:")
                    continue
                os.environ["BA_FAKE_SLEEP_SECONDS"] = "12"
                code, submitted = _turn(__import__("argparse").Namespace(
                    state_dir=str(state_dir), command="FAKE_SLEEP", wait_seconds=0,
                ))
                active = _wait_active(run)
                _assert(code == 0 and active is not None,
                        f"{harness}-active-child-process-observed-for-pause", failures, checks)
                old_fence = int(load_state(run)["fencing_token"])
                pause_code, paused = _control_signal(run, "paused", wait_seconds=5)
                _assert(pause_code == 0 and paused.get("status") == "paused"
                        and paused.get("reconciliation_required") is True,
                        f"{harness}-active-pause-fences-and-preserves-uncertainty", failures, checks)
                _assert(paused.get("active") is None
                        and paused.get("last_child_process_group", {}).get("status") == "gone",
                        f"{harness}-status-retains-last-process-group-observation-after-active-clears", failures, checks)
                _assert(not identity_alive(active["child_identity"]),
                        f"{harness}-pause-identity-checks-child-process-group", failures, checks)
                try:
                    _resume(__import__("argparse").Namespace(
                        state_dir=str(state_dir), command="FAKE_RECALL_MEMORY", session_id="wrong-session",
                        wait_seconds=3,
                    ))
                    failures.append(f"{harness}-mismatched-resume-target-was-not-refused")
                except AgentError as exc:
                    _assert(exc.code == "mismatched_resume_identity",
                            f"{harness}-mismatched-resume-target-refused", failures, checks)
                try:
                    resume_code, resumed = _resume(__import__("argparse").Namespace(
                        state_dir=str(state_dir),
                        command="FAKE_RECALL_MEMORY; FAKE_WRITE_STAGE_TWO",
                        session_id=child_id,
                        wait_seconds=10,
                    ))
                except AgentError as exc:
                    paused_state = load_state(run)
                    failures.append(
                        f"{harness}-resume-after-pause-refused:{exc.code}:"
                        f"active={bool(paused_state.get('active'))}:"
                        f"controller_alive={identity_alive(paused_state.get('controller_identity'))}"
                    )
                    resume_code, resumed = 1, public_status(run)
                _assert(resume_code == 0 and resumed.get("status") == "ready"
                        and resumed.get("child_session_identity") == child_id
                        and resumed.get("fencing_token", 0) > old_fence,
                        f"{harness}-exact-child-reconciliation-uses-new-fence", failures, checks)
                _assert((ws / "proof.txt").read_text(encoding="utf-8") == "stage-two\n",
                        f"{harness}-resumed-durable-task-continuation", failures, checks)
                resumed_row = load_state(run)["turns"][-1]
                trace = _trace_for_turn(run, resumed_row["request_id"], harness)
                recalled = (trace.get("final_ack") or {}).get("recalled_marker")
                _assert(recalled == memory,
                        f"{harness}-resumed-native-session-history-marker-recalled", failures, checks)

                # A recovery attempt while the controller still owns its lock is refused.
                try:
                    _recover(__import__("argparse").Namespace(
                        state_dir=str(state_dir), command="FAKE_RECALL_MEMORY", session_id=child_id,
                        wait_seconds=3,
                    ))
                    failures.append(f"{harness}-live-owner-recovery-was-not-refused")
                except AgentError as exc:
                    _assert(exc.code in {"controller_still_live_recovery_refused", "controller_owner_lock_still_live"},
                            f"{harness}-recovery-refuses-live-owner", failures, checks)

                # Deliberately kill only the identity-checked controller while idle.
                before_recovery_fence = int(load_state(run)["fencing_token"])
                _assert(_kill_controller(run), f"{harness}-controller-killed-by-recorded-identity", failures, checks)
                diagnosis = diagnose_run(run)
                _assert(diagnosis["classification"] == "stale_controller",
                        f"{harness}-stale-controller-diagnosed", failures, checks)
                if identity_alive(load_state(run).get("controller_identity")):
                    failures.append(f"{harness}-controller-identity-remained-live-after-kill")
                with run.lock():
                    malformed_state = load_state(run)
                    malformed_state["active"] = {
                        "request_id": "unknown-active",
                        "turn_id": "unknown-active",
                        "fence": malformed_state["fencing_token"],
                        "child_identity": {},
                        "process_started": True,
                    }
                    run.save(malformed_state)
                pending_code, pending_status = _control_signal(run, "paused", wait_seconds=0)
                _assert(pending_code == 2 and pending_status.get("cleanup_pending") is True
                        and pending_status.get("status") == "paused"
                        and pending_status.get("active", {}).get("child_process_identity_status") == "unknown"
                        and pending_status.get("agent_process_termination", {}).get("attempted") is False,
                        f"{harness}-unknown-active-identity-preserves-pause-and-returns-pending", failures, checks)
                try:
                    _recover(__import__("argparse").Namespace(
                        state_dir=str(state_dir), command="FAKE_RECALL_MEMORY", session_id=child_id,
                        wait_seconds=3,
                    ))
                    failures.append(f"{harness}-empty-active-identity-transfer-was-not-refused")
                except AgentError as exc:
                    _assert(exc.code == "active_child_identity_unknown_recovery_refused",
                            f"{harness}-empty-active-child-identity-never-transfers", failures, checks)
                with run.lock():
                    repaired_state = load_state(run)
                    repaired_state["active"] = None
                    repaired_state["reconciliation_required"] = True
                    run.save(repaired_state)

                race_state = root / f"race-start-recover-{harness}"
                race_results: dict[str, object] = {}
                race_barrier = threading.Barrier(3)

                def recover_race() -> None:
                    race_barrier.wait()
                    try:
                        race_results["recover"] = _recover(__import__("argparse").Namespace(
                            state_dir=str(state_dir), command="FAKE_RECALL_MEMORY", session_id=child_id,
                            wait_seconds=10,
                        ))
                    except Exception as exc:
                        race_results["recover"] = exc

                def start_race() -> None:
                    race_barrier.wait()
                    try:
                        race_results["start"] = _start_fixture(
                            harness, ws, task, race_state, fake, "FAKE_WRITE_STAGE_ONE",
                        )
                    except Exception as exc:
                        race_results["start"] = exc

                race_threads = [threading.Thread(target=recover_race), threading.Thread(target=start_race)]
                for thread in race_threads:
                    thread.start()
                race_barrier.wait()
                for thread in race_threads:
                    thread.join(timeout=20)
                recover_result = race_results.get("recover")
                start_result = race_results.get("start")
                if isinstance(start_result, tuple) and race_state.exists():
                    runs.append(__import__("background_agent_store").validate_existing_run(str(race_state)))
                _assert(isinstance(recover_result, tuple) and recover_result[0] == 0
                        and recover_result[1].get("status") == "ready"
                        and isinstance(start_result, StoreError)
                        and start_result.code == "workspace_owned_by_existing_run",
                        f"{harness}-start-recover-race-keeps-original-run-owner", failures, checks)
                recover_code, recovered = recover_result if isinstance(recover_result, tuple) else (1, {})
                _assert(recover_code == 0 and recovered.get("status") == "ready"
                        and recovered.get("child_session_identity") == child_id
                        and recovered.get("fencing_token", 0) > before_recovery_fence
                        and recovered.get("controller", {}).get("generation", 0) >= 2,
                        f"{harness}-stale-controller-recovered-same-child-under-new-fence", failures, checks)

                # Stop an active process group in another run and verify terminality.
                ws_stop, task_stop, state_stop = _new_fixture(root, f"stop-{harness}")
                runs.append(state_stop)
                code_stop, _ = _start_fixture(harness, ws_stop, task_stop, state_stop, fake, "FAKE_WRITE_STAGE_ONE")
                stop_run = __import__("background_agent_store").validate_existing_run(str(state_stop))
                runs[-1] = stop_run
                code_turn, _ = _turn(__import__("argparse").Namespace(
                    state_dir=str(state_stop), command="FAKE_SLEEP", wait_seconds=0,
                ))
                stop_active = _wait_active(stop_run)
                stop_code, stopped = _control_signal(stop_run, "stopped", wait_seconds=5)
                _wait_service_dead(stop_run)
                _assert(code_stop == 0 and code_turn == 0 and stop_active is not None
                        and stop_code == 0 and stopped.get("status") == "stopped"
                        and stopped.get("last_child_process_group", {}).get("status") == "gone"
                        and not identity_alive(stop_active["child_identity"]),
                        f"{harness}-active-stop-is-terminal-and-process-identity-checked", failures, checks)
                for action in (
                    lambda: _turn(__import__("argparse").Namespace(state_dir=str(state_stop), command="x", wait_seconds=0)),
                    lambda: _resume(__import__("argparse").Namespace(state_dir=str(state_stop), command="reconcile", session_id=None, wait_seconds=1)),
                    lambda: _recover(__import__("argparse").Namespace(state_dir=str(state_stop), command="reconcile", session_id=None, wait_seconds=1)),
                ):
                    try:
                        action()
                        failures.append(f"{harness}-terminal-run-reactivated")
                        break
                    except AgentError:
                        continue
                else:
                    checks.append(f"{harness}-terminal-stop-never-reactivates")

                # A dead controller's proven-gone active child is terminally uncertain,
                # then releases the canonical workspace for a fresh owner.
                crash_ws, crash_task, crash_state_dir = _new_fixture(root, f"crash-active-stop-{harness}")
                crash_start_code, _ = _start_fixture(
                    harness, crash_ws, crash_task, crash_state_dir, fake, "FAKE_WRITE_STAGE_ONE",
                    interval_seconds=120.0,
                    continuation_command="FAKE_RECURRENCE_ACTION: preserve this ongoing inert marker task.",
                )
                crash_run = __import__("background_agent_store").validate_existing_run(str(crash_state_dir))
                runs.append(crash_run)
                os.environ["BA_FAKE_SLEEP_SECONDS"] = "30"
                crash_turn_code, _ = _turn(__import__("argparse").Namespace(
                    state_dir=str(crash_state_dir), command="FAKE_SLEEP", wait_seconds=0,
                ))
                crash_active = _wait_active(crash_run)
                controller_killed_during_turn = bool(crash_active and _kill_controller(crash_run))
                crash_stop_code, crash_stopped = _stop_cli(crash_state_dir)
                crash_stopped_state = load_state(crash_run)
                crash_turn = next((item for item in crash_stopped_state["turns"]
                                   if crash_active and item.get("request_id") == crash_active.get("request_id")), None)
                _assert(
                    crash_start_code == 0 and crash_turn_code == 0 and crash_active is not None
                    and controller_killed_during_turn and crash_stop_code == 0
                    and crash_stopped.get("status") == "stopped"
                    and crash_stopped.get("active") is None
                    and crash_stopped.get("scheduler", {}).get("state") == "cancelled"
                    and crash_stopped.get("scheduler", {}).get("next_due_at") is None
                    and crash_stopped.get("workspace_ownership_released") is True
                    and crash_turn is not None and crash_turn.get("status") == "uncertain"
                    and crash_turn.get("failure_code") == "controller_crashed_during_turn"
                    and crash_turn.get("process_started") is True
                    and crash_turn.get("child_process_identity") == crash_active.get("child_identity")
                    and crash_turn.get("child_process_group_alive_after_exit") is False
                    and crash_turn.get("child_process_group_status_after_exit") == "gone"
                    and not crash_turn.get("acknowledgement_observed")
                    and not identity_alive(crash_active["child_identity"]),
                    f"{harness}-crashed-active-stop-records-uncertainty-and-releases-workspace",
                    failures, checks,
                )
                repeat_stop_code, repeat_stop = _stop_cli(crash_state_dir)
                _assert(repeat_stop_code == 0 and repeat_stop.get("status") == "stopped",
                        f"{harness}-repeated-terminal-stop-is-safe", failures, checks)

                fresh_state_dir = root / f"crash-active-fresh-owner-{harness}"
                try:
                    fresh_code, fresh_status = _start_fixture(
                        harness, crash_ws, crash_task, fresh_state_dir, fake, "FAKE_WRITE_STAGE_ONE",
                    )
                except StoreError as exc:
                    failures.append(f"{harness}-proven-crash-stop-workspace-reclaim-refused:{exc.code}")
                else:
                    fresh_run = __import__("background_agent_store").validate_existing_run(str(fresh_state_dir))
                    runs.append(fresh_run)
                    _assert(fresh_code == 0 and fresh_status.get("status") == "ready",
                            f"{harness}-proven-crash-stop-allows-fresh-workspace-claim", failures, checks)
                    fresh_stop_code, fresh_stopped = _stop_cli(fresh_state_dir)
                    _assert(fresh_stop_code == 0 and fresh_stopped.get("status") == "stopped",
                            f"{harness}-fresh-workspace-owner-stops-cleanly", failures, checks)

                # Unknown child identity evidence must not be cleared or signalled by stop.
                unknown_ws, unknown_task, unknown_state_dir = _new_fixture(root, f"unknown-active-stop-{harness}")
                unknown_start_code, _ = _start_fixture(
                    harness, unknown_ws, unknown_task, unknown_state_dir, fake, "FAKE_WRITE_STAGE_ONE",
                )
                unknown_run = __import__("background_agent_store").validate_existing_run(str(unknown_state_dir))
                runs.append(unknown_run)
                unknown_controller_killed = _kill_controller(unknown_run)
                unknown_request_id = "unknown-" + uuid.uuid4().hex
                unknown_turn_id = "turn-unknown-" + uuid.uuid4().hex[:12]
                with locked_workspace(unknown_ws) as guard:
                    with unknown_run.lock():
                        unknown_state = load_state(unknown_run)
                        unknown_state["turns"].append({
                            "request_id": unknown_request_id,
                            "turn_id": unknown_turn_id,
                            "kind": "turn",
                            "status": "running",
                            "fence": unknown_state["fencing_token"],
                            "process_started": True,
                        })
                        unknown_state["active"] = {
                            "request_id": unknown_request_id,
                            "turn_id": unknown_turn_id,
                            "fence": unknown_state["fencing_token"],
                            "controller_identity": unknown_state["controller_identity"],
                            "child_identity": {},
                            "process_started": True,
                            "cancel_requested": None,
                        }
                        unknown_run.save(unknown_state)
                    with unknown_run.lock():
                        update_workspace_owner(guard, unknown_run, load_state(unknown_run))
                unknown_stop_code, unknown_stopped = _stop_cli(unknown_state_dir)
                unknown_after = load_state(unknown_run)
                unknown_row = next((item for item in unknown_after["turns"]
                                    if item.get("request_id") == unknown_request_id), None)
                _assert(
                    unknown_start_code == 0 and unknown_controller_killed and unknown_stop_code == 2
                    and unknown_stopped.get("status") == "stopped"
                    and unknown_stopped.get("cleanup_pending") is True
                    and unknown_stopped.get("workspace_ownership_released") is False
                    and unknown_stopped.get("active", {}).get("child_process_identity_status") == "unknown"
                    and unknown_stopped.get("agent_process_termination", {}).get("attempted") is False
                    and unknown_after.get("active", {}).get("child_identity") == {}
                    and unknown_row is not None and unknown_row.get("status") == "running"
                    and not unknown_row.get("acknowledgement_observed"),
                    f"{harness}-unknown-crashed-child-identity-remains-pending-and-unsignalled",
                    failures, checks,
                )
                with locked_workspace(unknown_ws) as guard:
                    with unknown_run.lock():
                        unknown_state = load_state(unknown_run)
                        unknown_state["active"] = None
                        unknown_state["turns"] = [
                            item for item in unknown_state["turns"]
                            if item.get("request_id") != unknown_request_id
                        ]
                        unknown_run.save(unknown_state)
                    with unknown_run.lock():
                        update_workspace_owner(guard, unknown_run, load_state(unknown_run))
                    with unknown_run.lock():
                        unknown_state = load_state(unknown_run)
                    _assert(release_stopped_workspace(guard, unknown_run, unknown_state),
                            f"{harness}-unknown-identity-fixture-released-after-test-repair", failures, checks)

        # Missing acknowledgement remains a failure; explicit recovery must use the exact candidate id.
        harness = "codex"
        ws, task, state_dir = _new_fixture(root, "missing-ack")
        runs.append(state_dir)
        with _fake_env(harness, fake_db, active_lock, overlap_file):
            code, status = _start_fixture(harness, ws, task, state_dir, fake, "FAKE_NO_ACK")
            run = __import__("background_agent_store").validate_existing_run(str(state_dir))
            runs[-1] = run
            state = load_state(run)
            candidate = state.get("candidate_child_session_identity")
            _assert(code != 0 and status.get("status") == "needs_reconciliation"
                    and status.get("child_session_identity") is None and candidate
                    and state.get("ownership_acknowledgement") == "not_observed",
                    "successful-cli-exit-without-ownership-ack-is-not-ready", failures, checks)
            try:
                _recover(__import__("argparse").Namespace(
                    state_dir=str(state_dir), command="reconcile", session_id=None, wait_seconds=3,
                ))
                failures.append("unacknowledged-candidate-adopted-without-exact-id")
            except AgentError as exc:
                _assert(exc.code in {"controller_still_live_recovery_refused", "exact_candidate_session_id_required_for_recovery"},
                        "missing-ack-recovery-needs-explicit-exact-target", failures, checks)
            _control_signal(run, "paused", wait_seconds=1)
            _assert(_kill_controller(run), "missing-ack-controller-stopped-for-safe-recovery", failures, checks)
            recover_code, adopted = _recover(__import__("argparse").Namespace(
                state_dir=str(state_dir), command="FAKE_RECALL_MEMORY", session_id=candidate,
                wait_seconds=10,
            ))
            _assert(recover_code == 0 and adopted.get("status") == "ready"
                    and adopted.get("child_session_identity") == candidate
                    and load_state(run).get("ownership_acknowledgement") == "reconciled",
                    "candidate-session-reconciled-before-ownership-is-claimed", failures, checks)

        # A fresh child that returns the parent identity must not establish ownership.
        ws, task, state_dir = _new_fixture(root, "parent-reuse")
        runs.append(state_dir)
        with _fake_env("opencode", fake_db, active_lock, overlap_file):
            os.environ["BA_FAKE_PARENT_REUSE"] = "1"
            try:
                code, status = _start_fixture("opencode", ws, task, state_dir, fake, "FAKE_WRITE_STAGE_ONE")
            finally:
                os.environ.pop("BA_FAKE_PARENT_REUSE", None)
            run = __import__("background_agent_store").validate_existing_run(str(state_dir))
            runs[-1] = run
            _assert(code != 0 and status.get("status") == "needs_reconciliation"
                    and status.get("child_session_identity") is None,
                    "parent-session-reuse-does-not-establish-owner", failures, checks)

        # Explicit external interval delivery continues one useful bounded action at a time.
        ws, task, state_dir = _new_fixture(root, "recurrence-success")
        runs.append(state_dir)
        continuation = "FAKE_RECURRENCE_ACTION: re-read TASK.md and perform one bounded local check."
        with _fake_env("codex", fake_db, active_lock, overlap_file, sleep=0.1):
            start_code, start_status = _start_fixture(
                "codex", ws, task, state_dir, fake, "FAKE_WRITE_STAGE_ONE",
                interval_seconds=1.0, continuation_command=continuation,
            )
            run = __import__("background_agent_store").validate_existing_run(str(state_dir))
            runs[-1] = run
            launcher_returned_at = time.time()
            initial_state = load_state(run)
            due = initial_state["recurrence"].get("next_due_at")
            _assert(start_code == 0 and start_status.get("status") == "ready"
                    and start_status.get("scheduler", {}).get("mode") == "external_interval"
                    and initial_state["recurrence"].get("state") == "scheduled"
                    and isinstance(due, (int, float)) and due > launcher_returned_at,
                    "external-recurrence-is-explicit-and-first-due-follows-launcher-return", failures, checks)
            due_rows = _wait_due_acknowledgements(run, 2, timeout=8)
            _assert(len(due_rows) >= 2 and all(row.get("status") == "acknowledged" for row in due_rows[:2])
                    and all(row.get("delivery") == "external_due_time"
                            and row.get("child_session_identity") == initial_state.get("child_session_identity")
                            for row in due_rows[:2]),
                    "two-due-time-continuations-use-the-exact-child-and-valid-ack", failures, checks)
            marker_path = ws / "recurrence.log"
            marker_lines = marker_path.read_text(encoding="utf-8").splitlines() if marker_path.is_file() else []
            _assert(len(marker_lines) >= 2 and len(set(marker_lines)) == len(marker_lines)
                    and all(row.get("turn_id") in marker_lines for row in due_rows[:2]),
                    "due-continuation-command-performs-distinct-useful-local-actions", failures, checks)
            pause_code, paused = _control_signal(run, "paused", wait_seconds=3)
            paused_state = load_state(run)
            paused_count = len([item for item in paused_state["turns"]
                                if item.get("delivery") == "external_due_time"])
            _assert(pause_code == 0 and paused.get("status") == "paused"
                    and paused.get("scheduler", {}).get("state") == "paused"
                    and paused.get("scheduler", {}).get("next_due_at") is None,
                    "pause-drains-external-due-trigger", failures, checks)
            time.sleep(1.2)
            after_pause = load_state(run)
            _assert(len([item for item in after_pause["turns"]
                         if item.get("delivery") == "external_due_time"]) == paused_count,
                    "paused-due-window-dispatches-no-continuation", failures, checks)
            resume_code, resumed = _resume(__import__("argparse").Namespace(
                state_dir=str(state_dir), command="FAKE_RECONCILE", session_id=initial_state["child_session_identity"],
                wait_seconds=8,
            ))
            _assert(resume_code == 0 and resumed.get("status") == "ready"
                    and resumed.get("scheduler", {}).get("state") == "scheduled",
                    "reconciliation-restores-only-the-authorized-recurrence", failures, checks)
            before_stop_state = load_state(run)
            before_stop_count = sum(item.get("delivery") == "external_due_time"
                                    for item in before_stop_state["turns"])
            stop_code, stopped = _stop_cli(state_dir)
            _assert(stop_code == 0 and stopped.get("status") == "stopped"
                    and stopped.get("scheduler", {}).get("state") == "cancelled"
                    and stopped.get("scheduler", {}).get("next_due_at") is None,
                    "terminal-stop-cancels-recurrence-and-releases-clean-workspace", failures, checks)
            time.sleep(1.2)
            after_stop = load_state(run)
            _assert(len([item for item in after_stop["turns"]
                         if item.get("delivery") == "external_due_time"]) == before_stop_count
                    and process_group_observation(after_stop.get("controller_identity")) == "gone",
                    "stopped-due-window-has-no-more-dispatch-or-owned-controller-group", failures, checks)

        # Missing acknowledgements and either non-progress status permanently block due retries.
        for label, bootstrap, expected_state in (
            ("blocked", "FAKE_BLOCKED_STATUS", "agent_task_status_blocked"),
            ("complete", "FAKE_COMPLETE_STATUS", "agent_task_status_agent_reported_complete"),
            ("missing", "FAKE_NO_ACK", "missing_native_ack"),
        ):
            ws_block, task_block, state_block = _new_fixture(root, f"recurrence-{label}")
            runs.append(state_block)
            with _fake_env("codex", fake_db, active_lock, overlap_file, sleep=0.1):
                blocked_code, blocked_status = _start_fixture(
                    "codex", ws_block, task_block, state_block, fake, bootstrap,
                    interval_seconds=1.0, continuation_command=continuation,
                )
                block_run = __import__("background_agent_store").validate_existing_run(str(state_block))
                runs[-1] = block_run
                block_state = load_state(block_run)
                time.sleep(1.2)
                block_state = load_state(block_run)
                due_count = sum(item.get("delivery") == "external_due_time" for item in block_state["turns"])
                _assert(block_state["recurrence"].get("state") == "blocked"
                        and block_state["recurrence"].get("block_reason") == expected_state
                        and block_state["recurrence"].get("next_due_at") is None and due_count == 0
                        and (blocked_code == 1 if label == "missing" else blocked_code == 0),
                        f"external-recurrence-{label}-outcome-blocks-without-retry", failures, checks)
                terminal_code, terminal_status = _stop_cli(state_block)
                _assert(terminal_code == 0 and terminal_status.get("status") == "stopped",
                        f"external-recurrence-{label}-fixture-cleanly-stopped", failures, checks)

        _assert(not overlap_file.exists(), "all-inert-child-process-groups-were-serialized", failures, checks)

        # ---- Strict cleanup: stop each owned controller, then verify exact identities. ----
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(root / "private-home")
        try:
            for item in runs:
                try:
                    run = item if hasattr(item, "load") else __import__("background_agent_store").validate_existing_run(str(item))
                    with run.lock():
                        state = load_state(run)
                    if state.get("status") != "stopped":
                        _control_signal(run, "stopped", wait_seconds=3)
                    _wait_service_dead(run, timeout=4)
                    with run.lock():
                        state = load_state(run)
                    controller = state.get("controller_identity")
                    active = state.get("active")
                    child = active.get("child_identity") if active else None
                    if child and identity_alive(child):
                        terminate_identity_checked(child, grace_seconds=0.5)
                    if controller and identity_alive(controller):
                        terminate_identity_checked(controller, grace_seconds=0.5)
                    if controller and isinstance(controller.get("pid"), int):
                        _reap_local_child(controller["pid"])
                    _assert(not (controller and identity_alive(controller))
                            and not (child and identity_alive(child))
                            and (state.get("status") != "stopped" or
                                 (process_group_observation(state.get("controller_identity")) == "gone"
                                  and owned_processes_gone(state))),
                            "cleanup-left-no-owned-controller-or-child", failures, checks)
                except Exception as exc:  # no raw exception text or path in the check output
                    failures.append(f"cleanup-error:{type(exc).__name__}")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    if failures:
        for failure in failures:
            print(f"FAIL: inert background-agent selfcheck: {failure}")
        print(f"{len(failures)} failure(s), {len(checks)} check(s) completed")
        return 1
    print(f"note: inert background-agent selfcheck: {len(checks)} checks passed (fake local processes only)")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(run_selfcheck())
