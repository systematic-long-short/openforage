#!/usr/bin/env python3
"""Linux-only external supervisor for a durable task in OpenCode, Codex, or Claude Code.

The selected harness remains responsible for reasoning, native goals/tasks,
memory, and session continuation. This CLI is process and request plumbing; an
explicit interval adds only due-time delivery of a caller-authorized bounded
continuation. It does not register, run, or control an OpenForage worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

from background_agent_adapters import AdapterError, get_adapter, validate_selector
from background_agent_ownership import (
    adopt_workspace_for_recovery,
    claim_new_workspace,
    ensure_workspace_available,
    locked_workspace,
    owned_processes_gone,
    release_stopped_workspace,
    require_workspace_owner,
    update_workspace_owner,
)
from background_agent_store import (
    StoreError,
    TERMINAL_STATES,
    diagnose_run,
    identity_alive,
    initialize_run,
    load_state,
    make_run,
    process_group_observation,
    process_identity_status,
    process_identity,
    prove_controller_lock_available,
    public_status,
    task_sha256,
    terminate_identity_checked,
    utc_now,
    validate_existing_run,
    validate_parent_session,
    validate_run_id,
    validate_start_paths,
    valid_process_identity,
    write_request,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
SERVICE_LOG_PREFIX = "controller"
MAX_RUN_SECONDS = 60 * 60
MAX_WAIT_SECONDS = 2 * 60 * 60
MAX_INTERVAL_SECONDS = 7 * 24 * 60 * 60


class AgentError(RuntimeError):
    """A stable, safe CLI refusal code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require_linux() -> None:
    if not sys.platform.startswith("linux"):
        raise AgentError("linux_only")


def _positive_seconds(value: float, name: str, *, allow_zero: bool = False,
                      maximum: float = MAX_WAIT_SECONDS) -> float:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero) or value > maximum:
        raise AgentError(f"{name}_out_of_range")
    return value


def _resolve_binary(harness: str, selected: str | None) -> str:
    try:
        name = get_adapter(harness).executable_name
    except AdapterError as exc:
        raise AgentError(exc.code) from None
    candidates: list[Path] = []
    if selected:
        path = Path(os.path.expanduser(selected))
        if path.is_absolute() or os.sep in selected:
            try:
                path = path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                raise AgentError("cli_binary_not_found") from None
            candidates = [path]
        else:
            for item in os.environ.get("PATH", "").split(os.pathsep):
                if not item:
                    continue
                candidate = Path(item) / selected
                try:
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        candidates.append(candidate.resolve(strict=True))
                except (OSError, RuntimeError, ValueError):
                    continue
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) > 1:
                raise AgentError("ambiguous_cli_binary_on_path")
    else:
        for item in os.environ.get("PATH", "").split(os.pathsep):
            if not item:
                continue
            candidate = Path(item) / name
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    candidates.append(candidate.resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                continue
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) > 1:
            raise AgentError("ambiguous_cli_binary_on_path")
    if len(candidates) != 1:
        raise AgentError("cli_binary_not_found")
    binary = candidates[0]
    try:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise AgentError("cli_binary_not_executable")
        if binary.stat().st_mode & stat.S_IXUSR == 0 and not os.access(binary, os.X_OK):
            raise AgentError("cli_binary_not_executable")
    except OSError:
        raise AgentError("cli_binary_not_executable") from None
    return str(binary)


def _public_json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True))


def _public_error(code: str) -> None:
    print(json.dumps({"ok": False, "error": code}, sort_keys=True), file=sys.stderr)


def _service_argv(run) -> list[str]:
    return [sys.executable, str(SCRIPT_PATH), "__service", "--state-dir", str(run.dir)]


def _spawn_service_locked(run, state: dict) -> dict:
    """Start a detached controller while its caller holds run.lock()."""
    generation = int(state.get("controller_generation", 0)) + 1
    raw_dir = run.dir / "raw"
    raw_dir.mkdir(mode=0o700, exist_ok=True)
    if raw_dir.is_symlink():
        raise StoreError("raw_directory_symlink_refused")
    log_path = raw_dir / f"{SERVICE_LOG_PREFIX}-{generation:03d}.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(log_path, flags, 0o600)
    except FileExistsError:
        raise StoreError("controller_log_collision") from None
    except OSError:
        raise StoreError("controller_log_create_failed") from None
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        with os.fdopen(fd, "wb") as log_stream:
            proc = subprocess.Popen(
                _service_argv(run),
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
    except OSError:
        raise StoreError("controller_spawn_failed") from None
    identity = None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        identity = process_identity(proc.pid)
        if identity and identity.get("starttime") is not None:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.01)
    if not valid_process_identity(identity):
        raise StoreError("controller_identity_unavailable")
    state["controller_identity"] = identity
    state["controller_generation"] = generation
    state["controller_started_at"] = utc_now()
    state["controller_log_name"] = log_path.name
    run.save(state)
    run.append_event({"at": utc_now(), "type": "controller_spawned", "controller_generation": generation})
    return identity


def _start(args: argparse.Namespace) -> tuple[int, dict]:
    _require_linux()
    try:
        validate_selector(args.harness, args.model, args.variant, args.reasoning_effort, args.effort)
    except AdapterError as exc:
        raise AgentError(exc.code) from None
    parent_session = validate_parent_session(args.parent_session)
    if not args.command.strip():
        raise AgentError("explicit_start_command_required")
    workspace, task_path, state_dir = validate_start_paths(args.workspace, args.task, args.state_dir)
    validate_run_id(state_dir.name)
    binary = _resolve_binary(args.harness, args.binary)
    run_timeout = _positive_seconds(args.run_timeout_seconds, "run_timeout_seconds", maximum=MAX_RUN_SECONDS)
    startup_timeout = _positive_seconds(args.startup_timeout_seconds, "startup_timeout_seconds")
    interval = None
    if args.interval_seconds is not None:
        interval = _positive_seconds(args.interval_seconds, "interval_seconds", maximum=MAX_INTERVAL_SECONDS)
        if interval < 1:
            raise AgentError("interval_seconds_below_one")
        if not args.continuation_command or not args.continuation_command.strip():
            raise AgentError("recurrence_continuation_command_required")
    elif args.continuation_command is not None:
        raise AgentError("continuation_command_requires_interval")
    task_hash = task_sha256(task_path)
    run = make_run(state_dir)
    request_id = "bootstrap"
    turn = {
        "request_id": request_id,
        "turn_id": "bootstrap",
        "kind": "ownership",
        "status": "queued",
        "command_sha256": hashlib.sha256(args.command.encode("utf-8")).hexdigest(),
        "submitted_at": utc_now(),
    }
    state = {
        "schema_version": 1,
        "run_id": run.run_id,
        "created_at": utc_now(),
        "status": "starting",
        "harness": args.harness,
        "adapter_profile": args.profile,
        "binary": binary,
        "model": args.model,
        "variant": args.variant,
        "reasoning_effort": args.reasoning_effort,
        "effort": args.effort,
        "fresh_session_id_requested": str(uuid.uuid4()) if args.harness == "claude-code" else None,
        "workspace": str(workspace),
        "task_path": str(task_path),
        "task_name": task_path.name,
        "task_sha256": task_hash,
        "parent_session_identity": parent_session,
        "child_session_identity": None,
        "candidate_child_session_identity": None,
        "ownership_acknowledgement": "pending",
        "ownership_reconciled": False,
        "task_status": "unknown",
        "fencing_token": 1,
        "controller_identity": None,
        "controller_generation": 0,
        "active": None,
        "turns": [turn],
        "startup_request_id": request_id,
        "recovery_request_id": None,
        "run_timeout_seconds": run_timeout,
        "recurrence": {
            "mode": "external_interval" if interval is not None else "manual",
            "authorized": interval is not None,
            "interval_seconds": interval,
            "continuation_command": args.continuation_command,
            "state": "awaiting_bootstrap_ack" if interval is not None else "manual",
            "next_due_at": None,
            "remaining_seconds": None,
            "block_reason": None,
            "scheduled_count": 0,
            "last_request_id": None,
        },
        "reconciliation_required": False,
        "native_tool_observations": [],
        "native_primitive_observations": [],
        "failure_code": None,
        "terminal_at": None,
        "worker_semantics": {
            "started_by_supervisor": False,
            "checkpoint": "unknown",
            "stop": "unknown",
        },
    }
    request = {
        "request_id": request_id,
        "turn_id": "bootstrap",
        "kind": "ownership",
        "command": args.command,
    }
    with locked_workspace(workspace) as guard:
        ensure_workspace_available(guard)
        initialize_run(run, state, request)
        spawn_error = None
        with run.lock():
            locked_state = load_state(run)
            claim_new_workspace(guard, run, locked_state)
            try:
                _spawn_service_locked(run, locked_state)
            except (StoreError, OSError) as exc:
                code = exc.code if isinstance(exc, StoreError) else "controller_spawn_failed"
                failed = load_state(run)
                failed["status"] = "failed"
                failed["failure_code"] = code
                failed["ownership_acknowledgement"] = "not_observed"
                run.save(failed)
                update_workspace_owner(guard, run, failed)
                spawn_error = code
        if spawn_error is not None:
            return 1, public_status(run)
        with run.lock():
            spawned_state = load_state(run)
        update_workspace_owner(guard, run, spawned_state)
    result = _wait_for_start(run, startup_timeout)
    status = public_status(run)
    if status["status"] == "ready" and status["controller"]["alive"] and status["child_session_identity"]:
        return 0, status
    if result == "timeout":
        status["launch_wait"] = "timed_out_before_ownership_acknowledgement"
        return 2, status
    return 1, status


def _wait_for_start(run, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while True:
        with run.lock():
            state = load_state(run)
        status = state.get("status")
        if status in {"ready", "paused", "stopped", "needs_reconciliation", "failed"}:
            return status
        if not identity_alive(state.get("controller_identity")):
            return "stale_controller"
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(0.1)


def _await_turn(run, request_id: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while True:
        with run.lock():
            state = load_state(run)
        row = next((item for item in state.get("turns", []) if item.get("request_id") == request_id), None)
        if row is None:
            return "missing"
        if row.get("status") in {"acknowledged", "uncertain", "failed", "cancelled_before_start"}:
            return str(row.get("status"))
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(0.1)


def _child_groups_gone(state: dict) -> bool:
    active = state.get("active")
    if isinstance(active, dict):
        identity = active.get("child_identity")
        if active.get("process_started") and not valid_process_identity(identity):
            return False
        if identity is not None and process_group_observation(identity) != "gone":
            return False
    turns = state.get("turns", [])
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn, dict):
            return False
        identity = turn.get("child_process_identity")
        if turn.get("process_started") is True and not valid_process_identity(identity):
            return False
        if identity is not None and process_group_observation(identity) != "gone":
            return False
    return True


def _identity_and_group_gone(identity: object) -> bool:
    return (valid_process_identity(identity)
            and process_identity_status(identity) == "dead"
            and process_group_observation(identity) == "gone")


def _orphan_active_cleanup_proven(state: dict) -> bool:
    """Require exact dead identities and absent groups before terminal active cleanup."""
    active = state.get("active")
    controller = state.get("controller_identity")
    generation = state.get("controller_generation")
    if (state.get("status") != "stopped" or not isinstance(active, dict)
            or type(generation) is not int or generation < 1
            or not _identity_and_group_gone(controller)):
        return False
    active_controller = active.get("controller_identity")
    if not valid_process_identity(active_controller) or active_controller != controller:
        return False
    request_id, turn_id, fence = active.get("request_id"), active.get("turn_id"), active.get("fence")
    process_started = active.get("process_started")
    child_identity = active.get("child_identity")
    if (not isinstance(request_id, str) or not request_id
            or not isinstance(turn_id, str) or not turn_id
            or type(fence) is not int or fence < 0
            or type(process_started) is not bool
            or (process_started and not valid_process_identity(child_identity))
            or (child_identity is not None and not _identity_and_group_gone(child_identity))):
        return False

    turns = state.get("turns")
    if not isinstance(turns, list):
        return False
    matches = []
    for turn in turns:
        if not isinstance(turn, dict):
            return False
        if "process_started" in turn and type(turn["process_started"]) is not bool:
            return False
        recorded_identity = turn.get("child_process_identity")
        if turn.get("process_started") is True and not valid_process_identity(recorded_identity):
            return False
        if (recorded_identity is not None
                and not _identity_and_group_gone(recorded_identity)):
            return False
        if turn.get("request_id") == request_id:
            matches.append(turn)
        elif turn.get("status") == "running":
            return False
    if len(matches) != 1:
        return False
    turn = matches[0]
    recorded_started = turn.get("process_started")
    recorded_identity = turn.get("child_process_identity")
    return (
        turn.get("status") == "running"
        and turn.get("turn_id") == turn_id
        and type(turn.get("fence")) is int
        and turn.get("fence") == fence
        and (recorded_started is None or recorded_started == process_started)
        and (recorded_identity is None or recorded_identity == child_identity)
    )


def _reconcile_stopped_orphan_active(run, guard) -> bool:
    """Finalize a crashed active turn only while its controller lock is proven free."""
    lock_probe = None
    try:
        with run.lock():
            state = load_state(run)
            if not _orphan_active_cleanup_proven(state):
                return False
            try:
                require_workspace_owner(guard, run, state)
                lock_probe = prove_controller_lock_available(run)
            except StoreError:
                return False

            current = load_state(run)
            if (current.get("status") != "stopped"
                    or current.get("controller_identity") != state.get("controller_identity")
                    or current.get("fencing_token") != state.get("fencing_token")
                    or current.get("active") != state.get("active")
                    or not _orphan_active_cleanup_proven(current)):
                return False

            active = current["active"]
            turn = next(item for item in current["turns"]
                        if item.get("request_id") == active["request_id"])
            process_started = active["process_started"]
            child_identity = active.get("child_identity")
            failure_code = ("controller_crashed_during_turn" if process_started
                            else "controller_crashed_before_turn_start")
            turn.update({
                "status": "uncertain" if process_started else "cancelled_before_start",
                "failure_code": failure_code,
                "process_started": process_started,
                "ended_at": utc_now(),
            })
            if child_identity is not None:
                turn["child_process_identity"] = child_identity.copy()
                turn["child_process_group_alive_after_exit"] = False
                turn["child_process_group_status_after_exit"] = "gone"
            termination_result = active.get("termination_result")
            if isinstance(termination_result, dict):
                termination = {
                    key: bool(termination_result.get(key))
                    for key in ("attempted", "terminated", "killed")
                }
                if isinstance(termination_result.get("reason"), str):
                    termination["reason"] = termination_result["reason"]
                turn["agent_process_termination"] = termination
            current["active"] = None
            current["reconciliation_required"] = bool(
                process_started or current.get("reconciliation_required")
            )
            current["failure_code"] = failure_code
            recurrence = current.get("recurrence")
            if isinstance(recurrence, dict) and recurrence.get("mode") == "external_interval":
                recurrence.update({"state": "cancelled", "next_due_at": None,
                                   "remaining_seconds": None, "block_reason": "terminal_stop"})
            for queued in current.get("turns", []):
                if queued.get("status") == "queued":
                    queued["status"] = "cancelled_before_start"
                    queued["ended_at"] = utc_now()
            run.save(current)
            run.append_event({
                "at": utc_now(), "type": "orphan_active_reconciled",
                "request_id": active["request_id"], "turn_id": active["turn_id"],
                "fence": active["fence"], "status": turn["status"],
                "failure_code": failure_code,
            })
            update_workspace_owner(guard, run, current)
            return True
    finally:
        if lock_probe is not None:
            os.close(lock_probe)


def _turn(args: argparse.Namespace) -> tuple[int, dict]:
    _require_linux()
    wait_seconds = _positive_seconds(args.wait_seconds, "wait_seconds", allow_zero=True)
    run = validate_existing_run(args.state_dir)
    if not args.command.strip():
        raise AgentError("explicit_turn_command_required")
    request_id = uuid.uuid4().hex
    turn_id = f"turn-{request_id}"
    with run.lock():
        snapshot = load_state(run)
    with locked_workspace(snapshot["workspace"]) as guard:
        with run.lock():
            state = load_state(run)
            require_workspace_owner(guard, run, state)
            if state.get("status") == "stopped":
                raise AgentError("run_stopped_terminal")
            if state.get("status") != "ready":
                raise AgentError("run_not_ready_use_resume_or_recover")
            controller_state = process_identity_status(state.get("controller_identity"))
            if controller_state == "unknown":
                raise AgentError("controller_identity_unknown_use_recover")
            if controller_state != "alive":
                raise AgentError("controller_not_live_use_recover")
            if not state.get("child_session_identity"):
                raise AgentError("child_session_identity_missing")
            if state.get("active") is not None or any(item.get("status") == "queued" for item in state.get("turns", [])):
                raise AgentError("another_turn_owns_the_run")
            if state.get("reconciliation_required"):
                raise AgentError("child_reconciliation_required")
            if task_sha256(Path(state["task_path"])) != state["task_sha256"]:
                raise AgentError("task_digest_changed_start_new_run_or_reconcile")
            write_request(run, request_id, {
                "request_id": request_id,
                "turn_id": turn_id,
                "kind": "turn",
                "command": args.command,
            })
            state["turns"].append({
                "request_id": request_id,
                "turn_id": turn_id,
                "kind": "turn",
                "status": "queued",
                "command_sha256": hashlib.sha256(args.command.encode("utf-8")).hexdigest(),
                "submitted_at": utc_now(),
            })
            run.save(state)
            run.append_event({"at": utc_now(), "type": "turn_queued", "request_id": request_id,
                              "turn_id": turn_id, "task_sha256": state["task_sha256"]})
    outcome = _await_turn(run, request_id, wait_seconds) if wait_seconds else "queued"
    status = public_status(run)
    status["submitted_request_id"] = request_id
    status["submitted_turn_id"] = turn_id
    status["request_outcome"] = outcome
    if outcome in {"acknowledged", "queued", "timeout"}:
        return 0 if outcome != "timeout" else 2, status
    return 1, status


def _control_signal(run, desired_status: str, *, wait_seconds: float) -> tuple[int, dict]:
    _require_linux()
    _positive_seconds(wait_seconds, "wait_seconds", allow_zero=True, maximum=120)
    with run.lock():
        snapshot = load_state(run)
    termination = {"attempted": False, "terminated": False, "killed": False, "reason": "no_active_child"}
    request_id = None
    with locked_workspace(snapshot["workspace"]) as guard:
        with run.lock():
            state = load_state(run)
            owner_record = guard.read()
            owner_matches_run = bool(
                owner_record
                and owner_record.get("run_id") == run.run_id
                and owner_record.get("state_dir") == str(run.dir.resolve(strict=True))
                and owner_record.get("workspace") == snapshot["workspace"]
            )
            repeated_released_stop = bool(
                desired_status == "stopped"
                and state.get("status") == "stopped"
                and state.get("active") is None
                and owner_matches_run
                and owner_record.get("status") == "released"
                and owned_processes_gone(state)
            )
            if not repeated_released_stop:
                require_workspace_owner(guard, run, state)
                if state.get("status") == "stopped" and desired_status == "paused":
                    raise AgentError("run_stopped_terminal")
            active = state.get("active")
            child_identity = active.get("child_identity") if isinstance(active, dict) else None
            request_id = active.get("request_id") if isinstance(active, dict) else None
            if isinstance(active, dict) and child_identity is None:
                termination["reason"] = "active_child_identity_unknown_refused"
            if repeated_released_stop:
                pass
            elif desired_status == "paused":
                already_paused = state.get("status") == "paused"
                if not already_paused:
                    recurrence = state.get("recurrence")
                    if isinstance(recurrence, dict) and recurrence.get("mode") == "external_interval":
                        interval = recurrence.get("interval_seconds")
                        due = recurrence.get("next_due_at")
                        remaining = max(0.0, float(due) - time.time()) if isinstance(due, (int, float)) else None
                        if recurrence.get("state") == "delivering" or remaining is None:
                            remaining = interval if isinstance(interval, (int, float)) else None
                        recurrence.update({"state": "paused", "next_due_at": None,
                                           "remaining_seconds": remaining, "block_reason": None})
                    state["status"] = "paused"
                    state["fencing_token"] = int(state.get("fencing_token", 0)) + 1
                    for turn in state.get("turns", []):
                        if turn.get("status") == "queued":
                            turn["status"] = "cancelled_before_start"
                            turn["ended_at"] = utc_now()
                if isinstance(active, dict):
                    active["cancel_requested"] = "paused"
                    state["reconciliation_required"] = True
                run.save(state)
                if not already_paused:
                    run.append_event({"at": utc_now(), "type": desired_status,
                                      "fencing_token": state["fencing_token"]})
            else:
                already_stopped = state.get("status") == "stopped"
                state["status"] = "stopped"
                state["terminal_at"] = state.get("terminal_at") or utc_now()
                if not already_stopped:
                    state["fencing_token"] = int(state.get("fencing_token", 0)) + 1
                recurrence = state.get("recurrence")
                if isinstance(recurrence, dict) and recurrence.get("mode") == "external_interval":
                    recurrence.update({"state": "cancelled", "next_due_at": None,
                                       "remaining_seconds": None, "block_reason": "terminal_stop"})
                for turn in state.get("turns", []):
                    if turn.get("status") == "queued":
                        turn["status"] = "cancelled_before_start"
                        turn["ended_at"] = utc_now()
                if isinstance(active, dict):
                    active["cancel_requested"] = "stopped"
                    state["reconciliation_required"] = True
                run.save(state)
                if not already_stopped:
                    run.append_event({"at": utc_now(), "type": desired_status,
                                      "fencing_token": state["fencing_token"]})
        with run.lock():
            state = load_state(run)
        if not repeated_released_stop:
            update_workspace_owner(guard, run, state)
    if child_identity is not None:
        identity_state = process_identity_status(child_identity)
        if not valid_process_identity(child_identity) or identity_state == "unknown":
            termination["reason"] = "child_identity_unknown_refused"
        elif identity_state == "dead":
            termination["reason"] = "child_identity_dead_no_signal"
        elif process_group_observation(child_identity) != "alive":
            termination["reason"] = "child_process_group_membership_unverified"
        else:
            termination = terminate_identity_checked(child_identity, grace_seconds=2.0)
    with run.lock():
        state = load_state(run)
    if request_id:
        with locked_workspace(state["workspace"]) as guard:
            with run.lock():
                state = load_state(run)
                active = state.get("active")
                if isinstance(active, dict) and active.get("request_id") == request_id:
                    active["termination_result"] = {
                        **{key: bool(termination.get(key)) for key in ("attempted", "terminated", "killed")},
                        "reason": termination.get("reason"),
                    }
                    run.save(state)
            with run.lock():
                state = load_state(run)
            update_workspace_owner(guard, run, state)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        with run.lock():
            state = load_state(run)
        if state.get("active") is None and _child_groups_gone(state):
            break
        if process_identity_status(state.get("controller_identity")) == "dead":
            break
        time.sleep(0.1)
    if desired_status == "stopped":
        with run.lock():
            orphan_state = load_state(run)
        if orphan_state.get("status") == "stopped" and orphan_state.get("active") is not None:
            try:
                with locked_workspace(orphan_state["workspace"]) as guard:
                    _reconcile_stopped_orphan_active(run, guard)
            except (OSError, StoreError):
                pass
    with run.lock():
        state = load_state(run)
    cleanup_proven = state.get("active") is None and _child_groups_gone(state)
    status = public_status(run)
    status["agent_process_termination"] = {
        key: bool(termination.get(key)) for key in ("attempted", "terminated", "killed")
    }
    status["agent_process_termination"]["reason"] = termination.get("reason")
    status["cleanup_proven"] = cleanup_proven
    status["cleanup_pending"] = not cleanup_proven
    status["application_worker_checkpoint"] = "unknown"
    status["application_worker_stop"] = "unknown"
    return (0 if cleanup_proven else 2), status


def _create_reconcile_request(run, state: dict, command: str, *, kind: str,
                              target_session: str) -> tuple[str, dict]:
    request_id = uuid.uuid4().hex
    turn_id = f"reconcile-{request_id}"
    fence = int(state.get("fencing_token", 0)) + 1
    state["fencing_token"] = fence
    write_request(run, request_id, {
        "request_id": request_id,
        "turn_id": turn_id,
        "kind": kind,
        "command": command,
        "target_session": target_session,
    })
    state["turns"].append({
        "request_id": request_id,
        "turn_id": turn_id,
        "kind": "reconcile",
        "status": "queued",
        "fence": fence,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "submitted_at": utc_now(),
    })
    return request_id, state


def _resume(args: argparse.Namespace) -> tuple[int, dict]:
    _require_linux()
    timeout = _positive_seconds(args.wait_seconds, "wait_seconds")
    run = validate_existing_run(args.state_dir)
    if not args.command.strip():
        raise AgentError("explicit_reconciliation_command_required")
    with run.lock():
        snapshot = load_state(run)
    with locked_workspace(snapshot["workspace"]) as guard:
        with run.lock():
            state = load_state(run)
            require_workspace_owner(guard, run, state)
            if state.get("status") == "stopped":
                raise AgentError("run_stopped_terminal")
            if state.get("status") not in {"paused", "needs_reconciliation"}:
                raise AgentError("run_not_paused_or_needs_reconciliation")
            controller_state = process_identity_status(state.get("controller_identity"))
            if controller_state == "unknown":
                raise AgentError("controller_identity_unknown_use_recover")
            if controller_state != "alive":
                raise AgentError("controller_not_live_use_recover")
            if state.get("active") is not None:
                raise AgentError("active_turn_not_reconciled_yet")
            target = state.get("child_session_identity")
            if not target:
                raise AgentError("no_acknowledged_child_session_use_recover_with_exact_candidate")
            if not _child_groups_gone(state):
                raise AgentError("previous_child_process_group_alive_or_unknown")
            if args.session_id is not None and args.session_id != target:
                raise AgentError("mismatched_resume_identity")
            if task_sha256(Path(state["task_path"])) != state["task_sha256"]:
                raise AgentError("task_digest_changed_start_new_run_or_reconcile")
            request_id, state = _create_reconcile_request(run, state, args.command,
                                                          kind="reconcile", target_session=target)
            state["status"] = "ready"
            state["reconciliation_required"] = True
            state["failure_code"] = state.get("failure_code") or "reconciliation_requested"
            run.save(state)
            run.append_event({"at": utc_now(), "type": "resume_requested", "request_id": request_id,
                              "fence": state["fencing_token"], "session_id": target})
        with run.lock():
            updated_state = load_state(run)
        update_workspace_owner(guard, run, updated_state)
    outcome = _await_turn(run, request_id, timeout)
    status = public_status(run)
    status["request_outcome"] = outcome
    status["submitted_request_id"] = request_id
    if outcome == "acknowledged" and status["status"] == "ready":
        return 0, status
    if outcome == "timeout":
        return 2, status
    return 1, status


def _recover(args: argparse.Namespace) -> tuple[int, dict]:
    _require_linux()
    timeout = _positive_seconds(args.wait_seconds, "wait_seconds")
    run = validate_existing_run(args.state_dir)
    if not args.command.strip():
        raise AgentError("explicit_reconciliation_command_required")
    lock_probe = None
    try:
        with run.lock():
            snapshot = load_state(run)
        with locked_workspace(snapshot["workspace"]) as guard:
            with run.lock():
                state = load_state(run)
                if state.get("workspace") != snapshot.get("workspace"):
                    raise AgentError("workspace_identity_changed_recovery_refused")
                if state.get("status") == "stopped" or state.get("status") in TERMINAL_STATES:
                    raise AgentError("run_stopped_terminal")
                if state.get("status") not in {"starting", "ready", "paused", "recovering", "needs_reconciliation", "failed"}:
                    raise AgentError("run_state_not_recoverable")
                controller_identity = state.get("controller_identity")
                controller_generation = state.get("controller_generation", 0)
                if controller_identity is None and type(controller_generation) is int and controller_generation == 0:
                    if state.get("failure_code") not in {
                        "controller_spawn_failed", "controller_log_create_failed", "controller_log_collision",
                    }:
                        raise AgentError("controller_identity_unknown_recovery_refused")
                    controller_was_dead = True
                else:
                    controller_state = process_identity_status(controller_identity)
                    if controller_state == "unknown":
                        raise AgentError("controller_identity_unknown_recovery_refused")
                    if controller_state == "alive":
                        raise AgentError("controller_still_live_recovery_refused")
                    if process_group_observation(controller_identity) != "gone":
                        raise AgentError("controller_process_group_alive_or_unknown_recovery_refused")
                    controller_was_dead = True
                try:
                    lock_probe = prove_controller_lock_available(run)
                except StoreError as exc:
                    raise AgentError("controller_owner_lock_still_live") from exc
                active = state.get("active")
                if active is not None:
                    if not isinstance(active, dict) or not valid_process_identity(active.get("child_identity")):
                        raise AgentError("active_child_identity_unknown_recovery_refused")
                    child_identity = active["child_identity"]
                    child_state = process_identity_status(child_identity)
                    if child_state == "unknown":
                        raise AgentError("active_child_identity_unknown_recovery_refused")
                    if child_state == "alive":
                        raise AgentError("active_child_still_live_recovery_refused")
                    if process_group_observation(child_identity) != "gone":
                        raise AgentError("active_child_process_group_alive_or_unknown_recovery_refused")
                    turn = next((item for item in state.get("turns", [])
                                 if item.get("request_id") == active.get("request_id")), None)
                    if turn:
                        turn["status"] = "uncertain"
                        turn["failure_code"] = "controller_crashed_during_turn"
                        turn["process_started"] = True
                        turn["child_process_identity"] = child_identity
                        turn["child_process_group_alive_after_exit"] = False
                        turn["child_process_group_status_after_exit"] = "gone"
                        turn["ended_at"] = utc_now()
                    state["active"] = None
                    state["reconciliation_required"] = True
                if not _child_groups_gone(state):
                    raise AgentError("previous_child_process_group_alive_or_unknown_recovery_refused")
                child_session = state.get("child_session_identity")
                candidate = state.get("candidate_child_session_identity")
                if child_session:
                    if args.session_id is not None and args.session_id != child_session:
                        raise AgentError("mismatched_resume_identity")
                    target = child_session
                    next_status = "recovering"
                    recovery_kind = "reconcile"
                elif candidate:
                    if args.session_id is None or args.session_id != candidate:
                        raise AgentError("exact_candidate_session_id_required_for_recovery")
                    if candidate == state.get("parent_session_identity"):
                        raise AgentError("parent_session_reuse")
                    state["child_session_identity"] = candidate
                    target = candidate
                    next_status = "recovering"
                    recovery_kind = "reconcile"
                    state["ownership_acknowledgement"] = "not_observed"
                else:
                    if args.session_id is not None:
                        raise AgentError("no_child_session_matches_recovery_target")
                    bootstrap = next((item for item in state.get("turns", [])
                                      if item.get("turn_id") == "bootstrap"), None)
                    never_dispatched = bool(
                        bootstrap and bootstrap.get("status") == "queued"
                        and state.get("ownership_acknowledgement") == "pending"
                    )
                    launch_failed_before_process = bool(
                        bootstrap and bootstrap.get("status") == "failed"
                        and bootstrap.get("process_started") is False
                        and state.get("ownership_acknowledgement") == "not_observed"
                    )
                    if (state.get("status") not in {"starting", "paused", "failed", "needs_reconciliation"}
                            or not (never_dispatched or launch_failed_before_process)):
                        raise AgentError("no_safe_child_identity_for_recovery")
                    target = ""
                    next_status = "starting"
                    recovery_kind = "ownership"
                    state["startup_request_id"] = None
                if task_sha256(Path(state["task_path"])) != state["task_sha256"]:
                    raise AgentError("task_digest_changed_start_new_run_or_reconcile")
                recurrence = state.get("recurrence")
                if isinstance(recurrence, dict) and recurrence.get("mode") == "external_interval":
                    if recurrence.get("state") == "scheduled" and isinstance(recurrence.get("next_due_at"), (int, float)):
                        remaining = max(0.0, recurrence["next_due_at"] - time.time())
                        if remaining == 0:
                            remaining = recurrence.get("interval_seconds")
                    else:
                        remaining = recurrence.get("remaining_seconds")
                        if not isinstance(remaining, (int, float)):
                            remaining = recurrence.get("interval_seconds")
                    recurrence.update({"state": "recovering", "next_due_at": None,
                                       "remaining_seconds": remaining})
                for turn in state.get("turns", []):
                    if turn.get("status") == "queued":
                        turn["status"] = "cancelled_before_start"
                        turn["ended_at"] = utc_now()
                request_id, state = _create_reconcile_request(
                    run, state, args.command, kind=recovery_kind, target_session=target,
                )
                if recovery_kind == "ownership":
                    row = state["turns"][-1]
                    row["kind"] = "ownership"
                    state["recovery_request_id"] = None
                    state["startup_request_id"] = request_id
                    state["status"] = "starting"
                else:
                    state["recovery_request_id"] = request_id
                    state["status"] = next_status
                state["failure_code"] = "reconciliation_requested"
                state["reconciliation_required"] = bool(recovery_kind == "reconcile")
                state.setdefault("recovery_history", []).append({
                    "at": utc_now(),
                    "controller_was_dead": controller_was_dead,
                    "active_child_was_dead": active is None or bool(active),
                    "fencing_token": state["fencing_token"],
                    "target_session_recorded": bool(target),
                })
                run.save(state)
                run.append_event({"at": utc_now(), "type": "recovery_claimed", "request_id": request_id,
                                  "fence": state["fencing_token"], "session_id": target or None})
                adopt_workspace_for_recovery(guard, run, state)
                _spawn_service_locked(run, state)
            with run.lock():
                recovered_controller_state = load_state(run)
            update_workspace_owner(guard, run, recovered_controller_state)
    finally:
        if lock_probe is not None:
            os.close(lock_probe)
    outcome = _await_turn(run, request_id, timeout)
    status = public_status(run)
    status["request_outcome"] = outcome
    status["submitted_request_id"] = request_id
    if outcome == "acknowledged" and status["status"] == "ready" and status["controller"]["alive"]:
        return 0, status
    if outcome == "timeout":
        return 2, status
    return 1, status


def _wait_control_completion(run, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        with run.lock():
            state = load_state(run)
        identity_state = process_identity_status(state.get("controller_identity"))
        group_state = process_group_observation(state.get("controller_identity"))
        if identity_state == "dead" and group_state == "gone":
            return True
        if identity_state == "unknown" or time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _selfcheck() -> int:
    from background_agent_selfcheck import run_selfcheck

    return run_selfcheck()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    start = sub.add_parser("start", help="launch one detached controller and wait for child ownership acknowledgement")
    start.add_argument("--harness", choices=("opencode", "codex", "claude-code"), required=True)
    start.add_argument("--parent-session", required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--variant")
    start.add_argument("--reasoning-effort")
    start.add_argument("--effort", help="Claude Code effort level: low, medium, high, xhigh, or max")
    start.add_argument("--workspace", required=True)
    start.add_argument("--task", required=True)
    start.add_argument("--state-dir", required=True, help="new per-run directory; its final component is the run id")
    start.add_argument("--command", required=True, help="explicit bounded bootstrap instruction; no default task runs")
    start.add_argument("--binary", help="select one CLI executable when PATH resolution is ambiguous")
    start.add_argument("--run-timeout-seconds", type=float, default=300.0)
    start.add_argument("--startup-timeout-seconds", type=float, default=600.0)
    start.add_argument("--profile", choices=("native", "isolated"), default="native",
                       help="retain caller configuration by default; isolated is for explicit fixture probes")
    start.add_argument("--interval-seconds", type=float,
                       help="opt into one external due-time continuation after each acknowledged in-progress turn")
    start.add_argument("--continuation-command",
                       help="explicit bounded useful task for each due continuation; required with --interval-seconds")

    for name, help_text in (("status", "show transcript-free run status"), ("diagnose", "classify controller/child process state")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--state-dir", required=True)

    turn = sub.add_parser("turn", help="queue one explicit bounded turn on the exact child session")
    turn.add_argument("--state-dir", required=True)
    turn.add_argument("--command", required=True)
    turn.add_argument("--wait-seconds", type=float, default=0.0)

    pause = sub.add_parser("pause", help="fence new turns and identity-check the active agent CLI process group")
    pause.add_argument("--state-dir", required=True)
    pause.add_argument("--wait-seconds", type=float, default=10.0)

    resume = sub.add_parser("resume", help="resume the exact child with mandatory task/session reconciliation")
    resume.add_argument("--state-dir", required=True)
    resume.add_argument("--command", required=True, help="explicit instruction appended after reconciliation requirements")
    resume.add_argument("--session-id", help="optional exact-target assertion; mismatches are refused")
    resume.add_argument("--wait-seconds", type=float, default=600.0)

    stop = sub.add_parser("stop", help="terminal stop; never reactivates this run")
    stop.add_argument("--state-dir", required=True)
    stop.add_argument("--wait-seconds", type=float, default=10.0)

    recover = sub.add_parser("recover", help="reconcile only after stale controller and child identities are proven dead")
    recover.add_argument("--state-dir", required=True)
    recover.add_argument("--command", required=True, help="explicit reconciliation instruction; never replays an uncertain turn")
    recover.add_argument("--session-id", help="assert the exact recorded child, or the exact unacknowledged candidate")
    recover.add_argument("--wait-seconds", type=float, default=600.0)

    check = sub.add_parser("selfcheck", help="run inert local-process and parser regressions; never starts a native harness")
    check.set_defaults(_selfcheck=True)
    service = sub.add_parser("__service", help=argparse.SUPPRESS)
    service.add_argument("--state-dir", required=True)
    service.set_defaults(_service=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "_selfcheck", False):
        return _selfcheck()
    if getattr(args, "_service", False):
        from background_agent_service import service_main

        return service_main(args.state_dir)
    try:
        if args.action == "start":
            code, result = _start(args)
        elif args.action == "status":
            result = public_status(validate_existing_run(args.state_dir))
            code = 0
        elif args.action == "diagnose":
            result = diagnose_run(validate_existing_run(args.state_dir))
            code = 0
        elif args.action == "turn":
            code, result = _turn(args)
        elif args.action == "pause":
            run = validate_existing_run(args.state_dir)
            code, result = _control_signal(run, "paused", wait_seconds=args.wait_seconds)
        elif args.action == "resume":
            code, result = _resume(args)
        elif args.action == "stop":
            run = validate_existing_run(args.state_dir)
            code, result = _control_signal(run, "stopped", wait_seconds=args.wait_seconds)
            controller_gone = _wait_control_completion(run, args.wait_seconds)
            with run.lock():
                stopped_state = load_state(run)
            with locked_workspace(stopped_state["workspace"]) as guard:
                with run.lock():
                    stopped_state = load_state(run)
                if (controller_gone and stopped_state.get("status") == "stopped"
                        and stopped_state.get("active") is not None):
                    _reconcile_stopped_orphan_active(run, guard)
                with run.lock():
                    stopped_state = load_state(run)
                ownership_released = release_stopped_workspace(guard, run, stopped_state)
            cleanup_proven = bool(
                controller_gone and stopped_state.get("status") == "stopped"
                and stopped_state.get("active") is None
                and _child_groups_gone(stopped_state) and ownership_released
            )
            result = public_status(run) | {
                "agent_process_termination": result.get("agent_process_termination"),
                "cleanup_proven": cleanup_proven,
                "cleanup_pending": not cleanup_proven,
                "workspace_ownership_released": ownership_released,
                "application_worker_checkpoint": "unknown",
                "application_worker_stop": "unknown",
            }
            if not cleanup_proven:
                code = 2
        elif args.action == "recover":
            code, result = _recover(args)
        else:
            return 2
        _public_json(result)
        return code
    except (AgentError, StoreError, AdapterError) as exc:
        code = exc.code if isinstance(exc, (AgentError, StoreError, AdapterError)) else "background_agent_error"
        _public_error(code)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError):
        _public_error("background_agent_io_error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
