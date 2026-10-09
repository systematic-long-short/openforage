#!/usr/bin/env python3
"""Bounded, caller-invoked native proof for built-in CLI adapters.

This is not an offline selfcheck. It makes real harness calls only when the
caller explicitly supplies a harness and model selector. Its scratch work is
in ignored ``.tmp`` and is kept separate from the source checkout.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import posixpath
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from background_agent import _resolve_binary  # noqa: E402
from background_agent_adapters import (  # noqa: E402
    AdapterError,
    build_argv,
    get_adapter,
    native_operation_observations,
    native_primitive_observations,
    parse_trace,
    validate_ack,
    validate_selector,
)
from background_adapters import observe_version  # noqa: E402
from background_adapters.conformance import evaluate_proof_summary, validate_proof_v2  # noqa: E402
from background_adapters.native_evidence import (  # noqa: E402
    aggregate_native_primitive_observations,
    legacy_group_projection,
    session_fingerprint,
)
from background_agent_ownership import owned_processes_gone  # noqa: E402
from background_agent_store import (  # noqa: E402
    StoreError,
    diagnose_run,
    identity_alive,
    load_state,
    process_group_alive,
    process_group_observation,
    process_identity_status,
    process_identity,
    public_status,
    task_sha256,
    terminate_identity_checked,
    validate_existing_run,
    valid_process_identity,
)

BACKGROUND_CLI = REPO_ROOT / "scripts" / "background_agent.py"
ACTIVE_DELAY_SECONDS = 30
RECURRENCE_INTERVAL_SECONDS = 5
POLL_SECONDS = 0.15
NETWORK_TOOL_RE = re.compile(r"web|search|browser|fetch|http|network|mcp", re.I)
SOURCE_FILES = (
    "scripts/background_agent.py",
    "scripts/background_agent_service.py",
    "scripts/background_agent_store.py",
    "scripts/background_agent_adapters.py",
    "scripts/background_agent_ownership.py",
    "scripts/background_agent_selfcheck.py",
    "scripts/background_adapters/__init__.py",
    "scripts/background_adapters/contract.py",
    "scripts/background_adapters/opencode.py",
    "scripts/background_adapters/codex.py",
    "scripts/background_adapters/claude_code.py",
    "scripts/background_adapters/native_evidence.py",
    "scripts/background_adapters/conformance.py",
    "scripts/onboarding/capabilities.py",
    "src/openforage/skills/agent_runtime_integration/references/harnesses.json",
    "examples/background-agent/proof.schema.json",
    "examples/background-agent/conformance.schema.json",
    "examples/background-agent/conformance.py",
    "examples/background-agent/conformance.schema.json",
    "examples/opencode-lifecycle/bridge_core.py",
    "examples/codex-lifecycle/codex_events.py",
    "examples/background-agent/proof.py",
)


class ProofAbort(RuntimeError):
    """Stop dependent proof steps after a failed evidence gate."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _source_file_digests() -> dict:
    manifest = {}
    paths = set(SOURCE_FILES)
    registry_path = REPO_ROOT / "src" / "openforage" / "skills" / "agent_runtime_integration" / "references" / "harnesses.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise ProofAbort("source_digest_registry_unavailable") from None
    if not isinstance(registry, list):
        raise ProofAbort("source_digest_registry_malformed")
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        records = entry.get("capability_records")
        if not isinstance(records, list):
            continue
        for record in records:
            artifact = record.get("evidence_artifact") if isinstance(record, dict) else None
            if not isinstance(artifact, str) or not artifact:
                continue
            normalized = posixpath.normpath(artifact)
            if (normalized != artifact or artifact.startswith("/")
                    or "\\" in artifact or ".." in Path(artifact).parts):
                raise ProofAbort("source_digest_evidence_path_invalid")
            paths.add(artifact)
    for relative in sorted(paths):
        path = REPO_ROOT / relative
        try:
            if path.is_symlink() or any(
                component.is_symlink()
                for component in path.parents
                if component != REPO_ROOT and component.is_relative_to(REPO_ROOT)
            ):
                raise ProofAbort("source_digest_symlink_refused")
            path.resolve(strict=True).relative_to(REPO_ROOT.resolve(strict=True))
            content = path.read_bytes()
        except OSError:
            raise ProofAbort("source_digest_input_unavailable") from None
        except ValueError:
            raise ProofAbort("source_digest_input_escape") from None
        except ProofAbort:
            raise
        manifest[relative] = {"sha256": _sha256(content), "bytes": len(content)}
    return manifest


def _isolated_git_env() -> dict[str, str]:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
    ):
        env.pop(name, None)
    return env


def _check_ignored_scratch(path: Path) -> bool:
    try:
        relative = path.relative_to(REPO_ROOT).as_posix()
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "--quiet", "--no-index", relative],
            capture_output=True,
            timeout=15,
            env=_isolated_git_env(),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _safe_scratch_parent() -> Path:
    scratch = REPO_ROOT / ".tmp"
    if scratch.is_symlink() or not _check_ignored_scratch(scratch / "background-agent-proof-probe"):
        raise ProofAbort("ignored_tmp_root_required")
    if not scratch.exists():
        try:
            scratch.mkdir(mode=0o700, exist_ok=False)
        except OSError:
            raise ProofAbort("ignored_tmp_root_create_failed") from None
    if not scratch.is_dir():
        raise ProofAbort("ignored_tmp_root_required")
    try:
        resolved = scratch.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ProofAbort("ignored_tmp_root_unresolvable") from None
    if resolved != scratch.absolute() or not resolved.is_relative_to(REPO_ROOT):
        raise ProofAbort("ignored_tmp_root_escape")
    return resolved


def _no_symlink_components(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ProofAbort("symlink_output_path_refused")


def _write_exclusive(path: Path, text: str, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise ProofAbort("proof_output_collision") from None
    except OSError:
        raise ProofAbort("proof_output_write_failed") from None


def _prepare_paths(output_value: str | None) -> tuple[str, Path, Path, Path, Path, object]:
    scratch = _safe_scratch_parent()
    proof_id = "background-proof-" + uuid.uuid4().hex
    root = scratch / proof_id
    if root.exists() or root.is_symlink():
        raise ProofAbort("scratch_run_id_collision")
    output = root / "proof.json"
    if output_value:
        output = Path(os.path.expanduser(output_value))
        if not output.is_absolute():
            output = REPO_ROOT / output
        output = Path(os.path.abspath(output))
        _no_symlink_components(output)
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise ProofAbort("proof_output_must_be_new_with_existing_parent")
        if output.is_relative_to(REPO_ROOT) and not _check_ignored_scratch(output):
            raise ProofAbort("proof_output_inside_checkout_must_be_ignored")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(root, 0o700)
    workspace = root / "workspace"
    state_dir = root / proof_id
    raw_parent = root / "parent-raw"
    workspace.mkdir(mode=0o700)
    raw_parent.mkdir(mode=0o700)
    if _paths_overlap(output, workspace) or _paths_overlap(output, state_dir):
        raise ProofAbort("proof_output_overlaps_scratch_inputs")
    output.parent.mkdir(mode=0o700, parents=False, exist_ok=True) if output.parent == root else None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        output_fd = os.open(output, flags, 0o600)
    except FileExistsError:
        raise ProofAbort("proof_output_collision") from None
    except OSError:
        raise ProofAbort("proof_output_reservation_failed") from None
    output_handle = os.fdopen(output_fd, "w", encoding="utf-8")
    return proof_id, root, workspace, state_dir, raw_parent, (output, output_handle)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _workspace_contains_marker(workspace: Path, marker: str) -> bool | None:
    needle = marker.encode("utf-8")
    try:
        for current, directories, files in os.walk(workspace, followlinks=False):
            for name in directories:
                if (Path(current) / name).is_symlink():
                    return None
            for name in files:
                path = Path(current) / name
                if path.is_symlink():
                    return None
                if needle in path.read_bytes():
                    return True
    except OSError:
        return None
    return False


def _isolated_workspace(workspace: Path, proof_id: str, *, claude_code: bool = False) -> Path:
    agents = (
        "# Inert background-agent proof rules\n\n"
        "This scratch repository is for a bounded offline marker proof. Do not use "
        "network, web search, browser, external MCP, nested agents, or any OpenForage "
        "registration, campaign, search, evaluation, worker, or submission operation. "
        "Only inspect TASK.md and edit the proof, phase-readiness, or recurrence marker "
        "files explicitly named by a bounded caller instruction.\n"
    )
    task = (
        "# Durable inert proof task\n\n"
        f"Proof identity: `{proof_id}`. Read this task before each turn. Only the "
        "explicit initial-phase instruction may create `proof.txt` with exactly "
        "`stage-one:<proof-id>` and a trailing newline; a later command in that "
        "initial run may update it to `stage-two:<proof-id>`. The recurring phase "
        "starts a new child owner in this same workspace after the original run is "
        "stopped: it must verify and preserve stage two and must never reset "
        "proof.txt to stage one. In a requested control phase, write only the named "
        "phase-readiness marker before its bounded local delay. The explicitly "
        "authorized recurring marker task remains ongoing until the controller is "
        "stopped; one bounded append completes only that turn's step, not the task. "
        "For each due continuation, verify proof.txt remains at stage two and append "
        "that turn's unique ID to `recurrence-proof.txt` if absent, preserving prior "
        "lines. Do not write the one-time session-history marker into files or create "
        "a duplicate schedule. This task is offline and has no OpenForage or network work.\n"
    ).replace("<proof-id>", proof_id)
    _write_exclusive(workspace / "AGENTS.md", agents)
    if claude_code:
        _write_exclusive(
            workspace / "CLAUDE.md",
            "# Inert Claude Code adapter fixture rules\n\n"
            "This temporary workspace is for bounded offline marker checks only. "
            "Do not use network, nested agents, external MCP, or any OpenForage "
            "registration, campaign, worker, search, evaluation, or submission "
            "operation. Preserve all inherited permission and authentication "
            "settings; do not change host configuration. Follow TASK.md and only "
            "write the fixture marker requested by the current bounded turn.\n",
        )
    _write_exclusive(workspace / "TASK.md", task)
    git = subprocess.run(
        ["git", "-C", str(workspace), "init", "--quiet"],
        capture_output=True,
        timeout=30,
        env=_isolated_git_env(),
    )
    if git.returncode != 0 or not (workspace / ".git").exists():
        raise ProofAbort("isolated_git_root_init_failed")
    top = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=30,
        env=_isolated_git_env(),
    )
    try:
        actual = Path(top.stdout.strip()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ProofAbort("isolated_git_root_unverified") from None
    if top.returncode != 0 or actual != workspace.resolve(strict=True):
        raise ProofAbort("isolated_git_root_unverified")
    return workspace / "TASK.md"


def _probe_version(harness: str, binary: str) -> str:
    try:
        result = observe_version(harness, binary, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise ProofAbort("harness_version_probe_failed") from None
    except AdapterError as exc:
        raise ProofAbort(exc.code) from None
    version = result.get("version")
    if not isinstance(version, str) or not version:
        raise ProofAbort("harness_version_unobserved")
    return version


def _probe_network_tools(trace: dict) -> list[str]:
    return sorted({
        item["tool"] for item in trace.get("native_tools", [])
        if isinstance(item, dict) and isinstance(item.get("tool"), str)
        and NETWORK_TOOL_RE.search(item["tool"])
    })


def _wait_native_process(proc: subprocess.Popen, identity: dict | None, timeout: float) -> tuple[int | None, bool, dict]:
    timed_out = False
    cleanup = {"identity_captured": identity is not None, "termination_attempted": False,
               "terminated": False, "killed": False, "process_group_alive_after": None}
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if identity and identity_alive(identity):
            result = terminate_identity_checked(identity, grace_seconds=3.0)
            cleanup.update({key: bool(result.get(key)) for key in ("attempted", "terminated", "killed")})
            cleanup["termination_attempted"] = bool(result.get("attempted"))
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if identity and identity_alive(identity):
                result = terminate_identity_checked(identity, grace_seconds=0.0)
                cleanup["termination_attempted"] = cleanup["termination_attempted"] or bool(result.get("attempted"))
                cleanup["terminated"] = cleanup["terminated"] or bool(result.get("terminated"))
                cleanup["killed"] = cleanup["killed"] or bool(result.get("killed"))
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    cleanup["process_group_alive_after"] = process_group_alive(identity)
    cleanup["process_group_status_after"] = process_group_observation(identity)
    cleanup["identity_status_after"] = process_identity_status(identity)
    return proc.returncode, timed_out, cleanup


def _run_parent(*, harness: str, binary: str, model: str, variant: str | None,
                reasoning_effort: str | None, workspace: Path, proof_id: str,
                nonce: str, raw_path: Path, timeout: float,
                effort: str | None = None,
                fresh_session_id: str | None = None) -> tuple[dict, dict]:
    prompt = (
        "This is the fresh native parent for a bounded, offline adapter proof. "
        "Do not use network, search, browser, external MCP, nested agents, or any "
        "OpenForage operation. Do not edit files. Return exactly this JSON object "
        "as your final message: "
        + json.dumps({"kind": "proof_parent_ack", "proof_id": proof_id, "nonce": nonce}, separators=(",", ":"))
    )
    argv = build_argv(
        harness=harness, binary=binary, model=model, workspace=workspace, prompt=prompt,
        variant=variant, reasoning_effort=reasoning_effort, effort=effort,
        fresh_session_id=fresh_session_id, profile="isolated",
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(raw_path, flags, 0o600)
    with os.fdopen(fd, "wb") as raw:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.Popen(
                argv, cwd=workspace, stdin=subprocess.DEVNULL, stdout=raw,
                stderr=subprocess.STDOUT, env=env, start_new_session=True, close_fds=True,
            )
        except OSError:
            raise ProofAbort("native_parent_spawn_failed") from None
        identity = process_identity(proc.pid)
        if not valid_process_identity(identity):
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            raise ProofAbort("native_parent_process_identity_unobserved")
        exit_code, timed_out, cleanup = _wait_native_process(proc, identity, timeout)
    raw_bytes = raw_path.read_bytes()
    trace = parse_trace(harness, raw_bytes.decode("utf-8", errors="replace"))
    ack = trace.get("final_ack") or {}
    passed = (
        not timed_out
        and exit_code == 0
        and not trace.get("identity_ambiguous")
        and bool(trace.get("session_id"))
        and ack.get("kind") == "proof_parent_ack"
        and ack.get("proof_id") == proof_id
        and ack.get("nonce") == nonce
        and (fresh_session_id is None or trace.get("session_id") == fresh_session_id)
        and cleanup.get("process_group_status_after") == "gone"
        and cleanup.get("identity_status_after") == "dead"
    )
    record = {
        "request_id": f"parent-{proof_id}",
        "turn_id": "parent-bootstrap",
        "fence": 0,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "session_fingerprint": _sha256(trace["session_id"]) if trace.get("session_id") else None,
        "final_ack_observed": bool(ack),
        "ack_fields_match": passed,
        "native_tools": trace.get("native_tools", []),
        "network_tool_names_observed": _probe_network_tools(trace),
        "native_event_count": trace.get("event_count", 0),
        "malformed_event_count": trace.get("malformed_event_count", 0),
        "raw_sha256": _sha256(raw_bytes),
        "raw_bytes": len(raw_bytes),
        "cleanup": cleanup,
    }
    return record, trace


def _invoke_control(argv: list[str], timeout: float = 60.0) -> tuple[int, dict]:
    try:
        result = subprocess.run(
            [sys.executable, str(BACKGROUND_CLI), *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProofAbort("controller_cli_call_failed_or_timed_out") from None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        try:
            data = json.loads(result.stderr)
        except (json.JSONDecodeError, TypeError):
            data = {}
    return result.returncode, data if isinstance(data, dict) else {}


def _wait_request(run, request_id: str, timeout: float) -> tuple[dict, dict | None]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        row = next((item for item in state.get("turns", []) if item.get("request_id") == request_id), None)
        if row is None:
            return state, None
        if row.get("status") in {"acknowledged", "uncertain", "failed", "cancelled_before_start"}:
            return state, row
        if not identity_alive(state.get("controller_identity")):
            return state, row
        time.sleep(POLL_SECONDS)
    with run.lock():
        state = load_state(run)
    return state, next((item for item in state.get("turns", []) if item.get("request_id") == request_id), None)


def _trace_for_request(run, harness: str, request_id: str) -> dict:
    path = run.dir / "raw" / f"{request_id}.jsonl"
    if path.is_symlink() or not path.is_file():
        raise ProofAbort("native_turn_raw_trace_missing")
    return parse_trace(harness, path.read_text(encoding="utf-8", errors="replace"))


def _submit_turn(run, *, harness: str, command: str, timeout: float) -> tuple[dict, dict, dict]:
    code, result = _invoke_control([
        "turn", "--state-dir", str(run.dir), "--command", command, "--wait-seconds", "0",
    ])
    request_id = result.get("submitted_request_id")
    if code != 0 or not isinstance(request_id, str):
        raise ProofAbort("native_turn_request_not_accepted")
    state, row = _wait_request(run, request_id, timeout)
    if not row or row.get("status") != "acknowledged":
        raise ProofAbort("native_turn_did_not_receive_valid_ack")
    trace = _trace_for_request(run, harness, request_id)
    failure, _ = validate_ack(
        trace, exit_code=row.get("exit_code"), kind="turn_ack", run_id=run.run_id,
        task_sha256=state["task_sha256"], turn_id=row["turn_id"], fence=row["fence"],
        target_session=state["child_session_identity"],
    )
    if failure:
        raise ProofAbort("native_turn_ack_revalidation_failed")
    if _probe_network_tools(trace):
        raise ProofAbort("network_tool_observed_in_offline_turn")
    return state, row, trace


def _wait_external_due_rows(run, count: int, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        rows = [item for item in state.get("turns", [])
                if item.get("delivery") == "external_due_time"]
        if len(rows) >= count and all(item.get("status") == "acknowledged" for item in rows[:count]):
            return rows[:count]
        if any(item.get("status") in {"uncertain", "failed", "cancelled_before_start"} for item in rows):
            raise ProofAbort("external_due_continuation_did_not_acknowledge")
        if process_identity_status(state.get("controller_identity")) != "alive":
            raise ProofAbort("external_due_controller_not_live")
        time.sleep(POLL_SECONDS)
    raise ProofAbort("external_due_continuation_timeout")


def _active_turn(run, *, command: str, timeout: float,
                 readiness_path: Path, readiness_value: str) -> tuple[str, dict, dict, dict]:
    with run.lock():
        initial = load_state(run)
    workspace = Path(initial["workspace"]).resolve(strict=True)
    if readiness_path.parent.resolve(strict=True) != workspace or readiness_path.exists() or readiness_path.is_symlink():
        raise ProofAbort("active_phase_readiness_path_invalid")
    expected_session = initial.get("child_session_identity")
    if not isinstance(expected_session, str) or not expected_session:
        raise ProofAbort("active_phase_expected_session_missing")
    code, result = _invoke_control([
        "turn", "--state-dir", str(run.dir), "--command", command, "--wait-seconds", "0",
    ])
    request_id = result.get("submitted_request_id")
    if code != 0 or not isinstance(request_id, str):
        raise ProofAbort("active_control_turn_not_accepted")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        active = state.get("active")
        identity = active.get("child_identity") if isinstance(active, dict) else None
        live_process = bool(
            active and active.get("request_id") == request_id
            and valid_process_identity(identity)
            and process_identity_status(identity) == "alive"
            and process_group_observation(identity) == "alive"
        )
        session_matches = state.get("child_session_identity") == expected_session
        raw_path = run.dir / "raw" / f"{request_id}.jsonl"
        trace = None
        if raw_path.is_file() and not raw_path.is_symlink():
            try:
                trace = parse_trace(state.get("harness"), raw_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, StoreError, AdapterError):
                trace = None
        trace_matches = bool(trace and not trace.get("identity_ambiguous")
                             and trace.get("session_id") == expected_session)
        marker_matches = False
        if readiness_path.is_file() and not readiness_path.is_symlink():
            try:
                marker_matches = readiness_path.read_text(encoding="utf-8") == readiness_value + "\n"
            except (OSError, UnicodeError):
                marker_matches = False
        if live_process and session_matches and trace_matches and marker_matches:
            return request_id, state, active, {
                "phase_marker_observed": True,
                "live_recorded_cli_identity": True,
                "expected_session_observed_in_live_trace": True,
                "tool_state_limitation": "native event output may omit the exact shell command/state; the marker and live exact-session trace establish phase readiness only",
            }
        row = next((item for item in state.get("turns", []) if item.get("request_id") == request_id), None)
        if row and row.get("status") in {"acknowledged", "uncertain", "failed", "cancelled_before_start"}:
            break
        if process_identity_status(state.get("controller_identity")) != "alive":
            break
        time.sleep(POLL_SECONDS)
    raise ProofAbort("active_phase_readiness_not_proven")


def _kill_controller_exact(run, timeout: float = 10.0) -> bool:
    with run.lock():
        state = load_state(run)
    identity = state.get("controller_identity")
    if not valid_process_identity(identity) or process_identity_status(identity) != "alive":
        return False
    current = process_identity(identity.get("pid"))
    if (not valid_process_identity(current)
            or any(current.get(key) != identity.get(key) for key in ("pid", "boot_id", "starttime", "pgid"))
            or process_group_observation(identity) != "alive"):
        return False
    os.kill(identity["pid"], signal.SIGKILL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_identity_status(identity) == "dead" and process_group_observation(identity) == "gone":
            return True
        time.sleep(0.05)
    return process_identity_status(identity) == "dead" and process_group_observation(identity) == "gone"


def _append_failure(summary: dict, code: str) -> None:
    failures = summary.setdefault("failures", [])
    if code not in failures:
        failures.append(code)


def _record_request_ack(
    summary: dict,
    key: str,
    *,
    request_id: str | None,
    turn_id: str | None,
    kind: str | None,
    fence: int | None,
    task_sha256_value: str | None,
    command_sha256: str | None,
    exit_code: int | None,
    trace: dict | None,
    target_session: str | None,
    ack_binding_matches: bool,
    related_session: str | None = None,
) -> None:
    """Retain causal request/ack identity without publishing transcripts or raw IDs."""
    trace = trace if isinstance(trace, dict) else {}
    ack = trace.get("final_ack")
    returned_session = trace.get("session_id")
    summary.setdefault("request_acknowledgements", {})[key] = {
        "adapter_id": summary.get("adapter_id"),
        "product_version": summary.get("harness_version"),
        "os": summary.get("os"),
        "profile": (summary.get("adapter_profile") or {}).get("selected")
        if isinstance(summary.get("adapter_profile"), dict) else None,
        "request_id": request_id,
        "turn_id": turn_id,
        "kind": kind,
        "fence": fence,
        "task_sha256": task_sha256_value,
        "command_sha256": command_sha256,
        "process_exit_code": exit_code,
        "native_result_event_observed": isinstance(ack, dict),
        "ack_binding_matches": bool(ack_binding_matches),
        "target_session_fingerprint": session_fingerprint(target_session),
        "related_session_fingerprint": session_fingerprint(related_session),
        "returned_session_fingerprint": session_fingerprint(returned_session),
    }


def _record_controller_turn_ack(
    summary: dict,
    key: str,
    *,
    run,
    state: dict,
    row: dict | None,
    trace: dict | None,
    target_session: str | None = None,
    parent_session: str | None = None,
) -> None:
    row = row if isinstance(row, dict) else {}
    trace = trace if isinstance(trace, dict) else {}
    ack_kind = "ownership_ack" if row.get("kind") == "ownership" else "turn_ack"
    failure = "request_or_trace_missing"
    if row and trace:
        failure, _ = validate_ack(
            trace,
            exit_code=row.get("exit_code"),
            kind=ack_kind,
            run_id=run.run_id,
            task_sha256=state.get("task_sha256", ""),
            turn_id=row.get("turn_id", ""),
            fence=row.get("fence"),
            parent_session=parent_session if row.get("kind") == "ownership" else None,
            target_session=target_session,
        )
    _record_request_ack(
        summary,
        key,
        request_id=row.get("request_id"),
        turn_id=row.get("turn_id"),
        kind=row.get("kind"),
        fence=row.get("fence") if type(row.get("fence")) is int else None,
        task_sha256_value=state.get("task_sha256"),
        command_sha256=row.get("command_sha256"),
        exit_code=row.get("exit_code") if type(row.get("exit_code")) is int else None,
        trace=trace,
        target_session=target_session,
        related_session=parent_session,
        ack_binding_matches=failure is None,
    )


def _verify_offline_trace(summary: dict, trace: dict, phase: str) -> None:
    names = _probe_network_tools(trace)
    summary.setdefault("network_tool_events", {})[phase] = names
    if names:
        _append_failure(summary, f"{phase}_network_tool_observed")


def _cleanup_run(run, workspace: Path | None, summary: dict) -> None:
    parent_cleanup = ((summary.get("parent") or {}).get("cleanup") or {})
    parent_safe = (
        not summary.get("native_parent_process_attempted")
        or (parent_cleanup.get("process_group_status_after") == "gone"
            and parent_cleanup.get("identity_status_after") == "dead")
    )
    safe_to_remove_workspace = parent_safe
    if not parent_safe:
        _append_failure(summary, "native_parent_process_group_cleanup_unverified")
    if run is not None:
        run_cleanup = {}
        try:
            state = load_state(run)
            if state.get("status") != "stopped":
                _invoke_control(["stop", "--state-dir", str(run.dir), "--wait-seconds", "5"], timeout=20)
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                state = load_state(run)
                if process_identity_status(state.get("controller_identity")) == "dead":
                    break
                time.sleep(0.1)
            state = load_state(run)
            active = state.get("active")
            child = active.get("child_identity") if active else None
            controller = state.get("controller_identity")
            last_child = next((item.get("child_process_identity") for item in reversed(state.get("turns", []))
                               if item.get("child_process_identity")), None)
            child_to_cleanup = child or last_child
            if valid_process_identity(child_to_cleanup) and identity_alive(child_to_cleanup):
                terminate_identity_checked(child_to_cleanup, grace_seconds=2.0)
            if valid_process_identity(controller) and identity_alive(controller):
                terminate_identity_checked(controller, grace_seconds=2.0)
            wait_deadline = time.monotonic() + 3.0
            while time.monotonic() < wait_deadline:
                if not (controller and identity_alive(controller)) and not (child_to_cleanup and identity_alive(child_to_cleanup)):
                    break
                time.sleep(0.05)
            state = load_state(run)
            child_group_states = [process_group_observation(item.get("child_process_identity"))
                                  for item in state.get("turns", [])
                                  if isinstance(item, dict) and item.get("child_process_identity")]
            controller_state = process_identity_status(state.get("controller_identity"))
            all_owned_gone = owned_processes_gone(state)
            run_cleanup = {
                "run_terminal": state.get("status") == "stopped",
                "controller_identity_status_after_cleanup": controller_state,
                "child_process_group_statuses_after_cleanup": child_group_states,
                "all_owned_process_groups_proven_gone": all_owned_gone,
            }
            if state.get("status") != "stopped" or not all_owned_gone:
                safe_to_remove_workspace = False
                _append_failure(summary, "owned_process_cleanup_incomplete")
        except Exception as exc:  # do not publish exception strings, paths, or transcripts
            run_cleanup = {"result": "error", "error_type": type(exc).__name__}
            safe_to_remove_workspace = False
            _append_failure(summary, "owned_process_cleanup_error")
        summary.setdefault("cleanup", {}).setdefault("runs", {})[run.run_id] = run_cleanup
    if workspace is not None and workspace.exists():
        try:
            run_results = summary.get("cleanup", {}).get("runs", {})
            if any(not item.get("run_terminal") or not item.get("all_owned_process_groups_proven_gone")
                   for item in run_results.values()):
                safe_to_remove_workspace = False
            if not safe_to_remove_workspace:
                raise ProofAbort("owned_processes_may_still_use_workspace")
            if workspace.is_symlink() or workspace.resolve(strict=True) == REPO_ROOT:
                raise ProofAbort("workspace_cleanup_path_refused")
            shutil.rmtree(workspace)
            summary.setdefault("cleanup", {})["isolated_workspace_removed"] = not workspace.exists()
        except Exception:
            summary.setdefault("cleanup", {})["isolated_workspace_removed"] = False
            _append_failure(summary, "isolated_workspace_cleanup_incomplete")


def run_proof(args: argparse.Namespace) -> tuple[int, dict, tuple[Path, object] | None]:
    validate_selector(args.harness, args.model, args.variant, args.reasoning_effort, args.effort)
    if not sys.platform.startswith("linux"):
        raise ProofAbort("linux_only")
    timeout = float(args.turn_timeout_seconds)
    if timeout < ACTIVE_DELAY_SECONDS + 10 or timeout > 1800:
        raise ProofAbort("turn_timeout_must_bound_active_pause_window")
    binary = _resolve_binary(args.harness, args.binary)
    version = _probe_version(args.harness, binary)
    proof_id, root, workspace, state_dir, parent_raw, reserved_output = _prepare_paths(args.output)
    output_path, output_handle = reserved_output
    summary: dict = {
        "schema_version": 2,
        "evidence_class": "bounded-native-background-agent-proof",
        "adapter_interface_version": 1,
        "adapter_id": args.harness,
        "proof_id": proof_id,
        "started_at": utc_now(),
        "harness": args.harness,
        "product": get_adapter(args.harness).product_name,
        "harness_version": version,
        "model": args.model,
        "variant": args.variant,
        "reasoning_effort": args.reasoning_effort,
        "effort": args.effort,
        "os": "linux",
        "scope": (
            "Fresh native parent exits before the detached external controller starts a distinct child. "
            "One native CLI process is active at a time. Active pause/stop and stale-controller recovery "
            "cover this bounded fixture only; no host reboot, terminal hangup, native scheduler delivery, global "
            "memory, OpenForage worker checkpoint/stop, registration, search, evaluation, or submission is proved. "
            "The proof explicitly uses the isolated adapter profile; it does not test each user's plugin/tool config."
        ),
        "native_scheduler": "unknown; the controller supports manual delivery or one explicit external interval owner",
        "native_goal_task_memory": {
            **native_operation_observations([], harness=args.harness),
            "source": "typed adapter-normalized tool events; no prose, feature flag, planning record, or external due event is native-operation evidence",
        },
        "native_primitive_observations": native_primitive_observations(
            args.harness,
            [],
            declarations=get_adapter(args.harness).declared_primitives,
            scope={
                "product": get_adapter(args.harness).product_name,
                "product_version": version,
                "os": "linux",
                "profile": "isolated",
                "proof_id": proof_id,
            },
        ),
        "session_history": {"status": "not_yet_observed", "scope": "exact native child session only"},
        "global_memory": "unknown",
        "openforage_worker": {"started_by_adapter": False, "checkpoint": "unknown", "stop": "unknown"},
        "network_policy": "offline task/rules; no network/search/browser/MCP calls requested; explicit tool events checked",
        "adapter_profile": {
            "selected": "isolated",
            "scope": {
                "opencode": (
                    "temporary fixture workspace only; the recorded OpenCode invocation includes --pure, so its effective runtime configuration is selected by that flag and is not asserted to match a normal inherited user profile; no persisted user configuration is edited"
                ),
                "codex": (
                    "temporary fixture workspace only; the recorded Codex invocation includes --ignore-user-config and -c features.goals=true with --sandbox workspace-write; these are effective runtime overrides, and no persisted user configuration is edited"
                ),
                "claude-code": (
                    "temporary fixture workspace only; the recorded Claude Code invocation adds no config or permission override, so user configuration, authentication, and native permissions are inherited for this run; no persisted user configuration is edited"
                ),
            }[args.harness],
            "limitation": "the recorded argv describes only this fixture invocation; it does not establish another user's effective profile or configuration behavior",
        },
        "proof_applicability": "Exact product version, OS, profile, adapter identity, and source hashes are required; a version change requires a fresh proof review.",
        "source_file_digests": _source_file_digests(),
        "request_acknowledgements": {},
        "failures": [],
    }
    run = None
    additional_runs = []
    recurrence_state_dir = None
    task_path = None
    child_session = None
    try:
        task_path = _isolated_workspace(
            workspace, proof_id, claude_code=args.harness == "claude-code",
        )
        memory_marker = "history-only-" + uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        summary["native_parent_process_attempted"] = True
        parent_fresh_session_id = str(uuid.uuid4()) if args.harness == "claude-code" else None
        parent, parent_trace = _run_parent(
            harness=args.harness, binary=binary, model=args.model, variant=args.variant,
            reasoning_effort=args.reasoning_effort, workspace=workspace, proof_id=proof_id,
            nonce=nonce, raw_path=parent_raw / "parent.jsonl", timeout=timeout,
            effort=args.effort, fresh_session_id=parent_fresh_session_id,
        )
        summary["parent"] = parent
        _record_request_ack(
            summary, "parent-bootstrap",
            request_id=parent.get("request_id"), turn_id=parent.get("turn_id"),
            kind="proof_parent_ack", fence=parent.get("fence"),
            task_sha256_value=None, command_sha256=None, exit_code=parent.get("exit_code"),
            trace=parent_trace, target_session=parent_fresh_session_id,
            ack_binding_matches=bool(parent.get("ack_fields_match")),
        )
        _verify_offline_trace(summary, parent_trace, "parent")
        if not parent.get("ack_fields_match"):
            _append_failure(summary, "native_parent_ack_or_exit_failed")
            raise ProofAbort("native_parent_ack_or_exit_failed")
        if parent.get("network_tool_names_observed"):
            _append_failure(summary, "network_tool_observed_in_offline_parent")
            raise ProofAbort("network_tool_observed_in_offline_parent")
        parent_session = parent_trace["session_id"]
        summary["parent_exited_before_child_start"] = True

        initial_command = (
            f"Remember the one-time session-history marker `{memory_marker}` without writing it to any file. "
            f"Read TASK.md and create proof.txt exactly as it directs for stage one of `{proof_id}`. "
            "If a documented native goal declaration is actually available, it may be used only for the "
            "one-time stage-two condition; do not create a recurring native schedule. If a documented native "
            "task-creation operation is actually available, create one task for this inert document and retain "
            "its exact returned identity for a separate later reread. Goal declaration, task creation, and a "
            "later durable reread are distinct operations; do not claim any operation from a plan, checklist, "
            "feature flag, help text, or final prose. "
            "Use no network, search, browser, external MCP, nested-agent, or OpenForage tool."
        )
        startup_code, startup = _invoke_control([
            "start", "--harness", args.harness, "--parent-session", parent_session,
            "--model", args.model,
            *( ["--variant", args.variant] if args.variant is not None else [] ),
            *( ["--reasoning-effort", args.reasoning_effort] if args.reasoning_effort is not None else [] ),
            *( ["--effort", args.effort] if args.effort is not None else [] ),
            "--workspace", str(workspace), "--task", str(task_path), "--state-dir", str(state_dir),
            "--command", initial_command, "--binary", binary,
            "--profile", "isolated",
            "--run-timeout-seconds", str(timeout), "--startup-timeout-seconds", str(timeout + 30),
        ], timeout=timeout + 60)
        if startup_code != 0 or startup.get("status") != "ready":
            _append_failure(summary, "child_ownership_acknowledgement_not_ready")
            raise ProofAbort("child_start_not_ready")
        run = validate_existing_run(str(state_dir))
        state = load_state(run)
        child_session = state.get("child_session_identity")
        summary["run_id"] = run.run_id
        summary["task_sha256"] = state["task_sha256"]
        binding = startup.get("ownership_ack_binding")
        if (
            not child_session
            or child_session == parent_session
            or binding != {
                "run_id": run.run_id,
                "session_id": child_session,
                "task_sha256": state["task_sha256"],
                "fence": 1,
                "ack_turn_id": "bootstrap",
            }
        ):
            _append_failure(summary, "child_ownership_binding_invalid")
            raise ProofAbort("child_ownership_binding_invalid")
        bootstrap_trace = _trace_for_request(run, args.harness, "bootstrap")
        requested_child_session = state.get("fresh_session_id_requested")
        expected_child_session = requested_child_session if args.harness == "claude-code" else None
        bootstrap_failure, _ = validate_ack(
            bootstrap_trace, exit_code=state["turns"][0].get("exit_code"), kind="ownership_ack",
            run_id=run.run_id, task_sha256=state["task_sha256"], turn_id="bootstrap", fence=1,
            parent_session=parent_session, target_session=expected_child_session,
        )
        bootstrap_row = next(
            (item for item in state.get("turns", []) if item.get("request_id") == "bootstrap"),
            {},
        )
        _record_controller_turn_ack(
            summary, "bootstrap", run=run, state=state, row=bootstrap_row,
            trace=bootstrap_trace, parent_session=parent_session,
            target_session=expected_child_session,
        )
        if bootstrap_failure:
            _append_failure(summary, "child_ownership_ack_revalidation_failed")
            raise ProofAbort("child_ownership_ack_revalidation_failed")
        _verify_offline_trace(summary, bootstrap_trace, "child_bootstrap")
        stage_one = (workspace / "proof.txt").read_text(encoding="utf-8") if (workspace / "proof.txt").is_file() else ""
        summary["child"] = {
            "ownership_acknowledgement": state.get("ownership_acknowledgement"),
            "ownership_binding_matches": True,
            "session_fingerprint": _sha256(child_session),
            "distinct_from_parent": child_session != parent_session,
            "bootstrap_marker_effect": stage_one == f"stage-one:{proof_id}\n",
            "native_tools": bootstrap_trace.get("native_tools", []),
        }
        summary["launcher"] = {
            "launcher_exit_code": startup_code,
            "returned_after_ownership_ack": True,
            "detached_controller_alive_after_launcher_exit": bool(startup.get("controller", {}).get("alive")),
            "child_session_recorded": bool(child_session),
        }
        if stage_one != f"stage-one:{proof_id}\n":
            _append_failure(summary, "bootstrap_marker_effect_missing")
            raise ProofAbort("bootstrap_marker_effect_missing")
        if not startup.get("controller", {}).get("alive"):
            _append_failure(summary, "detached_controller_not_live_after_launcher_exit")
            raise ProofAbort("detached_controller_not_live_after_launcher_exit")

        state, first_turn, first_trace = _submit_turn(
            run,
            harness=args.harness,
            command=(
                "Resume this exact child session. Re-read TASK.md and proof.txt; recall the one-time "
                "marker from your own session history only (do not search files for it), update the "
                "marker to stage two. If you created a native task, retrieve that exact task using a "
                "documented native read operation now; task creation is not proof of durable reread. "
                "Complete the native task only if you created it. Include "
                "an extra final JSON key `proof_marker_recalled` with the recalled marker. Stay offline."
            ),
            timeout=timeout + 10,
        )
        _record_controller_turn_ack(
            summary, "history-continuation", run=run, state=state,
            row=first_turn, trace=first_trace, target_session=child_session,
        )
        _verify_offline_trace(summary, first_trace, "history_continuation")
        final_ack = first_trace.get("final_ack") or {}
        stage_two = (workspace / "proof.txt").read_text(encoding="utf-8") if (workspace / "proof.txt").is_file() else ""
        memory_ok = final_ack.get("proof_marker_recalled") == memory_marker
        stage_two_ok = stage_two == f"stage-two:{proof_id}\n"
        marker_in_workspace = _workspace_contains_marker(workspace, memory_marker)
        summary["continuation"] = {
            "turn_acknowledged": first_turn.get("status") == "acknowledged",
            "same_child_session": first_turn.get("child_session_identity") == child_session,
            "history_marker_match": memory_ok,
            "history_marker_absent_from_workspace": marker_in_workspace is False,
            "durable_task_marker_stage_two": stage_two_ok,
            "task_status_reported_by_agent": first_turn.get("task_status"),
            "native_tools": first_turn.get("native_tools", []),
        }
        summary["session_history"] = {
            "status": "observed" if memory_ok else "unknown",
            "scope": "one-time marker recalled from the exact child session history; not global memory",
        }
        if not memory_ok:
            _append_failure(summary, "resumed_history_marker_not_recalled")
            raise ProofAbort("resumed_history_marker_not_recalled")
        if not stage_two_ok:
            _append_failure(summary, "resumed_durable_task_marker_missing")
            raise ProofAbort("resumed_durable_task_marker_missing")
        if marker_in_workspace is not False:
            _append_failure(summary, "history_marker_written_or_workspace_scan_unverified")
            raise ProofAbort("history_marker_written_or_workspace_scan_unverified")

        current_adapter = get_adapter(args.harness)
        summary["native_primitive_observations"] = aggregate_native_primitive_observations(
            state.get("native_primitive_observations", []),
            declarations=current_adapter.declared_primitives,
            adapter_id=args.harness,
            scope={
                "product": current_adapter.product_name,
                "product_version": version,
                "os": "linux",
                "profile": "isolated",
                "proof_id": proof_id,
            },
        )
        summary["native_goal_task_memory"] = {
            **legacy_group_projection(summary["native_primitive_observations"]),
            "source": "typed adapter-normalized native event outcomes; history recall, help text, feature flags, and prose are not native-operation evidence",
        }

        # Active pause: require the phase marker and exact live child session before control.
        pause_marker = f"pause-ready:{proof_id}:{uuid.uuid4().hex}"
        pause_marker_path = workspace / "proof-pause-ready.txt"
        pause_request, pause_state, pause_active, pause_readiness = _active_turn(
            run,
            command=(
                f"For this interruption probe, first create `proof-pause-ready.txt` with exactly `{pause_marker}` "
                f"and a trailing newline; only then run one local bounded shell delay `sleep {ACTIVE_DELAY_SECONDS}` "
                "before returning the normal JSON acknowledgement. Do not use network, search, browser, "
                "external MCP, or OpenForage tools. Do not modify proof.txt."
            ),
            timeout=timeout,
            readiness_path=pause_marker_path,
            readiness_value=pause_marker,
        )
        pause_identity = pause_active["child_identity"]
        pause_code, pause_result = _invoke_control([
            "pause", "--state-dir", str(run.dir), "--wait-seconds", "10",
        ], timeout=20)
        if pause_code != 0 or pause_result.get("status") != "paused":
            _append_failure(summary, "active_pause_not_observed")
            raise ProofAbort("active_pause_not_observed")
        with run.lock():
            after_pause_state = load_state(run)
        interrupted_row = next((item for item in after_pause_state["turns"] if item.get("request_id") == pause_request), None)
        try:
            pause_trace = _trace_for_request(run, args.harness, pause_request)
        except ProofAbort:
            pause_trace = {}
        _record_controller_turn_ack(
            summary, "active-pause-interrupted-turn", run=run,
            state=after_pause_state, row=interrupted_row, trace=pause_trace,
            target_session=child_session,
        )
        pause_group_alive = process_group_alive(pause_identity)
        pause_group_status = process_group_observation(pause_identity)
        summary["active_pause"] = {
            **pause_readiness,
            "controller_exit_not_required": True,
            "status": after_pause_state.get("status"),
            "turn_outcome": interrupted_row.get("status") if interrupted_row else "unknown",
            "fence_advanced": after_pause_state.get("fencing_token", 0) > pause_active.get("fence", 0),
            "process_group_alive_after_pause": pause_group_alive,
            "process_group_status_after_pause": pause_group_status,
            "termination": pause_result.get("agent_process_termination"),
            "application_worker_checkpoint": "unknown",
            "application_worker_stop": "unknown",
        }
        if interrupted_row is None or interrupted_row.get("status") != "uncertain":
            _append_failure(summary, "interrupted_pause_outcome_not_preserved_as_uncertain")
        if pause_group_status != "gone" or process_identity_status(pause_identity) != "dead":
            _append_failure(summary, "active_pause_left_child_process_group_live")
            raise ProofAbort("active_pause_left_child_process_group_live")
        wrong_code, _ = _invoke_control([
            "resume", "--state-dir", str(run.dir), "--command", "reconcile", "--session-id", "wrong-session",
            "--wait-seconds", "2",
        ], timeout=10)
        summary["active_pause"]["mismatched_target_refused"] = wrong_code != 0
        if wrong_code == 0:
            _append_failure(summary, "mismatched_resume_target_was_accepted")

        resume_code, resume_result = _invoke_control([
            "resume", "--state-dir", str(run.dir), "--session-id", child_session,
            "--command",
            (
                "Reconcile the interrupted local delay without repeating it. Re-read TASK.md and proof.txt, "
                "recall the one-time marker from your own session history only, keep proof.txt at stage two, "
                "and report the marker in extra JSON key `proof_marker_recalled`. Stay offline."
            ),
            "--wait-seconds", str(timeout + 10),
        ], timeout=timeout + 30)
        if resume_code != 0 or resume_result.get("status") != "ready":
            _append_failure(summary, "exact_child_resume_reconciliation_failed")
            raise ProofAbort("exact_child_resume_reconciliation_failed")
        with run.lock():
            after_resume = load_state(run)
        resume_request = resume_result.get("submitted_request_id")
        resume_row = next((item for item in after_resume["turns"] if item.get("request_id") == resume_request), None)
        resume_trace = _trace_for_request(run, args.harness, resume_request)
        _record_controller_turn_ack(
            summary, "pause-reconciliation", run=run, state=after_resume,
            row=resume_row, trace=resume_trace, target_session=child_session,
        )
        _verify_offline_trace(summary, resume_trace, "pause_reconciliation")
        resume_ack = resume_trace.get("final_ack") or {}
        resume_memory_ok = resume_ack.get("proof_marker_recalled") == memory_marker
        if (
            not resume_row or resume_row.get("status") != "acknowledged"
            or resume_row.get("child_session_identity") != child_session
            or not resume_memory_ok
            or after_resume.get("child_session_identity") != child_session
        ):
            _append_failure(summary, "resume_did_not_reconcile_same_child_history")
            raise ProofAbort("resume_did_not_reconcile_same_child_history")
        summary["resume"] = {
            "exact_recorded_child_target": True,
            "same_child_session_fingerprint": _sha256(child_session),
            "reconciliation_instruction_acknowledged": True,
            "history_marker_match": True,
            "new_fence": resume_row.get("fence"),
            "status": after_resume.get("status"),
        }

        # Kill the idle controller by its recorded identity; no child CLI may overlap recovery.
        with run.lock():
            before_crash = load_state(run)
        if before_crash.get("active") is not None:
            _append_failure(summary, "controller_crash_probe_not_idle")
            raise ProofAbort("controller_crash_probe_not_idle")
        if process_group_alive(resume_row.get("child_process_identity")):
            _append_failure(summary, "child_process_group_not_idle_before_controller_crash")
            raise ProofAbort("child_process_group_not_idle_before_controller_crash")
        controller_before = before_crash.get("controller_identity")
        no_child_overlap = not bool(before_crash.get("active")) and not process_group_alive(
            resume_row.get("child_process_identity")
        )
        killed = _kill_controller_exact(run)
        diagnosis = diagnose_run(run)
        summary["controller_crash"] = {
            "deliberately_killed_by_recorded_identity": killed,
            "no_child_cli_overlapped_recovery": no_child_overlap,
            "classification_after_kill": diagnosis.get("classification"),
            "controller_generation_before": before_crash.get("controller_generation"),
        }
        if not killed or not no_child_overlap or diagnosis.get("classification") != "stale_controller":
            _append_failure(summary, "stale_controller_crash_not_safely_observed")
            raise ProofAbort("stale_controller_crash_not_safely_observed")

        recover_code, recover_result = _invoke_control([
            "recover", "--state-dir", str(run.dir), "--session-id", child_session,
            "--command",
            (
                "Reconcile the detached-controller interruption. Continue only this exact child task; "
                "re-read TASK.md and proof.txt, recall the one-time marker from your own native session "
                "history, do not repeat completed work, and return it in extra JSON key "
                "`proof_marker_recalled`. Stay offline."
            ),
            "--wait-seconds", str(timeout + 10),
        ], timeout=timeout + 30)
        if recover_code != 0 or recover_result.get("status") != "ready":
            _append_failure(summary, "stale_controller_recovery_failed")
            raise ProofAbort("stale_controller_recovery_failed")
        with run.lock():
            recovered_state = load_state(run)
        recovery_request = recover_result.get("submitted_request_id")
        recovery_row = next((item for item in recovered_state["turns"] if item.get("request_id") == recovery_request), None)
        recovery_trace = _trace_for_request(run, args.harness, recovery_request)
        _record_controller_turn_ack(
            summary, "stale-controller-reconciliation", run=run, state=recovered_state,
            row=recovery_row, trace=recovery_trace, target_session=child_session,
        )
        _verify_offline_trace(summary, recovery_trace, "stale_controller_reconciliation")
        recovery_ack = recovery_trace.get("final_ack") or {}
        recovery_memory_ok = recovery_ack.get("proof_marker_recalled") == memory_marker
        controller_after = recovered_state.get("controller_identity")
        summary["recovery"] = {
            "same_child_session": recovered_state.get("child_session_identity") == child_session,
            "history_marker_match": recovery_memory_ok,
            "reconciliation_acknowledged": bool(recovery_row and recovery_row.get("status") == "acknowledged"),
            "fence_advanced": recovered_state.get("fencing_token", 0) > before_crash.get("fencing_token", 0),
            "controller_generation_advanced": recovered_state.get("controller_generation", 0) > before_crash.get("controller_generation", 0),
            "new_controller_identity": bool(controller_after and controller_before and controller_after.get("pid") != controller_before.get("pid")),
            "no_overlapping_child": not bool(recovered_state.get("active")) if recovery_row and recovery_row.get("status") == "acknowledged" else False,
        }
        if not all((
            summary["recovery"]["same_child_session"], recovery_memory_ok,
            summary["recovery"]["reconciliation_acknowledged"], summary["recovery"]["fence_advanced"],
            summary["recovery"]["controller_generation_advanced"], summary["recovery"]["no_overlapping_child"],
        )):
            _append_failure(summary, "recovery_did_not_reconcile_exact_child_under_new_fence")
            raise ProofAbort("recovery_did_not_reconcile_exact_child_under_new_fence")

        # Terminal stop against a separately marked, live exact-session control phase.
        stop_marker = f"stop-ready:{proof_id}:{uuid.uuid4().hex}"
        stop_marker_path = workspace / "proof-stop-ready.txt"
        stop_request, _, stop_active, stop_readiness = _active_turn(
            run,
            command=(
                f"For the terminal-stop probe, first create `proof-stop-ready.txt` with exactly `{stop_marker}` "
                f"and a trailing newline; only then run one local bounded shell delay `sleep {ACTIVE_DELAY_SECONDS}` "
                "before returning a normal acknowledgement. Do not use network, search, browser, external MCP, "
                "or OpenForage tools; do not edit proof.txt."
            ),
            timeout=timeout,
            readiness_path=stop_marker_path,
            readiness_value=stop_marker,
        )
        stop_identity = stop_active["child_identity"]
        stop_code, stop_result = _invoke_control([
            "stop", "--state-dir", str(run.dir), "--wait-seconds", "10",
        ], timeout=25)
        with run.lock():
            stopped_state = load_state(run)
        stopped_row = next((item for item in stopped_state["turns"] if item.get("request_id") == stop_request), None)
        try:
            stop_trace = _trace_for_request(run, args.harness, stop_request)
        except ProofAbort:
            stop_trace = {}
        _record_controller_turn_ack(
            summary, "active-terminal-stop-interrupted-turn", run=run,
            state=stopped_state, row=stopped_row, trace=stop_trace,
            target_session=child_session,
        )
        group_alive_after_stop = process_group_alive(stop_identity)
        group_status_after_stop = process_group_observation(stop_identity)
        summary["active_stop"] = {
            **stop_readiness,
            "stop_exit_code": stop_code,
            "terminal_status": stopped_state.get("status"),
            "turn_outcome": stopped_row.get("status") if stopped_row else "unknown",
            "process_group_alive_after_stop": group_alive_after_stop,
            "process_group_status_after_stop": group_status_after_stop,
            "termination": stop_result.get("agent_process_termination"),
            "application_worker_checkpoint": "unknown",
            "application_worker_stop": "unknown",
        }
        if (
            stop_code != 0 or stopped_state.get("status") != "stopped"
            or group_status_after_stop != "gone" or process_identity_status(stop_identity) != "dead"
        ):
            _append_failure(summary, "active_stop_not_terminal_or_process_group_cleanup_unverified")
            raise ProofAbort("active_stop_not_terminal_or_process_group_cleanup_unverified")
        for action, args2 in (
            ("turn", ["turn", "--state-dir", str(run.dir), "--command", "must-refuse", "--wait-seconds", "0"]),
            ("resume", ["resume", "--state-dir", str(run.dir), "--command", "must-refuse", "--wait-seconds", "1"]),
            ("recover", ["recover", "--state-dir", str(run.dir), "--command", "must-refuse", "--wait-seconds", "1"]),
        ):
            refusal_code, _ = _invoke_control(args2, timeout=10)
            if refusal_code == 0:
                _append_failure(summary, f"terminal_stop_allowed_{action}")
        summary["terminal_stop"] = {"turn_resume_recover_refused": not any(
            item in summary["failures"] for item in (
                "terminal_stop_allowed_turn", "terminal_stop_allowed_resume", "terminal_stop_allowed_recover",
            )
        )}
        final_status = public_status(run)
        summary["final_status"] = {
            "status": final_status.get("status"),
            "fencing_token": final_status.get("fencing_token"),
            "task_status_reported_by_agent": final_status.get("task_status"),
            "native_goal_task_memory": final_status.get("native_goal_task_memory"),
        }
        if not summary["terminal_stop"]["turn_resume_recover_refused"]:
            raise ProofAbort("terminal_stop_not_enforced")

        # A separate run proves launcher-exit-independent external due-time delivery.
        recurrence_state_dir = root / f"{proof_id}-recurrence"
        recurrence_command = (
            "Continue this explicitly authorized recurring task, which remains in progress until the "
            "controller is stopped; completing this one bounded append does not complete the task. "
            "Re-read TASK.md and verify proof.txt remains at stage two without changing or resetting it, "
            f"then append this turn's unique turn_id as one new line in recurrence-proof.txt if absent. "
            "Preserve existing lines and do not repeat a completed line. Local file-reading and "
            "file-writing tools are allowed only as needed to read TASK.md and perform this requested "
            "marker action. Do not use network access, search, a browser, external MCP, nested agents, "
            "or any OpenForage operation; do not create a second schedule."
        )
        recurrence_start_code, recurrence_start = _invoke_control([
            "start", "--harness", args.harness, "--parent-session", parent_session,
            "--model", args.model,
            *( ["--variant", args.variant] if args.variant is not None else [] ),
            *( ["--reasoning-effort", args.reasoning_effort] if args.reasoning_effort is not None else [] ),
            *( ["--effort", args.effort] if args.effort is not None else [] ),
            "--workspace", str(workspace), "--task", str(task_path),
            "--state-dir", str(recurrence_state_dir),
            "--command", (
                "Re-read TASK.md. You are a new child owner in the existing workspace: verify and preserve "
                "stage two and never reset proof.txt to stage one. The explicitly authorized recurring "
                "marker task remains in progress until controller stop; one due append is one bounded step, "
                "not completion. Await its due continuation."
            ),
            "--binary", binary, "--profile", "isolated",
            "--interval-seconds", str(RECURRENCE_INTERVAL_SECONDS),
            "--continuation-command", recurrence_command,
            "--run-timeout-seconds", str(timeout),
            "--startup-timeout-seconds", str(timeout + 30),
        ], timeout=timeout + 60)
        if recurrence_start_code != 0 or recurrence_start.get("status") != "ready":
            _append_failure(summary, "external_recurrence_start_not_ready")
            raise ProofAbort("external_recurrence_start_not_ready")
        launcher_returned_at = time.time()
        recurrence_run = validate_existing_run(str(recurrence_state_dir))
        additional_runs.append(recurrence_run)
        recurrence_initial = load_state(recurrence_run)
        recurrence_child = recurrence_initial.get("child_session_identity")
        recurrence_bootstrap_row = next(
            (item for item in recurrence_initial.get("turns", []) if item.get("request_id") == "bootstrap"),
            {},
        )
        recurrence_bootstrap_trace = _trace_for_request(recurrence_run, args.harness, "bootstrap")
        _record_controller_turn_ack(
            summary, "external-recurrence-bootstrap", run=recurrence_run,
            state=recurrence_initial, row=recurrence_bootstrap_row,
            trace=recurrence_bootstrap_trace, parent_session=parent_session,
        )
        duplicate_state_dir = root / f"{proof_id}-duplicate-owner"
        duplicate_code, duplicate_result = _invoke_control([
            "start", "--harness", args.harness, "--parent-session", parent_session,
            "--model", args.model,
            *( ["--variant", args.variant] if args.variant is not None else [] ),
            *( ["--reasoning-effort", args.reasoning_effort] if args.reasoning_effort is not None else [] ),
            *( ["--effort", args.effort] if args.effort is not None else [] ),
            "--workspace", str(workspace), "--task", str(task_path),
            "--state-dir", str(duplicate_state_dir),
            "--command", "Do not run: this duplicate-owner request must be refused before launch.",
            "--binary", binary, "--profile", "isolated",
            "--run-timeout-seconds", str(timeout),
            "--startup-timeout-seconds", str(timeout + 30),
        ], timeout=30)
        recurrence_after_duplicate = load_state(recurrence_run)
        duplicate_owner_refused = (
            duplicate_code != 0
            and duplicate_result.get("error") == "workspace_owned_by_existing_run"
            and not duplicate_state_dir.exists()
        )
        duplicate_owner_unchanged = (
            recurrence_after_duplicate.get("status") == recurrence_initial.get("status") == "ready"
            and recurrence_after_duplicate.get("controller_identity") == recurrence_initial.get("controller_identity")
            and recurrence_after_duplicate.get("child_session_identity") == recurrence_child
            and recurrence_after_duplicate.get("fencing_token") == recurrence_initial.get("fencing_token")
        )
        summary["duplicate_owner"] = {
            "refused": duplicate_owner_refused,
            "refusal_code": duplicate_result.get("error"),
            "same_canonical_workspace": duplicate_owner_refused,
            "no_child_started": not duplicate_state_dir.exists(),
            "original_owner_unchanged": duplicate_owner_unchanged,
        }
        if not duplicate_owner_refused or not duplicate_owner_unchanged:
            _append_failure(summary, "duplicate_workspace_owner_was_not_excluded")
        first_due = recurrence_initial.get("recurrence", {}).get("next_due_at")
        launcher_exited_before_due = (
            recurrence_start.get("scheduler", {}).get("mode") == "external_interval"
            and isinstance(first_due, (int, float)) and first_due > launcher_returned_at
            and bool(recurrence_start.get("controller", {}).get("alive"))
        )
        if not launcher_exited_before_due or not recurrence_child or recurrence_child == parent_session:
            _append_failure(summary, "external_recurrence_not_armed_after_launcher_exit")
            raise ProofAbort("external_recurrence_not_armed_after_launcher_exit")
        due_rows = _wait_external_due_rows(
            recurrence_run, 2,
            timeout=2 * timeout + 3 * RECURRENCE_INTERVAL_SECONDS + 15,
        )
        due_results = []
        for row in due_rows[:2]:
            trace = _trace_for_request(recurrence_run, args.harness, row["request_id"])
            failure, _ = validate_ack(
                trace, exit_code=row.get("exit_code"), kind="turn_ack", run_id=recurrence_run.run_id,
                task_sha256=recurrence_initial["task_sha256"], turn_id=row["turn_id"], fence=row["fence"],
                target_session=recurrence_child,
            )
            _record_controller_turn_ack(
                summary, f"external-due-{len(due_results) + 1}",
                run=recurrence_run, state=recurrence_initial, row=row,
                trace=trace, target_session=recurrence_child,
            )
            _verify_offline_trace(summary, trace, f"external_due_{len(due_results) + 1}")
            due_results.append({
                "acknowledged": row.get("status") == "acknowledged" and failure is None,
                "delivery": row.get("delivery"),
                "same_child_session": row.get("child_session_identity") == recurrence_child,
                "due_time_present": isinstance(row.get("due_at"), (int, float)),
                "native_session_match": trace.get("session_id") == recurrence_child,
                "task_status": row.get("task_status"),
            })
            if failure or _probe_network_tools(trace):
                _append_failure(summary, "external_due_ack_or_offline_validation_failed")
        recurrence_marker = workspace / "recurrence-proof.txt"
        marker_lines = recurrence_marker.read_text(encoding="utf-8").splitlines() if recurrence_marker.is_file() else []
        due_turn_ids = [row.get("turn_id") for row in due_rows[:2]]
        marker_effects_match = all(turn_id in marker_lines for turn_id in due_turn_ids)
        if not all(item.get("acknowledged") and item.get("delivery") == "external_due_time"
                   and item.get("same_child_session") and item.get("due_time_present")
                   and item.get("native_session_match") and item.get("task_status") == "in_progress"
                   for item in due_results) or not marker_effects_match:
            _append_failure(summary, "two_native_due_time_continuations_not_proven")
            raise ProofAbort("two_native_due_time_continuations_not_proven")

        pause_due_code, pause_due_result = _invoke_control([
            "pause", "--state-dir", str(recurrence_run.dir), "--wait-seconds", "10",
        ], timeout=20)
        with recurrence_run.lock():
            paused_recurrence_state = load_state(recurrence_run)
        paused_due_count = sum(item.get("delivery") == "external_due_time"
                               for item in paused_recurrence_state["turns"])
        pause_due_started = time.monotonic()
        time.sleep(RECURRENCE_INTERVAL_SECONDS + 1.0)
        with recurrence_run.lock():
            after_paused_window = load_state(recurrence_run)
        paused_window_seconds = time.monotonic() - pause_due_started
        pause_window_passed = (
            pause_due_code == 0
            and pause_due_result.get("scheduler", {}).get("state") == "paused"
            and pause_due_result.get("scheduler", {}).get("next_due_at") is None
            and sum(item.get("delivery") == "external_due_time" for item in after_paused_window["turns"])
            == paused_due_count
            and paused_window_seconds >= RECURRENCE_INTERVAL_SECONDS
        )
        if not pause_window_passed:
            _append_failure(summary, "paused_external_due_window_dispatched_work")

        due_resume_code, due_resume = _invoke_control([
            "resume", "--state-dir", str(recurrence_run.dir), "--session-id", recurrence_child,
            "--command", "Reconcile the paused due-time window; re-read TASK.md and recurrence-proof.txt, then continue only if still in progress.",
            "--wait-seconds", str(timeout + 10),
        ], timeout=timeout + 30)
        if (due_resume_code != 0 or due_resume.get("status") != "ready"
                or due_resume.get("scheduler", {}).get("state") != "scheduled"):
            _append_failure(summary, "recurrence_reconciliation_did_not_restore_authorized_schedule")
            raise ProofAbort("recurrence_reconciliation_did_not_restore_authorized_schedule")
        due_resume_state = load_state(recurrence_run)
        due_resume_request = due_resume.get("submitted_request_id")
        due_resume_row = next(
            (item for item in due_resume_state.get("turns", []) if item.get("request_id") == due_resume_request),
            None,
        )
        due_resume_trace = _trace_for_request(recurrence_run, args.harness, due_resume_request)
        _record_controller_turn_ack(
            summary, "external-recurrence-pause-reconciliation", run=recurrence_run,
            state=due_resume_state, row=due_resume_row, trace=due_resume_trace,
            target_session=recurrence_child,
        )
        before_terminal_stop = load_state(recurrence_run)
        due_count_before_stop = sum(item.get("delivery") == "external_due_time"
                                    for item in before_terminal_stop["turns"])
        due_stop_code, due_stop = _invoke_control([
            "stop", "--state-dir", str(recurrence_run.dir), "--wait-seconds", "10",
        ], timeout=25)
        with recurrence_run.lock():
            terminal_recurrence_state = load_state(recurrence_run)
        stop_window_started = time.monotonic()
        time.sleep(RECURRENCE_INTERVAL_SECONDS + 1.0)
        with recurrence_run.lock():
            after_stopped_window = load_state(recurrence_run)
        stopped_window_seconds = time.monotonic() - stop_window_started
        stop_window_passed = (
            due_stop_code == 0 and due_stop.get("status") == "stopped"
            and due_stop.get("scheduler", {}).get("state") == "cancelled"
            and due_stop.get("scheduler", {}).get("next_due_at") is None
            and sum(item.get("delivery") == "external_due_time" for item in after_stopped_window["turns"])
            == due_count_before_stop
            and stopped_window_seconds >= RECURRENCE_INTERVAL_SECONDS
            and owned_processes_gone(after_stopped_window)
        )
        summary["external_recurrence"] = {
            "mode": "explicit_external_interval_delivery",
            "interval_seconds": RECURRENCE_INTERVAL_SECONDS,
            "launcher_returned_before_first_due": launcher_exited_before_due,
            "child_session_distinct_from_parent": recurrence_child != parent_session,
            "acknowledged_due_continuations": due_results,
            "useful_marker_effects_match": marker_effects_match,
            "paused_window_seconds": round(paused_window_seconds, 3),
            "paused_window_prevented_new_dispatch": pause_window_passed,
            "reconciliation_restored_previously_authorized_schedule": due_resume_code == 0,
            "stopped_window_seconds": round(stopped_window_seconds, 3),
            "stopped_window_prevented_new_dispatch": stop_window_passed,
            "terminal_status": terminal_recurrence_state.get("status"),
            "all_owned_process_groups_proven_gone": owned_processes_gone(after_stopped_window),
            "scheduler_scope": "external controller due-time dispatch; no native scheduler claim",
        }
        if not stop_window_passed:
            _append_failure(summary, "stopped_external_due_window_or_process_cleanup_unproven")
            raise ProofAbort("stopped_external_due_window_or_process_cleanup_unproven")
    except ProofAbort as exc:
        if not summary.get("failures"):
            _append_failure(summary, exc.code)
    except Exception as exc:  # failures persist; no exception text or transcript is published
        _append_failure(summary, f"runner_error:{type(exc).__name__}")
    finally:
        if run is None and state_dir.exists():
            try:
                run = validate_existing_run(str(state_dir))
            except Exception:
                run = None
        if recurrence_state_dir is not None and recurrence_state_dir.exists():
            try:
                recurrence_run = validate_existing_run(str(recurrence_state_dir))
                if all(existing.run_id != recurrence_run.run_id for existing in additional_runs):
                    additional_runs.append(recurrence_run)
            except Exception:
                _append_failure(summary, "recurrence_run_cleanup_state_unavailable")
        for extra_run in reversed(additional_runs):
            _cleanup_run(extra_run, None, summary)
        _cleanup_run(run, workspace if workspace.exists() else None, summary)
        summary["ended_at"] = utc_now()
        summary["conformance"] = evaluate_proof_summary(summary, root=REPO_ROOT)
        external_edges = [
            edge.get("external_controller_status")
            for edge in summary["conformance"].get("edges", {}).values()
            if isinstance(edge, dict) and edge.get("external_controller_status") != "not_applicable"
        ]
        if not external_edges or any(status != "passed" for status in external_edges):
            _append_failure(summary, "external_controller_conformance_incomplete")
        summary["passed"] = not summary.get("failures")
        if summary.get("passed") is not True:
            summary["evidence_class"] = "failed-bounded-native-background-agent-proof"
        if validate_proof_v2(summary):
            _append_failure(summary, "proof_schema_invalid")
            summary["passed"] = False
            summary["evidence_class"] = "failed-bounded-native-background-agent-proof"
        try:
            output_handle.write(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
            output_handle.flush()
            os.fsync(output_handle.fileno())
        finally:
            output_handle.close()
    return (0 if summary["passed"] else 1), summary, (output_path, None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=("opencode", "codex", "claude-code"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--variant", help="required for OpenCode")
    parser.add_argument("--reasoning-effort", help="required for Codex")
    parser.add_argument("--effort", help="optional Claude Code effort: low, medium, high, xhigh, or max")
    parser.add_argument("--binary", help="select one local CLI executable")
    parser.add_argument("--output", help="new sanitized proof JSON path; existing files are refused")
    parser.add_argument("--turn-timeout-seconds", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_path = None
    try:
        code, summary, output = run_proof(args)
        if output:
            output_path = output[0]
        print(json.dumps({
            "passed": summary.get("passed"),
            "proof_id": summary.get("proof_id"),
            "harness": summary.get("harness"),
            "failures": summary.get("failures", []),
            "summary_written": bool(output_path),
        }, indent=2, sort_keys=True))
        return code
    except (ProofAbort, AdapterError, StoreError) as exc:
        print(json.dumps({"passed": False, "error": getattr(exc, "code", "proof_preflight_failed")}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
