"""Detached, one-owner delivery controller for bounded native-harness turns."""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from background_agent_adapters import (
    AdapterError,
    build_argv,
    get_adapter,
    make_prompt,
    native_primitive_observations,
    parse_trace,
    summary_hash,
    validate_ack,
)
from background_adapters.native_evidence import session_fingerprint
from background_agent_ownership import locked_workspace, require_workspace_owner, update_workspace_owner
from background_agent_store import (
    StoreError,
    acquire_controller_lock,
    identity_alive,
    load_state,
    make_run,
    open_raw_log,
    process_group_observation,
    process_identity,
    read_request,
    task_sha256,
    terminate_identity_checked,
    utc_now,
    valid_process_identity,
    validate_live_paths,
    write_request,
)

POLL_SECONDS = 0.1
CANCEL_GRACE_SECONDS = 2.0
WAIT_AFTER_SIGNAL_SECONDS = 6.0


def _identity_matches(left: dict | None, right: dict | None) -> bool:
    if not valid_process_identity(left) or not valid_process_identity(right):
        return False
    return all(left.get(key) == right.get(key) for key in ("pid", "boot_id", "starttime", "pgid"))


def _service_identity() -> dict | None:
    from bridge_core import process_identity

    identity = process_identity(os.getpid())
    return identity if valid_process_identity(identity) else None


def _update_event(run, event_type: str, **fields) -> None:
    allowed = {key: value for key, value in fields.items() if key in {
        "request_id", "turn_id", "fence", "status", "failure_code", "session_id",
        "controller_generation", "task_sha256", "process_role", "delivery",
    }}
    run.append_event({"at": utc_now(), "type": event_type, **allowed})


def _find_turn(state: dict, request_id: str) -> dict | None:
    return next((item for item in state.get("turns", []) if item.get("request_id") == request_id), None)


def _sync_workspace_owner(run) -> None:
    with run.lock():
        snapshot = load_state(run)
    with locked_workspace(snapshot["workspace"]) as guard:
        with run.lock():
            current = load_state(run)
        update_workspace_owner(guard, run, current)


def _arm_recurrence_after_ack(state: dict, task_status: str) -> None:
    recurrence = state.get("recurrence")
    if not isinstance(recurrence, dict) or recurrence.get("mode") != "external_interval":
        return
    if not recurrence.get("authorized") or state.get("status") in {"paused", "stopped"}:
        return
    if task_status != "in_progress":
        recurrence.update({
            "state": "blocked",
            "next_due_at": None,
            "remaining_seconds": None,
            "block_reason": f"agent_task_status_{task_status}",
        })
        return
    interval = recurrence.get("interval_seconds")
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 1:
        recurrence.update({"state": "blocked", "next_due_at": None,
                           "block_reason": "recurrence_interval_invalid"})
        return
    delay = interval
    if recurrence.get("state") in {"paused", "recovering"}:
        saved = recurrence.get("remaining_seconds")
        if isinstance(saved, (int, float)) and saved >= 0:
            delay = min(saved, interval)
    recurrence.update({
        "state": "scheduled",
        "next_due_at": time.time() + delay,
        "remaining_seconds": None,
        "block_reason": None,
    })


def _block_recurrence(state: dict, reason: str) -> None:
    recurrence = state.get("recurrence")
    if isinstance(recurrence, dict) and recurrence.get("mode") == "external_interval":
        if state.get("status") not in {"paused", "stopped"}:
            recurrence.update({"state": "blocked", "next_due_at": None,
                               "remaining_seconds": None, "block_reason": reason})


def _claim_startup(run, service_identity: dict) -> dict | None:
    with run.lock():
        state = load_state(run)
        if not _identity_matches(state.get("controller_identity"), service_identity):
            raise StoreError("controller_identity_mismatch")
        status = state.get("status")
        if status == "starting":
            request_id = state.get("startup_request_id")
            request_kind = "ownership"
            target_session = None
            fence = int(state.get("fencing_token", 0))
        elif status == "recovering":
            request_id = state.get("recovery_request_id")
            request_kind = "reconcile"
            target_session = state.get("child_session_identity")
            fence = int(state.get("fencing_token", 0))
        else:
            return None
        if not request_id or not isinstance(target_session, (str, type(None))):
            raise StoreError("startup_request_invalid")
        if state.get("active") is not None:
            raise StoreError("startup_active_claim_collision")
        turn = _find_turn(state, request_id)
        if not turn or turn.get("status") not in {"queued", "recovering"}:
            raise StoreError("startup_turn_record_missing")
        turn["status"] = "running"
        turn["started_at"] = utc_now()
        turn["fence"] = fence
        turn["kind"] = request_kind
        state["active"] = {
            "request_id": request_id,
            "turn_id": turn["turn_id"],
            "fence": fence,
            "controller_identity": service_identity,
            "child_identity": None,
            "process_started": False,
            "cancel_requested": None,
        }
        run.save(state)
        _update_event(run, "turn_claimed", request_id=request_id, turn_id=turn["turn_id"], fence=fence)
        return {
            "request_id": request_id,
            "turn_id": turn["turn_id"],
            "fence": fence,
            "kind": request_kind,
            "target_session": target_session,
        }


def _claim_queued(run, service_identity: dict) -> dict | None:
    with run.lock():
        state = load_state(run)
        if not _identity_matches(state.get("controller_identity"), service_identity):
            raise StoreError("controller_identity_mismatch")
        if state.get("status") != "ready" or state.get("active") is not None:
            return None
        turn = next((item for item in state.get("turns", []) if item.get("status") == "queued"), None)
        if turn is None:
            return None
        request_id = turn.get("request_id")
        if not isinstance(request_id, str):
            raise StoreError("queued_turn_request_invalid")
        fence = int(state.get("fencing_token", 0)) + 1
        state["fencing_token"] = fence
        turn["status"] = "running"
        turn["started_at"] = utc_now()
        turn["fence"] = fence
        state["active"] = {
            "request_id": request_id,
            "turn_id": turn["turn_id"],
            "fence": fence,
            "controller_identity": service_identity,
            "child_identity": None,
            "process_started": False,
            "cancel_requested": None,
        }
        run.save(state)
        _update_event(run, "turn_claimed", request_id=request_id, turn_id=turn["turn_id"], fence=fence)
        return {
            "request_id": request_id,
            "turn_id": turn["turn_id"],
            "fence": fence,
            "kind": turn.get("kind", "turn"),
            "target_session": state.get("child_session_identity"),
        }


def _queue_due_continuation(run, service_identity: dict) -> bool:
    with run.lock():
        state = load_state(run)
        if not _identity_matches(state.get("controller_identity"), service_identity):
            raise StoreError("controller_identity_mismatch")
        recurrence = state.get("recurrence")
        if (state.get("status") != "ready" or state.get("active") is not None
                or not isinstance(recurrence, dict)
                or recurrence.get("mode") != "external_interval"
                or recurrence.get("state") != "scheduled"):
            return False
        due_at = recurrence.get("next_due_at")
        if type(due_at) not in (int, float) or not math.isfinite(due_at):
            recurrence.update({"state": "blocked", "next_due_at": None,
                               "block_reason": "recurrence_due_time_invalid"})
            run.save(state)
            return False
        if time.time() < due_at:
            return False
        if any(item.get("status") == "queued" for item in state.get("turns", [])):
            return False
        command = recurrence.get("continuation_command")
        if not isinstance(command, str) or not command.strip():
            recurrence.update({"state": "blocked", "next_due_at": None,
                               "block_reason": "continuation_command_missing"})
            run.save(state)
            return False
        scheduled_count = recurrence.get("scheduled_count", 0)
        if type(scheduled_count) is not int or scheduled_count < 0:
            recurrence.update({"state": "blocked", "next_due_at": None,
                               "block_reason": "recurrence_count_invalid"})
            run.save(state)
            return False
        request_id = uuid.uuid4().hex
        turn_id = f"due-{scheduled_count + 1}-{request_id[:8]}"
        write_request(run, request_id, {
            "request_id": request_id,
            "turn_id": turn_id,
            "kind": "turn",
            "command": command,
        })
        state["turns"].append({
            "request_id": request_id,
            "turn_id": turn_id,
            "kind": "turn",
            "delivery": "external_due_time",
            "due_at": due_at,
            "status": "queued",
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "submitted_at": utc_now(),
        })
        recurrence.update({
            "state": "delivering",
            "next_due_at": None,
            "remaining_seconds": None,
            "last_request_id": request_id,
            "scheduled_count": scheduled_count + 1,
        })
        run.save(state)
        _update_event(run, "turn_queued", request_id=request_id, turn_id=turn_id,
                      delivery="external_due_time", task_sha256=state.get("task_sha256"))
    return True


def _record_child_identity(run, claim: dict, identity: dict | None) -> bool:
    with run.lock():
        state = load_state(run)
        active = state.get("active")
        if (not active or active.get("request_id") != claim["request_id"]
                or active.get("fence") != claim["fence"]):
            return False
        active["child_identity"] = identity
        active["process_started"] = True
        run.save(state)
        may_run = state.get("status") not in {"paused", "stopped"} and not active.get("cancel_requested")
    _sync_workspace_owner(run)
    return may_run and valid_process_identity(identity)


def _cancel_reason(run, claim: dict) -> str | None:
    with run.lock():
        state = load_state(run)
    active = state.get("active")
    if state.get("status") == "stopped":
        return "stopped"
    if state.get("status") == "paused":
        return "paused"
    if (not active or active.get("request_id") != claim["request_id"]
            or active.get("fence") != claim["fence"]):
        return "fenced"
    return active.get("cancel_requested")


def _wait_process(run, claim: dict, proc: subprocess.Popen, identity: dict | None,
                  timeout_seconds: float) -> tuple[int | None, bool, str | None, dict]:
    deadline = time.monotonic() + timeout_seconds
    termination: dict = {"attempted": False, "terminated": False, "killed": False}
    timed_out = False
    cancel_reason = None
    while proc.poll() is None:
        cancel_reason = _cancel_reason(run, claim)
        if cancel_reason:
            if identity is not None and identity_alive(identity):
                termination = terminate_identity_checked(identity, grace_seconds=CANCEL_GRACE_SECONDS)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            if identity is not None and identity_alive(identity):
                termination = terminate_identity_checked(identity, grace_seconds=CANCEL_GRACE_SECONDS)
            break
        time.sleep(POLL_SECONDS)
    try:
        proc.wait(timeout=WAIT_AFTER_SIGNAL_SECONDS)
    except subprocess.TimeoutExpired:
        if identity is not None and identity_alive(identity):
            termination = terminate_identity_checked(identity, grace_seconds=0.0)
        try:
            proc.wait(timeout=WAIT_AFTER_SIGNAL_SECONDS)
        except subprocess.TimeoutExpired:
            return None, timed_out, cancel_reason or "child_cleanup_unconfirmed", termination
    if cancel_reason is None:
        cancel_reason = _cancel_reason(run, claim)
    return proc.returncode, timed_out, cancel_reason, termination


def _finalize(run, claim: dict, trace: dict | None, *, exit_code: int | None,
              timed_out: bool, cancel_reason: str | None, failure_code: str | None,
              log_bytes: bytes, termination: dict, process_started: bool) -> None:
    digest = hashlib.sha256(log_bytes).hexdigest()
    with run.lock():
        state = load_state(run)
        active = state.get("active")
        turn = _find_turn(state, claim["request_id"])
        if turn is None:
            raise StoreError("turn_record_disappeared")
        turn["process_started"] = process_started
        fenced = not active or active.get("request_id") != claim["request_id"] or active.get("fence") != claim["fence"]
        current_status = state.get("status")
        interrupted = bool(cancel_reason or fenced or current_status in {"paused", "stopped"})
        child_identity = active.get("child_identity") if isinstance(active, dict) else None
        child_group_status = process_group_observation(child_identity)
        adapter = get_adapter(state["harness"])
        parsed_trace = trace or {}
        native_operations = native_primitive_observations(
            state["harness"],
            parsed_trace.get("native_tools", []),
            declarations=adapter.declared_primitives,
            scope={
                "product": adapter.product_name,
                "product_version": state.get("harness_version"),
                "os": sys.platform,
                "profile": state.get("adapter_profile", "native"),
                "run_id": run.run_id,
                "request_id": claim["request_id"],
                "turn_id": claim["turn_id"],
                "fence": claim["fence"],
                "session_fingerprint": session_fingerprint(parsed_trace.get("session_id")),
            },
        )
        child_group_alive = child_group_status in {"alive", "present_unverified"}
        turn["child_process_identity"] = child_identity
        turn["child_process_group_alive_after_exit"] = child_group_alive
        turn["child_process_group_status_after_exit"] = child_group_status
        turn["native_primitive_observations"] = native_operations
        native_history = state.get("native_primitive_observations")
        if not isinstance(native_history, list):
            native_history = []
        native_history.append(native_operations)
        state["native_primitive_observations"] = native_history[-100:]
        if process_started and child_group_status != "gone" and failure_code is None:
            failure_code = "child_process_group_cleanup_unconfirmed"
        if interrupted:
            turn["status"] = "uncertain" if process_started else "cancelled_before_start"
            turn["failure_code"] = (
                "child_process_group_cleanup_unconfirmed" if child_group_status != "gone"
                else "turn_interrupted" if process_started
                else "turn_cancelled_before_start"
            )
            turn["agent_process_termination"] = {
                key: bool(termination.get(key)) for key in ("attempted", "terminated", "killed")
            }
            turn["ended_at"] = utc_now()
            state["reconciliation_required"] = bool(process_started)
            if current_status not in {"paused", "stopped"}:
                state["status"] = "needs_reconciliation"
            state["failure_code"] = turn["failure_code"]
        elif failure_code:
            if not process_started:
                turn["status"] = "failed"
                turn["failure_code"] = failure_code
                turn["ended_at"] = utc_now()
                state["failure_code"] = failure_code
                if claim["kind"] in {"ownership", "reconcile"}:
                    state["status"] = "needs_reconciliation"
                    state["reconciliation_required"] = True
                elif state.get("status") == "ready":
                    state["status"] = "ready"
            else:
                turn["status"] = "uncertain"
                turn["failure_code"] = failure_code
                turn["exit_code"] = exit_code
                turn["timed_out"] = timed_out
                turn["raw_sha256"] = digest
                turn["raw_bytes"] = len(log_bytes)
                turn["native_event_count"] = (trace or {}).get("event_count", 0)
                turn["malformed_event_count"] = (trace or {}).get("malformed_event_count", 0)
                turn["ended_at"] = utc_now()
                state["status"] = "needs_reconciliation"
                state["reconciliation_required"] = True
                state["failure_code"] = failure_code
                if claim["kind"] == "ownership" and trace and trace.get("session_id"):
                    candidate = trace["session_id"]
                    if candidate != state.get("parent_session_identity"):
                        state["candidate_child_session_identity"] = candidate
            if claim["kind"] == "ownership" and state.get("status") == "needs_reconciliation":
                state["ownership_acknowledgement"] = "not_observed"
        else:
            ack = trace["final_ack"]
            session_id = trace["session_id"]
            turn["status"] = "acknowledged"
            turn["task_status"] = ack["task_status"]
            turn["acknowledgement_observed"] = True
            turn["summary_sha256"] = summary_hash(ack["summary"])
            turn["exit_code"] = exit_code
            turn["timed_out"] = timed_out
            turn["raw_sha256"] = digest
            turn["raw_bytes"] = len(log_bytes)
            turn["native_event_count"] = trace.get("event_count", 0)
            turn["malformed_event_count"] = trace.get("malformed_event_count", 0)
            turn["native_tools"] = trace.get("native_tools", [])
            turn["child_session_identity"] = session_id
            turn["ended_at"] = utc_now()
            state["native_tool_observations"] = (state.get("native_tool_observations", []) +
                                                   trace.get("native_tools", []))[-100:]
            state["task_status"] = ack["task_status"]
            state["failure_code"] = None
            state["reconciliation_required"] = False
            if claim["kind"] == "ownership":
                state["ownership_acknowledgement"] = "observed"
                state["ownership_ack_binding"] = {
                    "run_id": run.run_id,
                    "session_id": session_id,
                    "task_sha256": state["task_sha256"],
                    "fence": claim["fence"],
                    "ack_turn_id": claim["turn_id"],
                }
            elif state.get("ownership_acknowledgement") == "not_observed":
                state["ownership_acknowledgement"] = "reconciled"
                state["ownership_ack_binding"] = {
                    "run_id": run.run_id,
                    "session_id": session_id,
                    "task_sha256": state["task_sha256"],
                    "fence": claim["fence"],
                    "ack_turn_id": claim["turn_id"],
                    "observed_via": "exact_session_reconciliation",
                }
            else:
                state["ownership_acknowledgement"] = state.get("ownership_acknowledgement", "observed")
            if claim["kind"] == "ownership":
                state["child_session_identity"] = session_id
                state["candidate_child_session_identity"] = None
                state["status"] = "ready"
            elif claim["kind"] == "reconcile":
                state["child_session_identity"] = session_id
                state["candidate_child_session_identity"] = None
                state["status"] = "ready"
                state["ownership_reconciled"] = True
            else:
                state["status"] = "ready"
        if turn.get("status") != "acknowledged":
            _block_recurrence(state, turn.get("failure_code") or "turn_outcome_uncertain")
        else:
            _arm_recurrence_after_ack(state, turn["task_status"])
        if active and active.get("request_id") == claim["request_id"] and active.get("fence") == claim["fence"]:
            state["active"] = None
        run.save(state)
        _update_event(
            run,
            "turn_finalized",
            request_id=claim["request_id"],
            turn_id=claim["turn_id"],
            fence=claim["fence"],
            status=turn.get("status"),
            failure_code=turn.get("failure_code"),
            session_id=turn.get("child_session_identity"),
            task_sha256=state.get("task_sha256"),
        )
    _sync_workspace_owner(run)


def _run_claim(run, claim: dict, service_identity: dict) -> None:
    try:
        request = read_request(run, claim["request_id"])
    except StoreError as exc:
        _finalize(run, claim, None, exit_code=None, timed_out=False, cancel_reason=None,
                  failure_code=exc.code, log_bytes=b"", termination={}, process_started=False)
        return
    with run.lock():
        state = load_state(run)
    try:
        workspace_path, task_path, _ = validate_live_paths(
            state["workspace"], state["task_path"], str(run.dir),
        )
        current_task_hash = task_sha256(task_path)
    except StoreError:
        current_task_hash = None
    if current_task_hash != state["task_sha256"]:
        _finalize(run, claim, None, exit_code=None, timed_out=False, cancel_reason=None,
                  failure_code="task_digest_changed", log_bytes=b"", termination={}, process_started=False)
        return
    if not _identity_matches(state.get("controller_identity"), service_identity):
        return
    if claim["kind"] in {"turn", "reconcile"}:
        target = claim.get("target_session") or state.get("child_session_identity")
        if not target or target == state.get("parent_session_identity"):
            _finalize(run, claim, None, exit_code=None, timed_out=False, cancel_reason=None,
                      failure_code="child_session_identity_unavailable", log_bytes=b"", termination={}, process_started=False)
            return
    else:
        target = None
    expected_session = target or (
        state.get("fresh_session_id_requested")
        if claim["kind"] == "ownership" and get_adapter(state["harness"]).requires_fresh_session_id
        else None
    )
    prompt = make_prompt(
        harness=state["harness"],
        run_id=run.run_id,
        task_path=Path(state["task_path"]),
        task_sha256=state["task_sha256"],
        command=request["command"],
        turn_id=claim["turn_id"],
        fence=claim["fence"],
        kind=claim["kind"],
        reconciliation_required=bool(state.get("reconciliation_required") or claim["kind"] == "reconcile"),
        external_recurrence=(state.get("recurrence", {}).get("mode") == "external_interval"),
    )
    try:
        argv = build_argv(
            harness=state["harness"], binary=state["binary"], model=state["model"],
            workspace=Path(state["workspace"]), prompt=prompt, variant=state.get("variant"),
            reasoning_effort=state.get("reasoning_effort"), effort=state.get("effort"), session_id=target,
            fresh_session_id=(
                state.get("fresh_session_id_requested")
                if claim["kind"] == "ownership" and target is None else None
            ),
            profile=state.get("adapter_profile", "native"),
        )
    except AdapterError as exc:
        _finalize(run, claim, None, exit_code=None, timed_out=False, cancel_reason=None,
                  failure_code=exc.code, log_bytes=b"", termination={}, process_started=False)
        return

    try:
        raw_fd, raw_path = open_raw_log(run, claim["request_id"])
    except StoreError as exc:
        _finalize(run, claim, None, exit_code=None, timed_out=False, cancel_reason=None,
                  failure_code=exc.code, log_bytes=b"", termination={}, process_started=False)
        return

    proc = None
    identity = None
    termination: dict = {"attempted": False, "terminated": False, "killed": False}
    timed_out = False
    cancel_reason = None
    exit_code: int | None = None
    process_started = False
    try:
        with os.fdopen(raw_fd, "wb") as raw_stream:
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.Popen(
                argv,
                cwd=workspace_path,
                stdin=subprocess.DEVNULL,
                stdout=raw_stream,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
            process_started = True
            deadline = time.monotonic() + float(state["run_timeout_seconds"])
            while time.monotonic() < deadline:
                identity = process_identity(proc.pid)
                if valid_process_identity(identity):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
            if not valid_process_identity(identity):
                _record_child_identity(run, claim, identity)
                try:
                    exit_code = proc.wait(timeout=WAIT_AFTER_SIGNAL_SECONDS)
                except subprocess.TimeoutExpired:
                    exit_code = None
                raise StoreError("child_process_identity_unavailable")
            may_run = _record_child_identity(run, claim, identity)
            if not may_run:
                cancel_reason = _cancel_reason(run, claim) or "fenced"
            exit_code, timed_out, wait_cancel_reason, termination = _wait_process(
                run, claim, proc, identity, float(state["run_timeout_seconds"]),
            )
            cancel_reason = wait_cancel_reason or cancel_reason
        try:
            log_bytes = raw_path.read_bytes()
        except OSError:
            log_bytes = b""
            raise StoreError("raw_log_read_failed")
        trace = parse_trace(state["harness"], log_bytes.decode("utf-8", errors="replace"))
        if cancel_reason:
            failure_code = "turn_interrupted"
            ack = None
        elif timed_out:
            failure_code = "child_process_timeout"
            ack = None
        elif exit_code is None:
            failure_code = "child_process_exit_unobserved"
            ack = None
        else:
            failure_code, ack = validate_ack(
                trace,
                exit_code=exit_code,
                kind="ownership_ack" if claim["kind"] == "ownership" else "turn_ack",
                run_id=run.run_id,
                task_sha256=state["task_sha256"],
                turn_id=claim["turn_id"],
                fence=claim["fence"],
                parent_session=state["parent_session_identity"] if claim["kind"] == "ownership" else None,
                target_session=expected_session,
            )
        if failure_code is None and ack is not None:
            live_workspace, live_task, _ = validate_live_paths(
                state["workspace"], state["task_path"], str(run.dir),
            )
            if live_workspace != workspace_path or task_sha256(live_task) != state["task_sha256"]:
                failure_code = "task_digest_changed_during_turn"
        _finalize(
            run, claim, trace,
            exit_code=exit_code, timed_out=timed_out, cancel_reason=cancel_reason,
            failure_code=failure_code, log_bytes=log_bytes, termination=termination,
            process_started=process_started,
        )
    except (OSError, subprocess.SubprocessError, StoreError, AdapterError) as exc:
        if proc is not None and proc.poll() is None and identity is not None and identity_alive(identity):
            termination = terminate_identity_checked(identity, grace_seconds=CANCEL_GRACE_SECONDS)
            try:
                proc.wait(timeout=WAIT_AFTER_SIGNAL_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        try:
            log_bytes = raw_path.read_bytes()
        except OSError:
            log_bytes = b""
        failure_code = exc.code if isinstance(exc, (StoreError, AdapterError)) else "child_process_error"
        try:
            _finalize(run, claim, None, exit_code=proc.returncode if proc else None, timed_out=timed_out,
                      cancel_reason=cancel_reason, failure_code=failure_code, log_bytes=log_bytes,
                      termination=termination, process_started=process_started)
        except (OSError, StoreError):
            pass


def service_main(state_dir_value: str) -> int:
    """Own one detached service process and serially consume explicit requests."""
    if not sys.platform.startswith("linux"):
        return 2
    run = make_run(Path(os.path.abspath(os.path.expanduser(state_dir_value))))
    owner_fd = None
    try:
        identity = _service_identity()
        if identity is None:
            raise StoreError("controller_process_identity_unavailable")
        with run.lock():
            state = load_state(run)
            if not _identity_matches(state.get("controller_identity"), identity):
                raise StoreError("controller_identity_mismatch")
        with locked_workspace(state["workspace"]) as guard:
            with run.lock():
                state = load_state(run)
            require_workspace_owner(guard, run, state)
        lock_deadline = time.monotonic() + 5.0
        while owner_fd is None:
            try:
                owner_fd = acquire_controller_lock(run)
            except StoreError as exc:
                if exc.code != "controller_owner_already_live" or time.monotonic() >= lock_deadline:
                    raise
                time.sleep(0.05)
        while True:
            with run.lock():
                state = load_state(run)
            if not _identity_matches(state.get("controller_identity"), identity):
                return 3
            if state.get("status") in {"stopped", "failed"}:
                return 0
            if state.get("active") is not None:
                time.sleep(POLL_SECONDS)
                continue
            claim = None
            if state.get("status") in {"starting", "recovering"}:
                claim = _claim_startup(run, identity)
            elif state.get("status") == "ready":
                claim = _claim_queued(run, identity)
            if claim is not None:
                _sync_workspace_owner(run)
                _run_claim(run, claim, identity)
                continue
            if state.get("status") == "ready" and _queue_due_continuation(run, identity):
                continue
            time.sleep(POLL_SECONDS)
    except Exception as exc:
        if isinstance(exc, StoreError) and exc.code == "controller_identity_mismatch":
            return 4
        if owner_fd is None:
            return 1
        try:
            with run.lock():
                state = load_state(run)
                if state.get("status") not in {"stopped", "failed"}:
                    state["failure_code"] = "controller_error"
                    state["status"] = "needs_reconciliation"
                    state["reconciliation_required"] = True
                    run.save(state)
                    _update_event(run, "controller_error", failure_code="controller_error")
        except Exception:  # state failure must not hide the original safe exit
            pass
        try:
            _sync_workspace_owner(run)
        except Exception:
            pass
        return 1
    finally:
        if owner_fd is not None:
            try:
                os.close(owner_fd)
            except OSError:
                pass
