#!/usr/bin/env python3
"""External OpenCode lifecycle supervisor example: CLI over ``bridge_core``.

Scope of the demonstration (declare this whenever citing observations):
  * it proves sequential parent-exit-before-child-continuation only;
  * it does NOT prove surviving forced simultaneous parent death, terminal
    hangup, host reboot, or process-group kill;
  * it never registers, downloads data, evaluates, or submits signals;
  * the default fixture only writes an acknowledgement marker.

Commands:
  init                create run state (refuses an existing run id)
  register-parent     record the finished parent CLI session identity
  parent-exit         mark the parent exited (required before a child)
  register-child      record a fresh child CLI session identity
  queue               append a queued event with a real due time
  schedule            wait for due, claim under a short lock, run outside it
  pause               fence + pause; terminate only the recorded child group
  resume              resume only the exact recorded child target
  stop                cancel queued events, fence active work, refuse dispatch
  diagnose            classify live / stale-active / crashed / unverified
  recover             auto-clear a proven-dead claim, re-queue under a new fence
  status              machine-readable state summary
  observe             write observations.json (sanitized argv/process/events)
  selfcheck           deterministic inert-fixture lifecycle regression
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bridge_core import (
    DEFAULT_STATE_ROOT,
    TERMINAL_STATES,
    BridgeError,
    RunState,
    cancel_active,
    diagnose,
    parse_due,
    recover,
    schedule,
    utc_now,
)


def cmd_init(run: RunState) -> dict:
    with run.lock():
        if run.exists():
            raise BridgeError(f"run {run.run_id!r} already exists; duplicate init refused")
        run.dir.mkdir(parents=True, exist_ok=True)
        run.acks_dir.mkdir(exist_ok=True)
        state = {
            "schema_version": 2,
            "run_id": run.run_id,
            "created_at": utc_now(),
            "status": "initialized",
            "parent_session_identity": None,
            "parent_exited_at": None,
            "child_session_identity": None,
            "child_registered_at": None,
            "fencing_token": 0,
            "resume_target": None,
            "paused_at": None,
            "stopped_at": None,
            "active": None,
            "queued": [],
            "dispatches": [],
            "notes": "Example bridge state. No OpenForage worker, wallet, or network action is performed.",
        }
        run.save(state)
        run.append_event({"at": utc_now(), "type": "init", "run_id": run.run_id})
        return state


def cmd_register_parent(run: RunState, session: str) -> dict:
    with run.lock():
        state = run.load()
        run.require_state(state, {"initialized"}, "register-parent")
        state["parent_session_identity"] = session
        state["status"] = "parent_registered"
        run.save(state)
        run.append_event({"at": utc_now(), "type": "parent_registered", "session": session})
        return state


def cmd_parent_exit(run: RunState) -> dict:
    with run.lock():
        state = run.load()
        run.require_state(state, {"parent_registered"}, "parent-exit")
        state["parent_exited_at"] = utc_now()
        state["status"] = "parent_exited"
        run.save(state)
        run.append_event({"at": utc_now(), "type": "parent_exited", "session": state["parent_session_identity"]})
        return state


def cmd_register_child(run: RunState, session: str, separate: bool) -> dict:
    with run.lock():
        state = run.load()
        run.require_state(state, {"parent_exited"}, "register-child")
        if session == state["parent_session_identity"]:
            raise BridgeError("register-child refused: child must not reuse the parent session identity")
        if not separate:
            raise BridgeError(
                "register-child refused: the child must be a separate CLI invocation; "
                "pass --separate-cli-invocation to confirm --continue was not used"
            )
        state["child_session_identity"] = session
        state["child_registered_at"] = utc_now()
        state["status"] = "child_registered"
        state["fencing_token"] += 1
        run.save(state)
        run.append_event({"at": utc_now(), "type": "child_registered", "session": session,
                          "fencing_token": state["fencing_token"]})
        return state


def cmd_queue(run: RunState, event_id: str, due_at: str, action: str, task_file: str,
              workspace: str, marker_path: str | None) -> dict:
    due_epoch = parse_due(due_at)
    with run.lock():
        state = run.load()
        run.require_state(state, {"child_registered", "paused"}, "queue")
        if any(item["event_id"] == event_id for item in state["queued"]):
            raise BridgeError(f"queue refused: duplicate event id {event_id!r}")
        if not Path(task_file).is_file():
            raise BridgeError(f"queue refused: task file does not exist: {task_file}")
        event = {
            "event_id": event_id,
            "due_at": due_at,
            "due_at_epoch": due_epoch,
            "queued_at": utc_now(),
            "action": action,
            "task_file": str(Path(task_file).resolve()),
            "workspace": str(Path(workspace).resolve()),
            "marker_path": marker_path,
            "status": "queued",
        }
        state["queued"].append(event)
        run.save(state)
        run.append_event({"at": utc_now(), "type": "event_queued", "event_id": event_id, "due_at": due_at})
        return state


def cmd_pause(run: RunState) -> dict:
    with run.lock():
        state = run.load()
        run.require_state(state, {"child_registered", "parent_exited"}, "pause")
        state["resume_target"] = state["child_session_identity"]
        state["paused_at"] = utc_now()
        state["status"] = "paused"
        run.save(state)
        run.append_event({"at": utc_now(), "type": "paused", "resume_target": state["resume_target"]})
    cancellation = cancel_active(run)
    state = run.load()
    state["pause_cancellation"] = cancellation
    return state


def cmd_resume(run: RunState, session: str) -> dict:
    with run.lock():
        state = run.load()
        run.require_state(state, {"paused"}, "resume")
        if session != state["resume_target"]:
            raise BridgeError(
                f"stale-target error: resume target is {state['resume_target']!r}, refusing to resume {session!r}"
            )
        state["fencing_token"] += 1
        state["status"] = "child_registered"
        state["paused_at"] = None
        state["resume_target"] = None
        run.save(state)
        run.append_event({"at": utc_now(), "type": "resumed", "session": session,
                          "fencing_token": state["fencing_token"]})
        return state


def cmd_stop(run: RunState) -> dict:
    with run.lock():
        state = run.load()
        if state["status"] in TERMINAL_STATES:
            raise BridgeError("stop refused: run is already stopped")
        cancelled = []
        for item in state["queued"]:
            if item["status"] in {"queued", "claimed"}:
                item["status"] = "cancelled"
                cancelled.append(item["event_id"])
        state["stopped_at"] = utc_now()
        state["status"] = "stopped"
        state["fencing_token"] += 1
        run.save(state)
        run.append_event({"at": utc_now(), "type": "stopped", "cancelled_events": cancelled})
    cancellation = cancel_active(run)
    state = run.load()
    state["stop_cancellation"] = cancellation
    return state


def cmd_status(run: RunState) -> dict:
    state = run.load()
    active = state.get("active") or {}
    return {
        "run_id": run.run_id,
        "status": state["status"],
        "parent_session_identity": state["parent_session_identity"],
        "child_session_identity": state["child_session_identity"],
        "fencing_token": state["fencing_token"],
        "resume_target": state["resume_target"],
        "active": {
            "event_id": active.get("event_id"),
            "fence": active.get("fence"),
            "child": active.get("child"),
            "cancel_requested": active.get("cancel_requested"),
        } if active else None,
        "queued": [
            {"event_id": item["event_id"], "status": item["status"], "due_at": item["due_at"]}
            for item in state["queued"]
        ],
        "dispatch_count": len(state["dispatches"]),
    }


UNVERIFIED = [
    "surviving forced simultaneous parent death, terminal hangup, host reboot, or process-group kill",
    "OpenCode session identity persistence beyond the recorded session list entry",
    "native task/memory semantics beyond the re-read evidence recorded in the fixture run",
]


def cmd_observe(run: RunState) -> Path:
    state = run.load()
    observations = {
        "schema_version": 2,
        "run_id": run.run_id,
        "observed_at": utc_now(),
        "status": state["status"],
        "parent_session_identity": state["parent_session_identity"],
        "child_session_identity": state["child_session_identity"],
        "parent_exited_at": state["parent_exited_at"],
        "fencing_token": state["fencing_token"],
        "active": state.get("active"),
        "dispatches": [
            {
                "event_id": record["event_id"],
                "fence": record.get("fence"),
                "argv": record.get("argv"),
                "pid": record["pid"],
                "session_id": record["session_id"],
                "started_at": record["started_at"],
                "ended_at": record["ended_at"],
                "exit_code": record["exit_code"],
                "dispatch_status": record["dispatch_status"],
                "detail": record.get("detail"),
            }
            for record in state["dispatches"]
        ],
        "events": run.events(),
        "unverified": UNVERIFIED,
        "scope": "Sequential parent-exit-before-child-continuation only; see module docstring.",
    }
    path = run.dir / "observations.json"
    run._atomic_write(path, observations)
    return path


def _executor(argv: list[str]) -> list[str]:
    if not argv:
        raise BridgeError("--executor requires at least the opencode binary")
    if any("\x00" in part for part in argv):
        raise BridgeError("executor arguments may not contain NUL bytes")
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    parser.add_argument("--run-id", default="default")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("parent-exit")
    parent = sub.add_parser("register-parent")
    parent.add_argument("--session", required=True)
    child = sub.add_parser("register-child")
    child.add_argument("--session", required=True)
    child.add_argument("--separate-cli-invocation", action="store_true")

    queue = sub.add_parser("queue")
    queue.add_argument("--event-id", required=True)
    queue.add_argument("--due-at", required=True)
    queue.add_argument("--action", required=True)
    queue.add_argument("--task-file", required=True)
    queue.add_argument("--workspace", required=True)
    queue.add_argument("--marker-path", default=None,
                       help="optional expected marker file relative to the workspace")

    schedule_parser = sub.add_parser("schedule")
    schedule_parser.add_argument("--executor", nargs=argparse.REMAINDER, required=True,
                                 help="full command prefix, including --dir/--session/--format for a real run; "
                                      "must be the last schedule option")
    schedule_parser.add_argument("--max-dispatches", type=int, default=10)
    schedule_parser.add_argument("--ack-wait-seconds", type=float, default=600.0)
    schedule_parser.add_argument("--max-wait-seconds", type=float, default=None)

    sub.add_parser("pause")
    resume = sub.add_parser("resume")
    resume.add_argument("--session", required=True)
    sub.add_parser("stop")
    sub.add_parser("diagnose")
    recover_parser = sub.add_parser("recover")
    recover_parser.add_argument("--event-id", required=True)
    sub.add_parser("status")
    sub.add_parser("observe")
    sub.add_parser("selfcheck")

    args = parser.parse_args(argv)
    if args.command == "selfcheck":
        from bridge_selfcheck import run_selfcheck
        return run_selfcheck()

    run = RunState(Path(args.state_root), args.run_id)
    try:
        if args.command == "init":
            result = cmd_init(run)
        elif args.command == "register-parent":
            result = cmd_register_parent(run, args.session)
        elif args.command == "parent-exit":
            result = cmd_parent_exit(run)
        elif args.command == "register-child":
            result = cmd_register_child(run, args.session, args.separate_cli_invocation)
        elif args.command == "queue":
            result = cmd_queue(run, args.event_id, args.due_at, args.action, args.task_file,
                               args.workspace, args.marker_path)
        elif args.command == "schedule":
            result = schedule(run, _executor(args.executor), args.max_dispatches,
                              args.ack_wait_seconds, args.max_wait_seconds)
        elif args.command == "pause":
            result = cmd_pause(run)
        elif args.command == "resume":
            result = cmd_resume(run, args.session)
        elif args.command == "stop":
            result = cmd_stop(run)
        elif args.command == "diagnose":
            result = diagnose(run)
        elif args.command == "recover":
            result = recover(run, args.event_id)
        elif args.command == "status":
            result = cmd_status(run)
        elif args.command == "observe":
            result = {"observations": str(cmd_observe(run))}
        else:
            return 2
        print(json.dumps(result, indent=2, default=str))
        return 0
    except BridgeError as exc:
        print(f"FAIL: {exc}")
        return 1
    except RuntimeError as exc:  # e.g. subprocess timeout is not expected here
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
