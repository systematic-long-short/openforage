#!/usr/bin/env python3
"""Lifecycle engine for the external OpenCode supervisor example.

Linux-scoped, standard library only. This module is the compact core used by
``opencode_bridge.py`` (CLI) and ``run_fixture.py`` (orchestrator fixture).

Ownership and dispatch model (the defects this core exists to prevent):

  * The run lock is held only for claim/finalize mutations. A due event is
    claimed and a fencing token advanced under a short lock, then the lock is
    released while the child process runs, so ``pause`` and ``stop`` are
    answered promptly instead of blocking for the child's whole runtime.
  * The active dispatch records the controller identity and, once spawned, the
    child process identity: pid + Linux starttime + boot id + process group.
    ``os.kill(pid, 0)`` alone only tests existence and is never treated as
    identity.
  * ``pause``/``stop`` block further dispatch, fence the active claim, and
    terminate only the recorded child process group after an identity check.
    A child that finishes later cannot resurrect paused/stopped state.
  * A timeout is captured (never an unbound local), the child is terminated or
    killed with bounded waits, reaped, and persisted as a failed dispatch.
  * An acknowledgement is accepted only when the exit code is zero, the
    process/CLI effect line is present, the ack matches
    run+event+session+fence+task hash, and any expected marker effect exists.
    Unknown or failed dispatches can never become success.

Scope: sequential parent-exit-before-child-continuation only. No host-reboot,
hangup, or simultaneous-death survival claim is made anywhere.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

SCHEMA_VERSION = 2
DEFAULT_STATE_ROOT = ".tmp/opencode-lifecycle"
ACTIVE_STATES = {"initialized", "parent_registered", "parent_exited", "child_registered", "paused"}
TERMINAL_STATES = {"stopped"}
EFFECT_LINE = "OF_FIXTURE_EFFECT"
ACK_TEMPLATE = (
    "This is a bounded lifecycle fixture, not a production task. Read the durable "
    "task file {task_file} first. Then perform exactly this bounded fixture action: "
    "{action}. Write the JSON acknowledgement {ack_file} with keys run_id, event_id, "
    "session_id, fence, task_file_sha256, observed_at, action_result. "
    "The required values are run_id={run_id}; event_id={event_id}; "
    "session_id={session_id}; fence={fence}; task_file_sha256={task_file_sha256}. "
    "{marker_instructions}"
    "Print one line to stdout in exactly this form: {effect_line}. "
    "Do not register, download data, evaluate, or submit signals."
)


class BridgeError(RuntimeError):
    """Raised for explicit, actionable lifecycle refusals."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_due(value: str) -> float:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError as exc:
        raise BridgeError(f"invalid due time {value!r}; use ISO-8601, for example 2026-09-23T12:00:00Z") from exc


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# process identity (pid existence is not identity)
# ---------------------------------------------------------------------------

def _boot_id() -> str:
    path = Path("/proc/sys/kernel/random/boot_id")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown-boot"


def _starttime(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    tail = raw.rsplit(")", 1)[-1].split()
    if len(tail) < 20:
        return None
    try:
        return int(tail[19])
    except (IndexError, ValueError):
        return None


def process_identity(pid: int | None) -> dict | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    starttime = _starttime(pid)
    if starttime is None:
        return {"pid": pid, "boot_id": _boot_id(), "starttime": None, "pgid": None, "alive_probe": False}
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    return {"pid": pid, "boot_id": _boot_id(), "starttime": starttime, "pgid": pgid, "alive_probe": True}


def identity_alive(identity: dict | None) -> bool:
    """True only when the recorded process identity still matches this pid."""
    if not identity or not isinstance(identity.get("pid"), int):
        return False
    pid = identity["pid"]
    if identity.get("boot_id") != _boot_id():
        return False
    current = _starttime(pid)
    if current is None:
        return False
    if identity.get("starttime") is not None and current != identity["starttime"]:
        return False
    return True


def identity_summary(identity: dict | None) -> str:
    if not identity:
        return "none"
    return f"pid={identity.get('pid')} starttime={identity.get('starttime')} boot={identity.get('boot_id')}"


# ---------------------------------------------------------------------------
# record sanitization (public artifacts only; raw events stay in private scratch)
# ---------------------------------------------------------------------------

SENSITIVE_KEY_PATTERNS = (
    re.compile(r"^usage($|_)", re.I),
    re.compile(r"(^|_)usage$", re.I),
    re.compile(r"^cost($|_)", re.I),
    re.compile(r"(^|_)cost$", re.I),
    re.compile(r"^tokens?($|_)", re.I),
    re.compile(r"(^|_)tokens?$", re.I),
    re.compile(r"^price($|_)", re.I),
    re.compile(r"credit|balance|api[_-]?key|authorization|secret", re.I),
)

SECRET_SHAPE_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "<REDACTED-PRIVATE-KEY>"),
    (re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b"), "<REDACTED-GITHUB-TOKEN>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<REDACTED-API-KEY>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "<REDACTED-JWT>"),
    (re.compile(r"(?i)\bAuthorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._-]{8,}"), "Authorization: Bearer <REDACTED>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<REDACTED-AWS-KEY>"),
    (re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])"), "<REDACTED-HEX64>"),
)


def is_sensitive_key(key) -> bool:
    return any(pattern.search(str(key)) for pattern in SENSITIVE_KEY_PATTERNS)


def redact_secret_shapes(text: str) -> str:
    """Redact credential-shaped substrings from agent-derived text."""
    redacted = text
    for pattern, replacement in SECRET_SHAPE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_value(value, replacements: list[tuple[str, str]]):
    """Key-filter and path-replace a structured record.

    ``replacements`` must be ordered most-specific path first (workspace,
    state root, repository root, home). Agent-derived strings must already be
    passed through ``redact_secret_shapes`` before they are embedded; this
    function does not redact credential shapes so that real content hashes
    survive.
    """
    if isinstance(value, dict):
        return {
            key: sanitize_value(item, replacements)
            for key, item in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [sanitize_value(item, replacements) for item in value]
    if isinstance(value, str):
        text = value
        for needle, replacement in replacements:
            if needle:
                text = text.replace(needle, replacement)
        return text
    return value


def plain_text_tail(output: str, limit: int = 400) -> str:
    """Return only non-JSON stdout lines, sanitized, for the public record."""
    lines = [line for line in output.splitlines() if line.strip() and not line.lstrip().startswith(("{", "["))]
    return redact_secret_shapes("\n".join(lines)[-limit:])


def terminate_identity_checked(identity: dict | None, grace_seconds: float = 5.0) -> dict:
    """Terminate then kill only the recorded process group, identity checked."""
    result = {"attempted": False, "terminated": False, "killed": False, "reason": None}
    if not identity:
        result["reason"] = "no recorded identity; refusing to signal"
        return result
    if not identity_alive(identity):
        result["reason"] = "recorded identity is not alive; refusing to signal a reused pid"
        return result
    result["attempted"] = True
    pgid = identity.get("pgid")
    target = -pgid if isinstance(pgid, int) and pgid > 0 else identity["pid"]
    try:
        os.kill(target, signal.SIGTERM)
        result["terminated"] = True
    except ProcessLookupError:
        result["reason"] = "process already exited"
        return result
    except OSError as exc:
        result["reason"] = f"SIGTERM failed: {exc}"
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not identity_alive(identity):
            return result
        time.sleep(0.05)
    if identity_alive(identity):
        try:
            os.kill(target, signal.SIGKILL)
            result["killed"] = True
        except OSError as exc:
            result["reason"] = f"SIGKILL failed: {exc}"
    return result


# ---------------------------------------------------------------------------
# run state
# ---------------------------------------------------------------------------

class RunState:
    def __init__(self, state_root: Path, run_id: str):
        if not run_id or "/" in run_id or run_id in {".", ".."}:
            raise BridgeError(f"invalid run id {run_id!r}")
        self.run_id = run_id
        self.dir = Path(state_root) / run_id
        self.state_path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        self.lock_path = self.dir / "run.lock"
        self.acks_dir = self.dir / "acks"

    def _atomic_write(self, path: Path, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def exists(self) -> bool:
        return self.state_path.is_file()

    def load(self) -> dict:
        if not self.exists():
            raise BridgeError(f"run {self.run_id!r} does not exist under {self.dir.parent}")
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        state["updated_at"] = utc_now()
        self._atomic_write(self.state_path, state)

    def append_event(self, event: dict) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def events(self) -> list[dict]:
        if not self.events_path.is_file():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def require_state(self, state: dict, allowed: set[str], action: str) -> None:
        if state["status"] in TERMINAL_STATES:
            raise BridgeError(f"{action} refused: run {self.run_id!r} is {state['status']}")
        if state["status"] not in allowed:
            raise BridgeError(
                f"{action} refused: run {self.run_id!r} is {state['status']!r}, expected one of {sorted(allowed)}"
            )


# ---------------------------------------------------------------------------
# dispatch engine
# ---------------------------------------------------------------------------

def _due_event(state: dict) -> dict | None:
    pending = [event for event in state["queued"] if event["status"] == "queued"]
    if not pending:
        return None
    return min(pending, key=lambda item: item["due_at_epoch"])


def claim_due_event(run: RunState, max_wait_seconds: float | None = None) -> tuple[dict | None, dict]:
    """Wait for the earliest due queued event and claim it under a short lock.

    Returns (event, info). ``event`` is None when the scheduler should stop
    (terminal/paused state or no queued work).
    """
    deadline = None if max_wait_seconds is None else time.time() + max_wait_seconds
    while True:
        with run.lock():
            state = run.load()
            if state["status"] in TERMINAL_STATES or state["status"] == "paused":
                return None, {"stop": state["status"]}
            event = _due_event(state)
            if event is None:
                return None, {"stop": "no-queued-events"}
            now = time.time()
            wait = event["due_at_epoch"] - now
            if wait <= 0:
                # Re-check under this same lock: the event is still queued and is now claimed.
                live = next((item for item in state["queued"] if item["event_id"] == event["event_id"]), None)
                if live is None or live["status"] != "queued":
                    continue
                fence = int(state["fencing_token"]) + 1
                state["fencing_token"] = fence
                live["status"] = "claimed"
                live["claimed_at"] = utc_now()
                live["fence"] = fence
                state["active"] = {
                    "event_id": live["event_id"],
                    "fence": fence,
                    "claimed_at": utc_now(),
                    "controller": process_identity(os.getpid()),
                    "child": None,
                    "cancel_requested": None,
                }
                run.save(state)
                run.append_event({"at": utc_now(), "type": "event_claimed", "event_id": live["event_id"], "fence": fence})
                return dict(live), {"fence": fence}
        if deadline is not None and time.time() >= deadline:
            return None, {"stop": "wait-timeout"}
        time.sleep(min(max(wait, 0.05), 5.0))


def _cancel_flag(run: RunState, event_id: str, fence: int) -> str | None:
    with run.lock():
        state = run.load()
        active = state.get("active")
        if state["status"] in TERMINAL_STATES:
            return "stopped"
        if state["status"] == "paused":
            return "paused"
        if not active or active.get("event_id") != event_id or active.get("fence") != fence:
            return "fenced"
        return active.get("cancel_requested")


def _validate_effects(run: RunState, state: dict, event: dict, fence: int, executor_argv: list[str],
                      exit_code: int | None, output: str) -> tuple[str, dict]:
    detail: dict = {}
    if exit_code != 0:
        detail["exit_code"] = exit_code
        return "failed_exit", detail
    effect_token = f"{EFFECT_LINE}:{event['event_id']}:{state['child_session_identity']}:{fence}"
    if effect_token not in output:
        detail["expected_effect_line"] = effect_token
        return "unverified_no_process_evidence", detail
    ack_file = (run.acks_dir / f"{event['event_id']}.json").resolve()
    if not ack_file.is_file():
        detail["ack_file"] = str(ack_file)
        return "unverified_no_ack", detail
    try:
        ack = json.loads(ack_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        detail["ack_error"] = str(exc)
        return "unverified_bad_ack", detail
    detail["ack"] = ack
    task_file = Path(event["task_file"])
    task_hash = sha256_text(task_file.read_text(encoding="utf-8")) if task_file.is_file() else "missing"
    expected = {
        "run_id": run.run_id,
        "event_id": event["event_id"],
        "session_id": state["child_session_identity"],
        "fence": fence,
        "task_file_sha256": task_hash,
    }
    mismatches = {key: {"expected": value, "observed": ack.get(key)} for key, value in expected.items() if ack.get(key) != value}
    if mismatches:
        detail["ack_mismatches"] = mismatches
        return "unverified_ack_mismatch", detail
    marker_path = event.get("marker_path")
    if marker_path:
        marker = Path(event["workspace"]) / marker_path
        expected_marker = f"{run.run_id}:{event['event_id']}:{state['child_session_identity']}:{fence}"
        observed_marker = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
        detail["marker"] = {"path": str(marker), "expected": expected_marker, "observed": observed_marker}
        if observed_marker != expected_marker:
            return "unverified_marker_mismatch", detail
    detail["executor_argv"] = executor_argv
    return "acknowledged", detail


def dispatch_once(run: RunState, event: dict, fence: int, executor: list[str], ack_wait_seconds: float) -> dict:
    """Run one claimed event outside the lock; finalize under a short lock."""
    state = run.load()
    cancel = _cancel_flag(run, event["event_id"], fence)
    if cancel:
        return _finalize(run, event, fence, None, None, "", cancel_override=cancel)

    task_file = Path(event["task_file"])
    task_hash = sha256_text(task_file.read_text(encoding="utf-8")) if task_file.is_file() else "missing"
    ack_file = (run.acks_dir / f"{event['event_id']}.json").resolve()
    effect_token = f"{EFFECT_LINE}:{event['event_id']}:{state['child_session_identity']}:{fence}"
    marker_instructions = ""
    if event.get("marker_path"):
        marker_content = f"{run.run_id}:{event['event_id']}:{state['child_session_identity']}:{fence}"
        marker_instructions = (
            f"Also create the file {event['marker_path']} in the workspace "
            f"containing exactly: {marker_content}. "
        )
    prompt = ACK_TEMPLATE.format(
        task_file=task_file,
        action=event["action"],
        ack_file=ack_file,
        run_id=run.run_id,
        event_id=event["event_id"],
        session_id=state["child_session_identity"],
        fence=fence,
        task_file_sha256=task_hash,
        marker_instructions=marker_instructions,
        effect_line=effect_token,
    )
    argv = [*executor, prompt]
    started = utc_now()
    output = ""
    proc = None
    exit_code: int | None = None
    try:
        run.acks_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            argv,
            cwd=event["workspace"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        child_identity = process_identity(proc.pid)
        with run.lock():
            state = run.load()
            active = state.get("active")
            if active and active.get("event_id") == event["event_id"] and active.get("fence") == fence:
                active["child"] = child_identity
                active["executor_argv"] = argv
                run.save(state)
        cancel = _cancel_flag(run, event["event_id"], fence)
        if cancel:
            terminate_identity_checked(child_identity, grace_seconds=3.0)
            try:
                output = proc.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                pass
            return _finalize(run, event, fence, proc.pid, proc.returncode, output or "",
                             cancel_override=cancel, argv=argv)

        try:
            output = proc.communicate(timeout=ack_wait_seconds)[0]
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = ""
            kill_result = terminate_identity_checked(child_identity, grace_seconds=5.0)
            try:
                output = proc.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    output = proc.communicate(timeout=5)[0]
                except subprocess.TimeoutExpired:
                    output = ""
            exit_code = proc.returncode
            output = (output or "") + f"\nfixture: dispatch timed out after {ack_wait_seconds}s; {kill_result}"
    except OSError as exc:
        output = f"fixture: failed to start executor: {exc}"
        exit_code = None

    return _finalize(run, event, fence, proc.pid if proc else None, exit_code, output or "",
                     started=started, argv=argv)


def _finalize(run: RunState, event: dict, fence: int, pid: int | None, exit_code: int | None,
              output: str, cancel_override: str | None = None, started: str | None = None,
              argv: list[str] | None = None) -> dict:
    with run.lock():
        state = run.load()
        active = state.get("active")
        cancel = cancel_override
        if cancel is None:
            if state["status"] in TERMINAL_STATES:
                cancel = "stopped"
            elif state["status"] == "paused":
                cancel = "paused"
            elif not active or active.get("event_id") != event["event_id"] or active.get("fence") != fence:
                cancel = "fenced"
            else:
                cancel = active.get("cancel_requested")

        if cancel:
            dispatch_status = f"cancelled_by_{cancel}" if cancel in {"paused", "stopped"} else "cancelled_by_fence"
            detail = {"cancel": cancel}
        else:
            dispatch_status, detail = _validate_effects(run, state, event, fence,
                                                        list(argv or (active or {}).get("executor_argv") or []),
                                                        exit_code, output)

        record = {
            "event_id": event["event_id"],
            "fence": fence,
            "session_id": state["child_session_identity"],
            "argv": list(argv or (active or {}).get("executor_argv") or []),
            "pid": pid,
            "started_at": started or utc_now(),
            "ended_at": utc_now(),
            "exit_code": exit_code,
            "dispatch_status": dispatch_status,
            "detail": detail,
            "output_tail": output[-600:] if output else "",
        }
        for item in state["queued"]:
            if item["event_id"] == event["event_id"] and item.get("fence") == fence:
                item["status"] = dispatch_status
        state["dispatches"].append(record)
        if active and active.get("event_id") == event["event_id"] and active.get("fence") == fence:
            state["active"] = None
        if not cancel and dispatch_status == "acknowledged" and state["status"] == "child_registered":
            pass
        run.save(state)
        run.append_event(
            {"at": utc_now(), "type": "event_dispatched", "event_id": event["event_id"], "fence": fence,
             "dispatch_status": dispatch_status, "exit_code": exit_code, "pid": pid,
             "session": state["child_session_identity"]}
        )
        return record


def schedule(run: RunState, executor: list[str], max_dispatches: int, ack_wait: float,
             max_wait_seconds: float | None = None) -> dict:
    dispatched = 0
    outcomes = []
    while dispatched < max_dispatches:
        event, info = claim_due_event(run, max_wait_seconds=max_wait_seconds)
        if event is None:
            state = run.load()
            return {"dispatched": dispatched, "final_status": state["status"], "note": info.get("stop"),
                    "outcomes": outcomes}
        record = dispatch_once(run, event, info["fence"], executor, ack_wait)
        outcomes.append({"event_id": record["event_id"], "status": record["dispatch_status"]})
        dispatched += 1
    state = run.load()
    return {"dispatched": dispatched, "final_status": state["status"], "outcomes": outcomes}


def cancel_active(run: RunState) -> dict:
    """Fence and cancel the active dispatch; terminate only its recorded child."""
    with run.lock():
        state = run.load()
        active = state.get("active")
        if not active:
            return {"cancelled": False, "reason": "no active dispatch"}
        active["cancel_requested"] = "stopped" if state["status"] in TERMINAL_STATES else "paused"
        state["fencing_token"] += 1
        run.save(state)
        child = active.get("child")
        controller = active.get("controller")
        event_id = active["event_id"]
    termination = terminate_identity_checked(child, grace_seconds=3.0) if identity_alive(child) else {
        "attempted": False, "reason": "no live child identity recorded; nothing to signal"}
    if identity_alive(controller) and not identity_alive(child):
        termination["controller_alive"] = True
    with run.lock():
        state = run.load()
        if state.get("active") and state["active"].get("event_id") == event_id:
            state["active"]["cancel_applied_at"] = utc_now()
            state["active"]["termination"] = termination
            run.save(state)
        run.append_event({"at": utc_now(), "type": "active_cancelled", "event_id": event_id,
                          "termination": termination})
    return {"cancelled": True, "event_id": event_id, "termination": termination}


def recover(run: RunState, event_id: str) -> dict:
    """Re-queue an unacknowledged event after proving the original owner is dead.

    The stale active claim is cleared automatically once its recorded
    identities are proven dead; users are never told to delete a lease by hand.
    """
    proof: dict = {"identity_checks": []}
    with run.lock():
        state = run.load()
        if state["status"] in TERMINAL_STATES:
            raise BridgeError("recover refused: run is stopped; terminal outcomes never reactivate")
        active = state.get("active")
        if active:
            for label in ("controller", "child"):
                identity = active.get(label)
                alive = identity_alive(identity)
                proof["identity_checks"].append({"role": label, "identity": identity, "alive": alive,
                                                 "summary": identity_summary(identity)})
            if any(item["alive"] for item in proof["identity_checks"]):
                raise BridgeError(
                    "recover refused: the recorded original owner is still alive for this run"
                )
            proof["stale_owner_cleared"] = True
            state["active"] = None
        else:
            proof["active_claim"] = "none"
        target = next((item for item in state["queued"] if item["event_id"] == event_id), None)
        if target is None:
            raise BridgeError(f"recover refused: unknown event id {event_id!r}")
        if target.get("status") == "acknowledged":
            raise BridgeError(f"recover refused: event {event_id!r} is already acknowledged")
        prior = target["status"]
        target["status"] = "queued"
        target["attempts"] = target.get("attempts", 0) + 1
        state["fencing_token"] += 1
        if state["status"] == "paused":
            state["status"] = "child_registered"
            state["paused_at"] = None
        run.save(state)
        run.append_event({"at": utc_now(), "type": "recovered", "event_id": event_id,
                          "prior_status": prior, "attempt": target["attempts"],
                          "fencing_token": state["fencing_token"], "stale_proof": proof})
        return {"state": state, "recovery_proof": proof}


def diagnose(run: RunState) -> dict:
    with run.lock():
        state = run.load()
    active = state.get("active")
    last = state["dispatches"][-1] if state["dispatches"] else None
    identity_report = None
    if active:
        identity_report = {
            "controller": {"identity": active.get("controller"), "alive": identity_alive(active.get("controller"))},
            "child": {"identity": active.get("child"), "alive": identity_alive(active.get("child"))},
        }
    if state["status"] in TERMINAL_STATES:
        classification = "stopped"
    elif state["status"] == "paused":
        classification = "paused"
    elif active and any(item["alive"] for item in identity_report.values()):
        classification = "live"
    elif active:
        classification = "crashed_stale_active"
    elif last and last["dispatch_status"] == "acknowledged":
        classification = "idle"
    elif last and last["dispatch_status"] in {"failed_exit", "unverified_no_process_evidence", "unverified_no_ack"}:
        classification = "crashed"
    elif last and last["dispatch_status"].startswith("unverified"):
        classification = "unverified"
    else:
        classification = "idle"
    return {
        "run_id": run.run_id,
        "status": state["status"],
        "classification": classification,
        "active": active,
        "identity_report": identity_report,
        "last_dispatch": last,
        "fencing_token": state["fencing_token"],
    }
