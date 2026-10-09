#!/usr/bin/env python3
"""Deterministic inert-fixture regressions for ``bridge_core``.

Every executor here is a generated inert subprocess. No OpenCode session,
wallet, network call, or OpenForage worker is involved. These regressions
prove the repaired behaviors the packet requires:

  * pause and stop are answered promptly while a child is running (the run
    lock is not held across the child's runtime);
  * a cancelled child cannot resurrect paused/stopped state;
  * a dispatch timeout is captured, terminated, reaped, and persisted as a
    failure (never an unbound-local crash, never success);
  * unknown acknowledgements, failed exits, and marker mismatches never
    become success;
  * two schedulers cannot replay the same tick;
  * recovery auto-clears a proof-dead active claim and re-queues under a new
    fence without telling the user to delete a lease by hand.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from bridge_core import (
    BridgeError,
    RunState,
    diagnose,
    process_identity,
    recover,
    schedule,
)

INERT_CODE = r'''
import json, re, sys, time
prompt = sys.argv[1]
fields = dict(re.findall(r"(run_id|event_id|session_id|fence|task_file_sha256)=([^;\s]+)", prompt))
fields = {key: value.rstrip(".,;") for key, value in fields.items()}
ack = re.search(r"acknowledgement (\S+) with keys", prompt)
ack_path = ack.group(1).rstrip(".")
marker = re.search(r"create the file (\S+) in the workspace containing exactly: (\S+)", prompt)
effect = re.search(r"exactly this form: (\S+)", prompt)
mode = "{mode}"
if mode == "sleep":
    time.sleep(float("{sleep_seconds}"))
if mode == "no_ack":
    if effect:
        print(effect.group(1))
    sys.exit(0)
if mode == "fail":
    sys.exit(3)
data = {
    "run_id": fields.get("run_id"),
    "event_id": fields.get("event_id"),
    "session_id": fields.get("session_id"),
    "fence": int(fields.get("fence", "0")),
    "task_file_sha256": fields.get("task_file_sha256"),
    "observed_at": "inert-fixture",
    "action_result": "ok",
}
open(ack_path, "w", encoding="utf-8").write(json.dumps(data))
if marker:
    Path = __import__("pathlib").Path
    Path(marker.group(1)).write_text(marker.group(2).rstrip(".") + "\n", encoding="utf-8")
if effect:
    print(effect.group(1))
if mode == "sleep_after_ack":
    time.sleep(float("{sleep_seconds}"))
'''


def inert_executor(workspace: Path, mode: str = "ok", sleep_seconds: float = 30.0) -> list[str]:
    code = INERT_CODE.replace("{mode}", mode).replace("{sleep_seconds}", str(sleep_seconds))
    wrapper = workspace / f"_inert_{mode}.py"
    wrapper.write_text(code, encoding="utf-8")
    return [sys.executable, str(wrapper)]


def _new_run(root: Path, name: str, workspace: Path, task: Path, separate: bool = True) -> RunState:
    from opencode_bridge import cmd_init, cmd_register_parent, cmd_parent_exit, cmd_register_child
    run = RunState(root / "state", name)
    cmd_init(run)
    cmd_register_parent(run, f"parent-{name}")
    cmd_parent_exit(run)
    cmd_register_child(run, f"child-{name}", separate)
    return run


def _queue(run: RunState, event_id: str, task: Path, workspace: Path, marker: str | None = None) -> dict:
    from opencode_bridge import cmd_queue
    from bridge_core import utc_now
    return cmd_queue(run, event_id, utc_now(), "inert fixture action", str(task), str(workspace), marker)


def run_selfcheck() -> int:
    failures: list[str] = []
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="of-bridge-selfcheck-") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        workspace.mkdir()
        task = workspace / "marker_task.md"
        task.write_text("# Durable fixture task\n\nWrite the acknowledgement.\n", encoding="utf-8")

        from opencode_bridge import cmd_init, cmd_pause, cmd_resume, cmd_stop

        # ---- lifecycle refusals -------------------------------------------
        run = RunState(root / "state", "refusals")
        cmd_init(run)
        try:
            cmd_init(run)
            failures.append("duplicate init was not refused")
        except BridgeError:
            checks.append("duplicate-init-refused")
        from opencode_bridge import cmd_register_parent, cmd_parent_exit, cmd_register_child
        cmd_register_parent(run, "parent-refusals")
        try:
            cmd_register_child(run, "child-refusals", separate=True)
            failures.append("child registration before parent exit was not refused")
        except BridgeError:
            checks.append("child-before-parent-exit-refused")
        cmd_parent_exit(run)
        try:
            cmd_register_child(run, "parent-refusals", separate=True)
            failures.append("child reuse of the parent session was not refused")
        except BridgeError:
            checks.append("parent-session-reuse-refused")
        cmd_register_child(run, "child-refusals", separate=True)

        # ---- acknowledged dispatch with marker effect ---------------------
        _queue(run, "evt-ok", task, workspace, marker="marker-ok.txt")
        try:
            _queue(run, "evt-ok", task, workspace)
            failures.append("duplicate event id was not refused")
        except BridgeError:
            checks.append("duplicate-event-refused")
        record = schedule(run, inert_executor(workspace), 1, 10, max_wait_seconds=5)["outcomes"]
        state = run.load()
        last = state["dispatches"][-1]
        if last["dispatch_status"] != "acknowledged" or not (workspace / "marker-ok.txt").is_file():
            failures.append(f"acknowledged dispatch with marker effect failed: {last['dispatch_status']}")
        else:
            checks.append("marker-effect-acknowledged")
        if state["active"] is not None:
            failures.append("active claim was not cleared after dispatch")

        # ---- failure modes never become success ---------------------------
        for mode, expected_status, label in (
            ("fail", "failed_exit", "failed-exit-fails"),
            ("no_ack", "unverified_no_ack", "unknown-ack-fails"),
        ):
            _queue(run, f"evt-{mode}", task, workspace)
            schedule(run, inert_executor(workspace, mode=mode), 1, 10, max_wait_seconds=5)
            status = run.load()["dispatches"][-1]["dispatch_status"]
            if status != expected_status:
                failures.append(f"{label}: expected {expected_status}, got {status}")
            else:
                checks.append(label)

        # ---- timeout is captured, terminated, reaped, persisted -----------
        _queue(run, "evt-timeout", task, workspace)
        started = time.time()
        schedule(run, inert_executor(workspace, mode="sleep", sleep_seconds=30), 1, 1.0, max_wait_seconds=5)
        elapsed = time.time() - started
        last = run.load()["dispatches"][-1]
        if last["dispatch_status"] != "failed_exit":
            failures.append(f"timeout dispatch status was {last['dispatch_status']!r}, expected failed_exit")
        elif elapsed > 20:
            failures.append(f"timeout handling did not terminate/reap boundedly ({elapsed:.1f}s)")
        else:
            checks.append("timeout-terminated-and-failed")
        if last["exit_code"] == 0:
            failures.append("timed-out child reported exit code 0")

        # ---- prompt pause while a child is running ------------------------
        _queue(run, "evt-pause", task, workspace)
        thread = threading.Thread(
            target=schedule,
            args=(run, inert_executor(workspace, mode="sleep", sleep_seconds=30), 1, 30.0),
            kwargs={"max_wait_seconds": 5},
            daemon=True,
        )
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            state = run.load()
            if (state.get("active") or {}).get("child"):
                break
            time.sleep(0.05)
        t0 = time.time()
        cmd_pause(run)
        pause_elapsed = time.time() - t0
        thread.join(timeout=20)
        status = run.load()["dispatches"][-1]["dispatch_status"]
        if pause_elapsed > 2.0:
            failures.append(f"pause blocked for {pause_elapsed:.2f}s while the child was running")
        elif status != "cancelled_by_paused":
            failures.append(f"paused dispatch status was {status!r}, expected cancelled_by_paused")
        else:
            checks.append("pause-prompt-and-fenced")
        try:
            cmd_resume(run, "other-child")
            failures.append("resume of a non-target session was not refused")
        except BridgeError:
            checks.append("stale-target-resume-refused")
        cmd_resume(run, "child-refusals")
        checks.append("exact-target-resume")

        # ---- prompt stop while a child is running -------------------------
        _queue(run, "evt-stop", task, workspace)
        thread = threading.Thread(
            target=schedule,
            args=(run, inert_executor(workspace, mode="sleep", sleep_seconds=30), 1, 30.0),
            kwargs={"max_wait_seconds": 5},
            daemon=True,
        )
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            state = run.load()
            if (state.get("active") or {}).get("child"):
                break
            time.sleep(0.05)
        t0 = time.time()
        cmd_stop(run)
        stop_elapsed = time.time() - t0
        thread.join(timeout=20)
        status = run.load()["dispatches"][-1]["dispatch_status"]
        if stop_elapsed > 2.0:
            failures.append(f"stop blocked for {stop_elapsed:.2f}s while the child was running")
        elif status != "cancelled_by_stopped":
            failures.append(f"stopped dispatch status was {status!r}, expected cancelled_by_stopped")
        else:
            checks.append("stop-prompt-and-fenced")
        try:
            _queue(run, "evt-after-stop", task, workspace)
            failures.append("queue after stop was not refused")
        except BridgeError:
            checks.append("queue-after-stop-refused")

        # ---- crash classification and proof-based recovery ----------------
        crash_run = _new_run(root, "crash", workspace, task)
        _queue(crash_run, "evt-crash", task, workspace)
        schedule(crash_run, inert_executor(workspace, mode="no_ack"), 1, 10, max_wait_seconds=5)
        diagnosis = diagnose(crash_run)
        if diagnosis["classification"] != "crashed":
            failures.append(f"crash classified as {diagnosis['classification']!r}")
        else:
            checks.append("crash-detected")
        recovery = recover(crash_run, "evt-crash")
        if not (recovery["recovery_proof"].get("stale_owner_cleared") or recovery["recovery_proof"].get("active_claim") == "none"):
            failures.append("recovery did not prove and clear the dead owner record")
        else:
            checks.append("recovery-auto-clears-dead-owner")
        state = crash_run.load()
        target = next(item for item in state["queued"] if item["event_id"] == "evt-crash")
        if target["status"] != "queued" or state["fencing_token"] < 3:
            failures.append("recover did not requeue under an advanced fence")

        # ---- stale active after a killed controller -----------------------
        stale_run = _new_run(root, "stale", workspace, task)
        _queue(stale_run, "evt-stale", task, workspace)
        with stale_run.lock():
            state = stale_run.load()
            event = state["queued"][0]
            state["fencing_token"] += 1
            event["status"] = "claimed"
            event["fence"] = state["fencing_token"]
            state["active"] = {
                "event_id": event["event_id"],
                "fence": state["fencing_token"],
                "claimed_at": "fixture",
                "controller": process_identity(424242) or {"pid": 424242, "boot_id": "x", "starttime": None},
                "child": {"pid": 424243, "boot_id": "y", "starttime": 1},
                "cancel_requested": None,
            }
            stale_run.save(state)
        diagnosis = diagnose(stale_run)
        if diagnosis["classification"] != "crashed_stale_active":
            failures.append(f"stale active classified as {diagnosis['classification']!r}")
        else:
            checks.append("stale-active-classified")
        recover(stale_run, "evt-stale")
        checks.append("stale-active-recovered-without-manual-delete")

        # ---- two schedulers cannot replay one tick ------------------------
        race_run = _new_run(root, "race", workspace, task)
        _queue(race_run, "evt-race", task, workspace)
        outcomes: list[dict] = []

        def competing():
            outcomes.append(schedule(race_run, inert_executor(workspace, mode="sleep_after_ack", sleep_seconds=1),
                                     1, 10, max_wait_seconds=5))

        threads = [threading.Thread(target=competing) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        dispatches = race_run.load()["dispatches"]
        acknowledged = [item for item in dispatches if item["dispatch_status"] == "acknowledged"]
        if len(acknowledged) != 1:
            failures.append(f"two schedulers produced {len(acknowledged)} acknowledged dispatches for one tick")
        else:
            checks.append("single-scheduler-tick-claimed-once")

        # ---- observations declare scope -----------------------------------
        from opencode_bridge import cmd_observe
        data = json.loads(cmd_observe(race_run).read_text(encoding="utf-8"))
        if not data.get("unverified"):
            failures.append("observations did not declare the unverified scope")
        else:
            checks.append("observations-declare-unverified-scope")

    if failures:
        for failure in failures:
            print(f"FAIL: selfcheck: {failure}")
        print(f"\n{len(failures)} selfcheck failure(s)")
        return 1
    print(f"note: selfcheck: {len(checks)} lifecycle checks behaved as expected (inert fixture processes only)")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(run_selfcheck())
