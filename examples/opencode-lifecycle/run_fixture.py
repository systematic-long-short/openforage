#!/usr/bin/env python3
"""Executable end-to-end lifecycle fixture for the OpenCode bridge.

Run by an orchestrator after this session exits; it is not run by CI and it
never touches an OpenForage wallet, data directory, network, or campaign.
It launches each OpenCode process sequentially and keeps at most one OpenCode
agent alive at a time.

    python examples/opencode-lifecycle/run_fixture.py \
        --model <provider/model> --variant <label> --output .tmp/lifecycle-observations.json

Real-mode sequence:
  1. fresh parent OpenCode process (no resume flag) -> actual session id/exit
  2. bridge records the parent and its exit
  3. distinct fresh child OpenCode process (no resume flag); its first turn
     carries a one-time memory marker that is never written to the workspace
     and asks for a native task/todo item when the harness has one
  4. bridge records the child and queues event 1; a detached controller waits
     for due time and resumes only the established child session
  5. real pause: queue event 2, run a bounded scheduler while paused and
     prove zero dispatches; resume the exact child, consume event 2 in the
     same child through a second detached controller
  6. real stop: queue event 3, stop, execute a bounded scheduler past the
     event's due time, and prove no later child dispatch or marker appears
  7. inert lifecycle regressions (pause/kill/crash/timeout/duplicate) run as
     inert subprocess controls and are labeled separately from the actual
     OpenCode evidence; they never prove native crash behavior
  8. sanitized observations are written to --output. Raw event streams stay
     only in git-ignored private scratch for diagnosis.

--inert replaces the OpenCode binary with a generated inert stand-in so the
whole runner can be regression-tested without an agent or credentials. Inert
observations are labeled `evidence_class=inert-regression-only` and
`actual_native_proof=false`; never cite them as native OpenCode proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from bridge_core import (
    RunState,
    identity_alive,
    identity_summary,
    plain_text_tail,
    process_identity,
    sanitize_value,
    terminate_identity_checked,
    utc_now,
)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCOPE = ("Sequential parent-exit-before-child-continuation only. No host-reboot, hangup, or "
         "simultaneous-death survival claim. Fixture agents perform marker/acknowledgement work only; "
         "no registration, evaluation, submission, wallet, campaign, or nested-agent action. "
         "Native task/todo tool use is recorded when the harness exposes it and stays unknown otherwise.")

FAKE_OPENCODE = r'''#!/usr/bin/env python3
import json, os, random, re, sys
args = sys.argv[1:]
if args[:1] == ["session"]:
    sessions_dir = os.environ["FAKE_OPENCODE_SESSIONS"]
    ids = os.listdir(sessions_dir) if os.path.isdir(sessions_dir) else []
    print(json.dumps([{"id": item, "title": "fixture"} for item in ids]))
    sys.exit(0)
if not args or args[0] != "run":
    print("fake opencode: only `run` and `session list` are implemented", file=sys.stderr)
    sys.exit(2)
session = args[args.index("--session") + 1] if "--session" in args else None
prompt = args[-1]
sessions_dir = os.environ["FAKE_OPENCODE_SESSIONS"]
label = os.environ.get("FAKE_OPENCODE_LABEL", "agent")
if session is None:
    session = f"fixture-{label}-{random.randrange(1 << 30):08x}"
os.makedirs(sessions_dir, exist_ok=True)
history_path = os.path.join(sessions_dir, session)
if not os.path.exists(history_path):
    open(history_path, "w").write(prompt)
print(json.dumps({"type": "run", "sessionID": session, "text": "fixture acknowledgement"}))
if session and "native task item" in prompt and "--session" not in args:
    print(json.dumps({"type": "tool", "tool": "todowrite",
                      "state": {"input": {"todos": [{"content": "lifecycle-fixture"}]}}}))
fields = dict(re.findall(r"(run_id|event_id|session_id|fence|task_file_sha256)=([^;\s]+)", prompt))
fields = {key: value.rstrip(".,;") for key, value in fields.items()}
ack = re.search(r"acknowledgement (\S+) with keys", prompt)
marker = re.search(r"create the file (\S+) in the workspace containing exactly: (\S+)", prompt)
effect = re.search(r"exactly this form: (\S+)", prompt)
if ack:
    history = open(history_path, encoding="utf-8").read()
    token = re.search(r"one-time marker: ([A-Za-z0-9-]+)", history)
    data = {
        "run_id": fields.get("run_id"),
        "event_id": fields.get("event_id"),
        "session_id": fields.get("session_id"),
        "fence": int(fields.get("fence", "0")),
        "task_file_sha256": fields.get("task_file_sha256"),
        "observed_at": "inert-fixture",
        "action_result": token.group(1) if token else "no-marker",
    }
    open(ack.group(1).rstrip("."), "w", encoding="utf-8").write(json.dumps(data))
    if marker:
        open(marker.group(1), "w", encoding="utf-8").write(marker.group(2).rstrip(".") + "\n")
    if effect:
        print(effect.group(1))
sys.exit(0)
'''


class FixtureError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_opencode_argv(binary: list[str], workspace: Path, model: str | None,
                        variant: str | None, session: str | None = None) -> list[str]:
    argv = [*binary, "run", "--format", "json", "--dir", str(workspace)]
    if model:
        argv += ["--model", model]
    if variant:
        argv += ["--variant", variant]
    if session:
        argv += ["--session", session]
    return argv


def tracked_popen(tracker: list, argv: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    tracker.append({"proc": proc, "identity": process_identity(proc.pid), "argv": argv})
    return proc


def communicate_bounded(proc: subprocess.Popen, identity: dict | None, timeout: float) -> tuple[str, bool]:
    """Wait bounded; on timeout terminate/reap only the owned identity."""
    try:
        return proc.communicate(timeout=timeout)[0] or "", False
    except subprocess.TimeoutExpired:
        terminate_identity_checked(identity, grace_seconds=5.0)
        try:
            return proc.communicate(timeout=10)[0] or "", True
        except subprocess.TimeoutExpired:
            terminate_identity_checked(identity, grace_seconds=0.0)
            try:
                return proc.communicate(timeout=5)[0] or "", True
            except subprocess.TimeoutExpired:
                return "", True


def extract_session_id(output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for key in ("sessionID", "session_id", "sessionId"):
            if isinstance(event.get(key), str):
                return event[key]
    return None


def extract_native_task_events(output: str) -> list[dict]:
    """Observe (never require) native task/todo tool events in the JSON stream."""
    observed = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        tool = event.get("tool") or event.get("name")
        if isinstance(tool, str) and re.search(r"todo|task", tool, re.I):
            observed.append({"tool": tool})
    return observed


def write_scratch(path: Path, text: str) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"raw_events_scratch": str(path.relative_to(REPO_ROOT)),
            "raw_events_sha256": sha256_text(text),
            "raw_events_bytes": len(text.encode("utf-8"))}


def run_opencode(binary: list[str], prompt: str, workspace: Path, tracker: list, env: dict,
                 model: str | None, variant: str | None, session: str | None,
                 timeout_seconds: float, scratch_dir: Path, label: str) -> dict:
    argv_prefix = build_opencode_argv(binary, workspace, model, variant, session)
    proc = tracked_popen(tracker, [*argv_prefix, prompt], cwd=workspace, env=env)
    output, timed_out = communicate_bounded(proc, process_identity(proc.pid), timeout_seconds)
    record = {
        "argv": argv_prefix,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "session_id": extract_session_id(output),
        "plain_text_tail": plain_text_tail(output),
        "native_task_tool_events": extract_native_task_events(output),
    }
    record.update(write_scratch(scratch_dir / f"{label}.jsonl", output))
    return record


def parse_last_json_object(output: str) -> dict | None:
    """Decode pretty-printed or single-line JSON objects from mixed stdout."""
    decoder = json.JSONDecoder()
    found: dict | None = None
    index = 0
    while True:
        start = output.find("{", index)
        if start < 0:
            return found
        try:
            value, end = decoder.raw_decode(output[start:])
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            found = value
        index = start + max(end, 1)


def run_scheduler(run_id: str, state_root: Path, executor: list[str], tracker: list, env: dict,
                  scratch_dir: Path, label: str, max_dispatches: int, ack_wait: float,
                  max_wait: float | None, join_timeout: float,
                  wait_event: str | None = None) -> dict:
    argv = [sys.executable, str(HERE / "opencode_bridge.py"), "--state-root", str(state_root),
            "--run-id", run_id, "schedule", "--max-dispatches", str(max_dispatches),
            "--ack-wait-seconds", str(ack_wait)]
    if max_wait is not None:
        argv += ["--max-wait-seconds", str(max_wait)]
    argv += ["--executor", *executor]
    proc = tracked_popen(tracker, argv, cwd=REPO_ROOT, env=env)
    if wait_event is not None:
        deadline = time.time() + join_timeout
        while time.time() < deadline:
            state = RunState(state_root, run_id).load()
            event = next((item for item in state["queued"] if item["event_id"] == wait_event), None)
            if event and event["status"] not in {"queued", "claimed"}:
                break
            time.sleep(0.5)
    output, timed_out = communicate_bounded(proc, process_identity(proc.pid), join_timeout)
    parsed = parse_last_json_object(output)
    record = {
        "argv": argv,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "result": parsed,
        "plain_text_tail": plain_text_tail(output),
    }
    record.update(write_scratch(scratch_dir / f"{label}.jsonl", output))
    return record


def cleanup(tracker: list) -> tuple[list[dict], list[str]]:
    cleaned = []
    for item in tracker:
        proc = item["proc"]
        entry = {"pid": proc.pid, "identity": identity_summary(item["identity"]), "argv": item["argv"]}
        if proc.poll() is None:
            entry["termination"] = terminate_identity_checked(item["identity"], grace_seconds=3.0)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        entry["exit_code"] = proc.returncode
        entry["still_running"] = proc.poll() is None
        cleaned.append(entry)
    remaining = [item["proc"].pid for item in tracker if identity_alive(item["identity"])]
    return cleaned, remaining


def probe_opencode_version(binary: list[str]) -> str | None:
    try:
        proc = subprocess.run([*binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or proc.stderr).strip()
    return text.splitlines()[-1] if text else None


def marker_present(workspace: Path, marker_path: str) -> bool:
    return (workspace / marker_path).is_file()


def evaluate_evidence(observations: dict, inert: bool) -> list[str]:
    reasons: list[str] = []
    if observations.get("error"):
        reasons.append(f"runner error: {observations['error']}")
    controller = observations.get("controller_run") or {}
    if controller.get("dispatch_status") != "acknowledged":
        reasons.append("event 1 was not acknowledged by the verified child session")
    second = observations.get("second_event") or {}
    if second.get("dispatch_status") != "acknowledged":
        reasons.append("event 2 (post-resume continuation) was not acknowledged in the same child")
    child_session = (observations.get("child_bootstrap_run") or {}).get("session_id")
    if not child_session or (second.get("ack") or {}).get("session_id") != child_session:
        reasons.append("event 2 was not consumed by the same established child session")
    memory = observations.get("memory_proof") or {}
    if memory.get("ack_action_result_matches_memory_marker") is not True:
        reasons.append("session-history recall marker did not match the first child turn")
    if memory.get("memory_marker_written_to_workspace") is not False:
        reasons.append("memory marker was written to the workspace instead of session history")
    paused = observations.get("paused_tick") or {}
    paused_result = paused.get("result") or {}
    if paused_result.get("dispatched") != 0 or paused_result.get("final_status") != "paused":
        reasons.append("paused scheduler did not prove zero dispatches")
    stopped = observations.get("stopped_tick") or {}
    stopped_result = stopped.get("result") or {}
    if stopped_result.get("dispatched") != 0 or stopped_result.get("final_status") != "stopped":
        reasons.append("stopped scheduler did not prove zero dispatches")
    post_stop = observations.get("post_stop") or {}
    if post_stop.get("event_status") != "cancelled":
        reasons.append("post-stop event was not cancelled")
    if post_stop.get("marker_present") is not False:
        reasons.append("post-stop event produced a marker after stop")
    if post_stop.get("dispatch_count_changed") is not False:
        reasons.append("a dispatch was recorded after stop")
    inert_checks = observations.get("inert_lifecycle_regression") or {}
    if inert_checks.get("exit_code") != 0:
        reasons.append("inert lifecycle regression selfcheck failed")
    if not inert_checks.get("separately_labeled"):
        reasons.append("inert lifecycle regression was not labeled separately from native evidence")
    cleanup_info = observations.get("cleanup") or []
    if any(item.get("still_running") for item in cleanup_info):
        reasons.append("an owned process was still running after cleanup")
    if observations.get("owned_processes_remaining"):
        reasons.append("owned process identities survived cleanup")
    if not inert:
        if not observations.get("opencode_version"):
            reasons.append("installed OpenCode version was not observable")
        if not observations.get("actual_native_proof"):
            reasons.append("actual-native evidence label missing")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="caller-selected provider/model; no private pin")
    parser.add_argument("--variant", default=None,
                        help="caller-selected model variant (for example max); passed to every opencode run argv")
    parser.add_argument("--output", required=True, help="sanitized observations JSON path")
    parser.add_argument("--workspace", default=".tmp/opencode-lifecycle-workspace")
    parser.add_argument("--state-root", default=".tmp/opencode-lifecycle")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--task-file", default="marker_task.md")
    parser.add_argument("--opencode-binary", default="opencode")
    parser.add_argument("--run-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--ack-wait-seconds", type=float, default=300.0)
    parser.add_argument("--inert", action="store_true", help="use a generated inert OpenCode stand-in")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(HERE))
    from opencode_bridge import (  # noqa: E402
        cmd_init,
        cmd_observe,
        cmd_parent_exit,
        cmd_pause,
        cmd_queue,
        cmd_register_child,
        cmd_register_parent,
        cmd_resume,
        cmd_status,
        cmd_stop,
    )

    run_id = args.run_id or f"fixture-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}"
    workspace = (REPO_ROOT / args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    task_file = workspace / args.task_file
    if not task_file.is_file():
        source = HERE / "fixture" / "marker_task.md"
        task_file.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    state_root = (REPO_ROOT / args.state_root).resolve()
    scratch_dir = state_root / run_id / "raw"
    run = RunState(state_root, run_id)
    tracker: list = []
    observations: dict = {
        "runner": "run_fixture.py",
        "scope": SCOPE,
        "claim": "not-a-measured-result",
        "evidence_class": "inert-regression-only" if args.inert else "actual-native-observation",
        "actual_native_proof": not args.inert,
        "variant": args.variant,
        "model_label": args.model,
        "model_variant_in_argv": {"model": args.model, "variant": args.variant, "variant_passed": args.variant is not None},
        "run_id": run_id,
        "inert": args.inert,
        "started_at": utc_now(),
    }

    env = dict(os.environ)
    if args.inert:
        fake = workspace / "_fake_opencode.py"
        fake.write_text(FAKE_OPENCODE, encoding="utf-8")
        binary = [sys.executable, str(fake)]
        sessions_dir = state_root / run_id / "fake-sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        env["FAKE_OPENCODE_SESSIONS"] = str(sessions_dir)
        env["FAKE_OPENCODE_LABEL"] = "parent"
        observations["opencode_version"] = None
        observations["inert_stand_in"] = True
    else:
        binary = [args.opencode_binary]
        observations["inert_stand_in"] = False
        observations["opencode_version"] = probe_opencode_version(binary)

    try:
        cmd_init(run)
        memory_marker = secrets.token_hex(8)

        # 1. Fresh parent (no resume flag), must exit before the child starts.
        parent = run_opencode(binary, "Read marker_task.md and reply with the single word ACK. "
                                      "Do not write files and do not run commands.",
                              workspace, tracker, env, args.model, args.variant, None,
                              args.run_timeout_seconds, scratch_dir, "parent")
        observations["parent_run"] = {key: parent[key] for key in
                                      ("exit_code", "timed_out", "session_id", "native_task_tool_events")}
        observations["parent_run"].update({key: parent[key] for key in
                                           ("raw_events_scratch", "raw_events_sha256", "raw_events_bytes")})
        if parent["exit_code"] != 0:
            raise FixtureError(f"parent OpenCode run exited {parent['exit_code']}")
        if not parent["session_id"]:
            raise FixtureError("parent session id was not observable in the native JSON event stream")
        parent_session = parent["session_id"]
        cmd_register_parent(run, parent_session)
        cmd_parent_exit(run)

        # 2. Distinct fresh child (no resume flag) establishes its own session.
        if args.inert:
            env["FAKE_OPENCODE_LABEL"] = "child"
        child = run_opencode(binary,
                             f"Remember this one-time marker: {memory_marker}. If you have a native task/todo tool, "
                             "create one native task item titled lifecycle-fixture. Reply with the single word READY. "
                             "Do not write workspace files and do not run shell commands.",
                             workspace, tracker, env, args.model, args.variant, None,
                             args.run_timeout_seconds, scratch_dir, "child-bootstrap")
        observations["child_bootstrap_run"] = {key: child[key] for key in
                                               ("exit_code", "timed_out", "session_id", "native_task_tool_events")}
        observations["child_bootstrap_run"].update({key: child[key] for key in
                                                    ("raw_events_scratch", "raw_events_sha256", "raw_events_bytes")})
        if child["exit_code"] != 0 or not child["session_id"]:
            raise FixtureError("child bootstrap run did not establish an observable session id")
        child_session = child["session_id"]
        if child_session == parent_session:
            raise FixtureError("child session id equals the parent session id; not a distinct child")
        cmd_register_child(run, child_session, separate=True)
        task_events = child["native_task_tool_events"]
        observations["native_task_tool_events"] = ({
            "observed": True,
            "tool_names": sorted({item["tool"] for item in task_events}),
            "source": "child bootstrap native JSON stream",
        } if task_events else {
            "observed": "unknown",
            "reason": "no native task/todo tool event was observable in this harness",
        })
        memory_marker_in_workspace = any(
            memory_marker in path.read_text(encoding="utf-8", errors="replace")
            for path in workspace.rglob("*") if path.is_file()
        )
        observations["memory_proof"] = {
            "memory_marker_written_to_workspace": memory_marker_in_workspace,
            "memory_source": "session history only; the marker exists only in the child's first prompt/history",
        }

        server_events = {
            "evt-001": ("marker-001.txt", "Write the acknowledgement; set action_result to the one-time marker from "
                                          "your earlier turn (read it from your own session history, not from any file)."),
            "evt-002": ("marker-002.txt", "Write the acknowledgement; mark the native task item complete if you created "
                                          "one, and set action_result to the one-time marker from your earlier turn again."),
        }
        executor = build_opencode_argv(binary, workspace, args.model, args.variant, child_session)
        ack_dir_events: dict[str, dict] = {}

        # 3. Event 1 over the real detached controller.
        due = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
        cmd_queue(run, "evt-001", due, server_events["evt-001"][1], str(task_file), str(workspace),
                  marker_path=server_events["evt-001"][0])
        controller = run_scheduler(run_id, state_root, executor, tracker, env, scratch_dir, "controller-evt-001",
                                   max_dispatches=1, ack_wait=args.ack_wait_seconds, max_wait=None,
                                   join_timeout=args.ack_wait_seconds + 60, wait_event="evt-001")
        state = run.load()
        event = next(item for item in state["queued"] if item["event_id"] == "evt-001")
        dispatch = state["dispatches"][-1] if state["dispatches"] else {}
        ack = (dispatch.get("detail") or {}).get("ack") or {}
        ack_dir_events["evt-001"] = {key: ack.get(key) for key in
                                     ("run_id", "event_id", "session_id", "fence", "task_file_sha256", "action_result")}
        observations["controller_run"] = {
            "event_status": event["status"],
            "dispatch_status": dispatch.get("dispatch_status"),
            "exit_code": dispatch.get("exit_code"),
            "scheduler": {key: controller[key] for key in ("exit_code", "timed_out", "result")},
            "ack": ack_dir_events["evt-001"],
        }
        observations["memory_proof"]["ack_action_result_matches_memory_marker"] = ack.get("action_result") == memory_marker

        # 4. Inert lifecycle controls, explicitly labeled apart from native proof.
        selfcheck = subprocess.run([sys.executable, str(HERE / "bridge_selfcheck.py")],
                                   capture_output=True, text=True, timeout=300)
        observations["inert_lifecycle_regression"] = {
            "separately_labeled": True,
            "evidence_class": "inert-subprocess-regression",
            "exit_code": selfcheck.returncode,
            "tail": (selfcheck.stdout or selfcheck.stderr).strip().splitlines()[-2:],
            "covers": ["pause-prompt", "stop-prompt", "duplicate-dispatch", "timeout", "crash", "recovery"],
            "note": "Inert controls never prove native OpenCode crash or pause behavior.",
        }

        # 5. Real pause: queue event 2, prove a bounded scheduler dispatches nothing.
        cmd_pause(run)
        paused_state = cmd_status(run)
        due2 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
        cmd_queue(run, "evt-002", due2, server_events["evt-002"][1], str(task_file), str(workspace),
                  marker_path=server_events["evt-002"][0])
        dispatch_count_before_pause = len(run.load()["dispatches"])
        paused_scheduler = run_scheduler(run_id, state_root, executor, tracker, env, scratch_dir,
                                         "controller-paused", max_dispatches=1, ack_wait=60.0,
                                         max_wait=5.0, join_timeout=90.0)
        run_after_paused = run.load()
        paused_event = next(item for item in run_after_paused["queued"] if item["event_id"] == "evt-002")
        observations["paused_tick"] = {
            "paused_status": paused_state["status"],
            "result": paused_scheduler["result"],
            "dispatches_added": len(run_after_paused["dispatches"]) - dispatch_count_before_pause,
            "event_status": paused_event["status"],
        }

        # 6. Resume the exact child and consume event 2 in the same session.
        cmd_resume(run, child_session)
        resumed_state = cmd_status(run)
        controller2 = run_scheduler(run_id, state_root, executor, tracker, env, scratch_dir,
                                    "controller-evt-002", max_dispatches=1, ack_wait=args.ack_wait_seconds,
                                    max_wait=None, join_timeout=args.ack_wait_seconds + 60, wait_event="evt-002")
        state = run.load()
        event2 = next(item for item in state["queued"] if item["event_id"] == "evt-002")
        dispatch2 = state["dispatches"][-1] if state["dispatches"] else {}
        ack2 = (dispatch2.get("detail") or {}).get("ack") or {}
        ack_dir_events["evt-002"] = {key: ack2.get(key) for key in
                                     ("run_id", "event_id", "session_id", "fence", "task_file_sha256", "action_result")}
        observations["second_event"] = {
            "resumed_status": resumed_state["status"],
            "event_status": event2["status"],
            "dispatch_status": dispatch2.get("dispatch_status"),
            "exit_code": dispatch2.get("exit_code"),
            "session_id": dispatch2.get("session_id"),
            "scheduler": {key: controller2[key] for key in ("exit_code", "timed_out", "result")},
            "ack": ack_dir_events["evt-002"],
        }

        # 7. Real stop: queue event 3, stop, then execute a scheduler past its due time.
        due3 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
        cmd_queue(run, "evt-003", due3, "This event must never run after stop.", str(task_file), str(workspace),
                  marker_path="marker-003.txt")
        cmd_stop(run)
        stopped_state = cmd_status(run)
        due3_epoch = time.time() + 4
        time.sleep(max(0.0, due3_epoch - time.time()))
        dispatch_count_before_post_stop = len(run.load()["dispatches"])
        stopped_scheduler = run_scheduler(run_id, state_root, executor, tracker, env, scratch_dir,
                                          "controller-stopped", max_dispatches=1, ack_wait=30.0,
                                          max_wait=5.0, join_timeout=60.0)
        final_state = run.load()
        event3 = next(item for item in final_state["queued"] if item["event_id"] == "evt-003")
        observations["stopped_tick"] = {
            "stopped_status": stopped_state["status"],
            "result": stopped_scheduler["result"],
            "event_status": event3["status"],
        }
        observations["post_stop"] = {
            "event_status": event3["status"],
            "marker_present": marker_present(workspace, "marker-003.txt"),
            "dispatch_count_changed": len(final_state["dispatches"]) != dispatch_count_before_post_stop,
            "scheduler_result": stopped_scheduler["result"],
        }
        observations["invocation_argv"] = {
            "parent": parent["argv"],
            "child_bootstrap": child["argv"],
            "scheduler_executor": executor,
        }
        observations["observations_file"] = str(cmd_observe(run))
    except (FixtureError, OSError, subprocess.TimeoutExpired) as exc:
        observations["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        cleaned, remaining = cleanup(tracker)
        observations["cleanup"] = cleaned
        observations["owned_processes_remaining"] = remaining
        observations["ended_at"] = utc_now()
        try:
            observations["failure_reasons"] = evaluate_evidence(observations, args.inert)
        except Exception as exc:  # noqa: BLE001 - evidence evaluation must never lose the record
            observations["failure_reasons"] = [f"evidence evaluation error: {type(exc).__name__}: {exc}"]
        replacements = [
            (str(workspace), "<WORKSPACE>"),
            (str(state_root), "<STATE_ROOT>"),
            (str(REPO_ROOT), "<REPO>"),
            (str(Path.home()), "<HOME>"),
        ]
        sanitized = sanitize_value(observations, replacements)
        output = Path(args.output)
        if not output.is_absolute():
            output = REPO_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(sanitized, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(sanitized, indent=2, default=str))

    failure_reasons = observations.get("failure_reasons") or []
    if failure_reasons:
        for reason in failure_reasons:
            print(f"FAIL: lifecycle fixture: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
