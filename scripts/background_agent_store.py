"""Private run-state, path policy, fencing, and process ownership helpers.

This module deliberately reuses ``bridge_core.RunState`` and its Linux process
identity helpers. State files and raw provider streams stay in a private,
ignored or out-of-checkout run directory; ``public_status`` never returns them.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "examples" / "opencode-lifecycle"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from bridge_core import (  # noqa: E402
    RunState,
    _boot_id as _bridge_boot_id,
    process_identity,
    utc_now,
)
from background_agent_adapters import get_adapter
from background_adapters.native_evidence import (
    aggregate_native_primitive_observations,
    legacy_group_projection,
)

SCHEMA_VERSION = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
TERMINAL_STATES = {"stopped"}


class StoreError(RuntimeError):
    """A stable, local-safe state/path refusal code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _absolute(value: str | Path, label: str) -> Path:
    text = os.fspath(value)
    if not text or "\x00" in text:
        raise StoreError(f"{label}_path_invalid")
    try:
        path = Path(os.path.expanduser(text))
        if not path.is_absolute():
            path = Path.cwd() / path
        return Path(os.path.abspath(path))
    except (OSError, RuntimeError, ValueError):
        raise StoreError(f"{label}_path_invalid") from None


def _check_no_symlinks(path: Path, *, allow_missing_leaf: bool = False) -> None:
    chain = list(reversed((path, *path.parents)))
    for index, item in enumerate(chain):
        if item.is_symlink():
            raise StoreError("symlink_path_refused")
        if item.exists():
            continue
        if allow_missing_leaf and index == len(chain) - 1:
            continue
        raise StoreError("path_component_missing")


def _path_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_within(left, right) or _path_within(right, left)


def process_group_alive(identity: dict | None) -> bool:
    """True when the recorded group is observed present, including after exit."""
    return process_group_observation(identity) in {"alive", "present_unverified"}


def valid_process_identity(identity: object) -> bool:
    """Require every persisted field needed for conservative runtime ownership."""
    if not isinstance(identity, dict):
        return False
    pid, starttime, pgid, boot_id = (
        identity.get("pid"), identity.get("starttime"), identity.get("pgid"), identity.get("boot_id"),
    )
    if (type(pid) is not int or pid <= 0 or type(starttime) is not int or starttime < 0
            or type(pgid) is not int or pgid <= 0 or pgid != pid
            or not isinstance(boot_id, str)):
        return False
    try:
        return str(uuid.UUID(boot_id)) == boot_id.lower()
    except (ValueError, AttributeError):
        return False


def process_identity_status(identity: object) -> str:
    """Return alive/dead/unknown; incomplete records are never treated as dead."""
    if not valid_process_identity(identity):
        return "unknown"
    if identity["boot_id"] != _bridge_boot_id():
        return "dead"
    current = process_identity(identity["pid"])
    if not current or current.get("starttime") is None:
        try:
            os.kill(identity["pid"], 0)
        except ProcessLookupError:
            return "dead"
        except PermissionError:
            return "unknown"
        except OSError:
            return "unknown"
        return "unknown"
    if current.get("starttime") != identity["starttime"]:
        return "dead"
    if current.get("boot_id") != identity["boot_id"]:
        return "dead"
    try:
        if os.getpgid(identity["pid"]) != identity["pgid"]:
            return "unknown"
        if os.getsid(identity["pid"]) != identity["pgid"]:
            return "unknown"
    except OSError:
        return "unknown"
    return "alive"


def identity_alive(identity: dict | None) -> bool:
    """Runtime liveness requires the exact process and its recorded session group."""
    return process_identity_status(identity) == "alive"


def process_group_observation(identity: object) -> str:
    """Observe a group without signaling; distinguish absent from unknown records."""
    if not valid_process_identity(identity):
        return "unknown"
    identity_state = process_identity_status(identity)
    try:
        os.killpg(identity["pgid"], 0)
        return "alive" if identity_state == "alive" else "present_unverified"
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "present_unverified"
    except OSError:
        return "unknown"


def terminate_identity_checked(identity: dict | None, grace_seconds: float = 5.0) -> dict:
    """Signal only a complete identity whose live PID is still in its recorded session group."""
    result = {"attempted": False, "terminated": False, "killed": False, "reason": None}
    status = process_identity_status(identity)
    if status != "alive":
        result["reason"] = "identity_unknown_refused" if status == "unknown" else "identity_dead_refused"
        return result
    if process_group_observation(identity) != "alive":
        result["reason"] = "process_group_membership_unverified"
        return result
    result["attempted"] = True
    try:
        os.killpg(identity["pgid"], signal.SIGTERM)
        result["terminated"] = True
    except ProcessLookupError:
        result["reason"] = "process_group_already_gone"
        return result
    except OSError:
        result["reason"] = "process_group_sigterm_unavailable"
        return result
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        status = process_identity_status(identity)
        group = process_group_observation(identity)
        if status == "dead" and group == "gone":
            return result
        if status == "unknown" or group in {"unknown", "present_unverified"}:
            result["reason"] = "process_group_cleanup_unconfirmed"
            return result
        time.sleep(0.05)
    if process_identity_status(identity) != "alive" or process_group_observation(identity) != "alive":
        result["reason"] = "process_group_membership_unverified"
        return result
    try:
        os.killpg(identity["pgid"], signal.SIGKILL)
        result["killed"] = True
    except OSError:
        result["reason"] = "process_group_sigkill_unavailable"
    return result


def _is_git_ignored(path: Path) -> bool:
    try:
        relpath = path.relative_to(REPO_ROOT).as_posix()
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
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "--quiet", "--no-index", relpath],
            capture_output=True,
            timeout=10,
            env=env,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def validate_start_paths(workspace_value: str, task_value: str, state_value: str) -> tuple[Path, Path, Path]:
    """Validate three explicit, non-overlapping paths before any state write."""
    workspace = _absolute(workspace_value, "workspace")
    task = _absolute(task_value, "task")
    state_dir = _absolute(state_value, "state")
    _check_no_symlinks(workspace)
    if not workspace.is_dir():
        raise StoreError("workspace_not_directory")
    _check_no_symlinks(task)
    if not task.is_file() or task.is_symlink():
        raise StoreError("task_not_regular_file")
    if not _path_within(task, workspace):
        raise StoreError("task_must_be_inside_workspace")
    try:
        task_bytes = task.read_bytes()
        task_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        raise StoreError("task_not_readable_utf8") from None
    if not task_bytes:
        raise StoreError("task_file_empty")
    workspace = workspace.resolve(strict=True)
    task = task.resolve(strict=True)
    if task == workspace or _paths_overlap(workspace, state_dir):
        raise StoreError("workspace_state_paths_overlap")
    _check_no_symlinks(state_dir, allow_missing_leaf=True)
    state_dir = state_dir.resolve(strict=False)
    if _paths_overlap(workspace, state_dir):
        raise StoreError("workspace_state_paths_overlap")
    if state_dir.exists() or state_dir.is_symlink():
        raise StoreError("state_directory_collision")
    if not state_dir.parent.is_dir():
        raise StoreError("state_parent_not_directory")
    if _path_within(state_dir, REPO_ROOT) and not _is_git_ignored(state_dir):
        raise StoreError("state_directory_must_be_git_ignored")
    return workspace, task, state_dir


def validate_live_paths(workspace_value: str, task_value: str, state_value: str) -> tuple[Path, Path, Path]:
    """Recheck recorded workspace/task/state boundaries before every native turn."""
    workspace = _absolute(workspace_value, "workspace")
    task = _absolute(task_value, "task")
    state_dir = _absolute(state_value, "state")
    _check_no_symlinks(workspace)
    _check_no_symlinks(task)
    _check_no_symlinks(state_dir)
    if not workspace.is_dir() or not task.is_file() or not state_dir.is_dir():
        raise StoreError("live_run_path_missing_or_wrong_type")
    workspace = workspace.resolve(strict=True)
    task = task.resolve(strict=True)
    state_dir = state_dir.resolve(strict=True)
    if not _path_within(task, workspace) or _paths_overlap(workspace, state_dir):
        raise StoreError("live_run_path_escape_or_overlap")
    return workspace, task, state_dir


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or RUN_ID_RE.fullmatch(value) is None or value in {".", ".."}:
        raise StoreError("run_id_invalid")
    return value


def validate_parent_session(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise StoreError("parent_session_identity_invalid")
    if len(value) > 256 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise StoreError("parent_session_identity_invalid")
    return value


def make_run(state_dir: Path) -> RunState:
    run_id = validate_run_id(state_dir.name)
    return RunState(state_dir.parent, run_id)


def validate_existing_run(state_dir_value: str) -> RunState:
    state_dir = _absolute(state_dir_value, "state")
    _check_no_symlinks(state_dir)
    if not state_dir.is_dir():
        raise StoreError("state_directory_not_found")
    mode = stat.S_IMODE(state_dir.stat().st_mode)
    if mode & 0o077:
        raise StoreError("state_directory_permissions_too_open")
    run = make_run(state_dir)
    for path in (run.state_path, run.events_path, run.lock_path, run.dir / "owner.lock"):
        if path.is_symlink():
            raise StoreError("state_file_symlink_refused")
    if not run.state_path.is_file() or run.state_path.is_symlink():
        raise StoreError("state_file_missing")
    state = load_state(run)
    if state.get("run_id") != run.run_id:
        raise StoreError("run_id_state_mismatch")
    return run


def task_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise StoreError("task_file_unreadable") from None


def initialize_run(run: RunState, state: dict, bootstrap_request: dict) -> None:
    if run.dir.exists() or run.dir.is_symlink():
        raise StoreError("state_directory_collision")
    run.dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(run.dir, 0o700)
    (run.dir / "raw").mkdir(mode=0o700)
    (run.dir / "requests").mkdir(mode=0o700)
    request_id = bootstrap_request["request_id"]
    write_request(run, request_id, bootstrap_request)
    with run.lock():
        run.save(state)
        run.append_event({"at": utc_now(), "type": "run_created", "run_id": run.run_id,
                          "harness": state["harness"], "task_sha256": state["task_sha256"]})


def load_state(run: RunState) -> dict:
    try:
        state = run.load()
    except (OSError, ValueError, KeyError, TypeError):
        raise StoreError("state_json_invalid_or_missing") from None
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise StoreError("state_schema_unsupported")
    if state.get("run_id") != run.run_id:
        raise StoreError("run_id_state_mismatch")
    return state


def write_request(run: RunState, request_id: str, payload: dict) -> Path:
    if REQUEST_ID_RE.fullmatch(request_id) is None:
        raise StoreError("request_id_invalid")
    request_dir = run.dir / "requests"
    request_dir.mkdir(mode=0o700, exist_ok=True)
    if request_dir.is_symlink():
        raise StoreError("request_directory_symlink_refused")
    path = request_dir / f"{request_id}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise StoreError("request_file_collision") from None
    except OSError:
        raise StoreError("request_file_write_failed") from None
    return path


def read_request(run: RunState, request_id: str) -> dict:
    if REQUEST_ID_RE.fullmatch(request_id) is None:
        raise StoreError("request_id_invalid")
    path = run.dir / "requests" / f"{request_id}.json"
    if path.is_symlink() or not path.is_file():
        raise StoreError("request_file_missing_or_symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StoreError("request_file_invalid") from None
    if not isinstance(data, dict) or data.get("request_id") != request_id:
        raise StoreError("request_file_identity_mismatch")
    return data


def acquire_controller_lock(run: RunState) -> int:
    path = run.dir / "owner.lock"
    if path.is_symlink():
        raise StoreError("controller_lock_symlink_refused")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        raise StoreError("controller_owner_already_live") from None
    except OSError:
        raise StoreError("controller_lock_unavailable") from None


def prove_controller_lock_available(run: RunState) -> int:
    """Take the lifetime lock nonblocking to prove no owner; caller closes fd."""
    return acquire_controller_lock(run)


def open_raw_log(run: RunState, request_id: str) -> tuple[int, Path]:
    raw_dir = run.dir / "raw"
    raw_dir.mkdir(mode=0o700, exist_ok=True)
    if raw_dir.is_symlink():
        raise StoreError("raw_directory_symlink_refused")
    path = raw_dir / f"{request_id}.jsonl"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise StoreError("raw_log_collision") from None
    except OSError:
        raise StoreError("raw_log_create_failed") from None
    return fd, path


def _native_operation_evidence(state: dict, run_id: str) -> tuple[dict, dict]:
    """Return a typed per-operation view without matching tools by substring."""
    harness = state.get("harness")
    adapter = get_adapter(harness) if isinstance(harness, str) else None
    if adapter is None:
        typed = aggregate_native_primitive_observations(
            [], declarations=(), adapter_id="unknown",
            scope={"run_id": run_id, "product_version": None, "os": None, "profile": None},
        )
    else:
        typed = aggregate_native_primitive_observations(
            state.get("native_primitive_observations", []),
            declarations=adapter.declared_primitives,
            adapter_id=adapter.adapter_id,
            scope={
                "product": adapter.product_name,
                "product_version": state.get("harness_version"),
                "os": sys.platform,
                "profile": state.get("adapter_profile", "native"),
                "run_id": run_id,
            },
        )
    return legacy_group_projection(typed), typed


def public_status(run: RunState) -> dict:
    """Return a transcript-free, path-free status suitable for publication."""
    with run.lock():
        state = load_state(run)
    controller_identity_state = process_identity_status(state.get("controller_identity"))
    controller_alive = controller_identity_state == "alive"
    active = state.get("active")
    child_identity = active.get("child_identity") if isinstance(active, dict) else None
    child_alive = identity_alive(child_identity) if child_identity else False
    child_group_status = process_group_observation(child_identity) if child_identity else "unknown"
    turns = state.get("turns", [])
    last = turns[-1] if turns else None
    last_result = None
    if isinstance(last, dict):
        last_result = {
            "turn_id": last.get("turn_id"),
            "status": last.get("status"),
            "task_status": last.get("task_status"),
            "acknowledgement_observed": last.get("acknowledgement_observed", False),
            "exit_code": last.get("exit_code"),
            "failure_code": last.get("failure_code"),
            "native_tools": last.get("native_tools", []),
            "child_process_group_alive_after_exit": last.get("child_process_group_alive_after_exit", False),
            "child_process_group_status_after_exit": last.get("child_process_group_status_after_exit", "unknown"),
        }
    native_tools = state.get("native_tool_observations", [])
    native_operations, typed_native_operations = _native_operation_evidence(state, run.run_id)
    last_process_row = next((item for item in reversed(turns)
                             if isinstance(item, dict) and item.get("child_process_identity")), None)
    last_identity = last_process_row.get("child_process_identity") if last_process_row else None
    last_group_status = process_group_observation(last_identity) if last_identity else "unknown"
    recurrence = state.get("recurrence") if isinstance(state.get("recurrence"), dict) else {}
    next_due = recurrence.get("next_due_at")
    try:
        next_due_text = datetime.fromtimestamp(float(next_due), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        next_due_text = None
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.run_id,
        "status": state.get("status"),
        "harness": state.get("harness"),
        "model": state.get("model"),
        "variant": state.get("variant"),
        "reasoning_effort": state.get("reasoning_effort"),
        "effort": state.get("effort"),
        "parent_session_identity": state.get("parent_session_identity"),
        "child_session_identity": state.get("child_session_identity"),
        "candidate_child_session_identity": state.get("candidate_child_session_identity"),
        "parent_child_distinct": bool(
            state.get("child_session_identity")
            and state.get("child_session_identity") != state.get("parent_session_identity")
        ),
        "ownership_acknowledgement": state.get("ownership_acknowledgement", "unknown"),
        "ownership_reconciled": bool(state.get("ownership_reconciled")),
        "task_sha256": state.get("task_sha256"),
        "fencing_token": state.get("fencing_token"),
        "controller": {
            "alive": controller_alive,
            "identity_status": controller_identity_state,
            "process_group_status": process_group_observation(state.get("controller_identity")),
            "generation": state.get("controller_generation"),
        },
        "active": ({
            "turn_id": active.get("turn_id"),
            "request_id": active.get("request_id"),
            "fence": active.get("fence"),
            "child_process_alive": child_alive,
            "child_process_identity_status": process_identity_status(child_identity),
            "child_process_group_alive": child_group_status in {"alive", "present_unverified"},
            "child_process_group_status": child_group_status,
            "cancel_requested": active.get("cancel_requested"),
        } if active else None),
        "activity": "turn_running" if active else ("idle" if state.get("status") == "ready" else state.get("status")),
        "reconciliation_required": bool(state.get("reconciliation_required")),
        "ownership_ack_binding": state.get("ownership_ack_binding"),
        "task_status": state.get("task_status", "unknown"),
        "last_turn": last_result,
        "last_child_process_group": {
            "status": last_group_status,
            "alive_after_exit": bool(last_process_row and last_process_row.get("child_process_group_alive_after_exit")),
        },
        "native_goal_task_memory": {
            **native_operations,
            "source": "typed adapter-normalized tool events; prose, feature flags, and external due delivery are not native-operation evidence",
        },
        "native_primitive_observations": typed_native_operations,
        "agent_process_lifecycle": "external CLI process identities and groups only",
        "openforage_worker": {
            "started_by_supervisor": False,
            "checkpoint": "unknown",
            "worker_stop": "unknown",
        },
        "scheduler": {
            "mode": recurrence.get("mode", "manual"),
            "owner": "external_controller" if recurrence.get("mode") == "external_interval" else "manual_request_delivery",
            "state": recurrence.get("state", "manual"),
            "next_due_at": next_due_text,
            "block_reason": recurrence.get("block_reason"),
            "interval_seconds": recurrence.get("interval_seconds"),
            "scheduled_count": recurrence.get("scheduled_count", 0),
        },
        "failure_code": state.get("failure_code"),
        "updated_at": state.get("updated_at"),
    }


def diagnose_run(run: RunState) -> dict:
    with run.lock():
        state = load_state(run)
    controller_identity_state = process_identity_status(state.get("controller_identity"))
    controller_alive = controller_identity_state == "alive"
    controller_group_status = process_group_observation(state.get("controller_identity"))
    active = state.get("active")
    active_identity = active.get("child_identity") if isinstance(active, dict) else None
    child_identity_state = process_identity_status(active_identity) if active else "unknown"
    child_alive = child_identity_state == "alive"
    child_group_status = process_group_observation(active_identity) if active else "unknown"
    child_group_alive = child_group_status in {"alive", "present_unverified"}
    last_child_identity = next((item.get("child_process_identity") for item in reversed(state.get("turns", []))
                                if item.get("child_process_identity")), None)
    last_child_group_status = process_group_observation(last_child_identity) if last_child_identity else "unknown"
    last_child_group_alive = last_child_group_status in {"alive", "present_unverified"}
    status = state.get("status")
    if status == "stopped" and (controller_identity_state != "dead" or last_child_group_status != "gone"):
        classification = "stopped_cleanup_unconfirmed"
    elif status == "stopped":
        classification = "stopped_terminal"
    elif controller_identity_state == "unknown" or (active and child_identity_state == "unknown"):
        classification = "ownership_identity_unknown"
    elif controller_alive and child_alive:
        classification = "controller_and_agent_cli_live"
    elif controller_alive and active:
        classification = "controller_live_child_not_observed"
    elif active and child_alive:
        classification = "stale_controller_child_cli_live"
    elif not controller_alive and last_child_group_alive:
        classification = "stale_controller_child_group_live"
    elif active:
        classification = "crashed_stale_active"
    elif status == "paused":
        classification = "paused"
    elif status == "needs_reconciliation":
        classification = "needs_reconciliation"
    elif status in {"starting", "recovering"} and not controller_alive:
        classification = "stale_controller"
    elif status == "ready" and not controller_alive:
        classification = "stale_controller"
    elif status == "failed":
        classification = "failed"
    else:
        classification = status or "unknown"
    return {
        "run_id": run.run_id,
        "status": status,
        "classification": classification,
        "controller_alive": controller_alive,
        "controller_identity_status": controller_identity_state,
        "controller_process_group_status": controller_group_status,
        "active_child_cli_alive": child_alive,
        "active_child_identity_status": child_identity_state,
        "active_child_process_group_alive": child_group_alive,
        "active_child_process_group_status": child_group_status,
        "last_child_process_group_alive": last_child_group_alive,
        "last_child_process_group_status": last_child_group_status,
        "active_turn_id": active.get("turn_id") if isinstance(active, dict) else None,
        "fencing_token": state.get("fencing_token"),
        "reconciliation_required": bool(state.get("reconciliation_required")),
        "failure_code": state.get("failure_code"),
        "controller_identity_recorded": isinstance(state.get("controller_identity"), dict),
        "child_identity_recorded": isinstance(active.get("child_identity"), dict) if isinstance(active, dict) else False,
    }
