#!/usr/bin/env python3
"""Orchestrator runner for one fresh-agent evaluation trial.

Run by an orchestrator, never by normal PR CI. It:

  1. prepares a throwaway snapshot for one case (fixtures applied), claims it
     with an owner marker, initializes a local throwaway Git repository in the
     destination only (never staged/committed/pushed), and records the
     snapshot content hash (the source HEAD alone ignores an uncommitted
     candidate);
  2. launches one FRESH separate `opencode run --format json` invocation with
     the common trial protocol block prepended to the case prompt (the case
     prompt in tasks.json stays exact case content, while the protocol
     version and rendered-prompt hash are recorded), the caller-selected model
     and variant in argv (no --continue, no session reuse), under
     PYTHONDONTWRITEBYTECODE=1 with the validator interpreter prepended to
      PATH, and captures the native JSON event stream, normalized tool actions
      with native status and explicit result-metadata outcomes, the session
      id, version, and timestamps;
  3. writes a sanitized trial record: only allowlisted normalized tool
     observations and final plain text, with credential-shaped strings and
     private paths removed. Raw event streams stay only in git-ignored
     private scratch for diagnosis;
  4. invokes the grader with that record and writes the verdict.

The grader grades expected actions against the collected actual tool events.
A missing trace, an unmet action, a fabricated command claim, or an empty
action list stays fail/unknown; it is never converted to success.

    .venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
        --case docs-fix --model <provider/model> --variant <label> \
        --output .tmp/fresh-agent-trials/docs-fix.trial.json

--events-file replays a previously captured native JSON stream instead of
launching an agent. Replay trials are labeled `execution_mode=
events-file-replay` / `fresh_separate_invocation=false`; they exist so the
runner/extractor can be regression tested offline and are never presented as
a fresh actual run.

--selfcheck runs offline prompt/profile, raw-trace identity, and normalized
tool-outcome regressions without launching an agent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "examples/opencode-lifecycle"))

import grader  # noqa: E402
from bridge_core import (  # noqa: E402
    identity_alive,
    plain_text_tail,
    process_identity,
    redact_secret_shapes,
    sanitize_value,
    terminate_identity_checked,
)
from tool_outcomes import (
    OUTCOME_SCHEMA_VERSION,
    is_native_tool_event,
    normalize_tool_actions,
    parse_native_events,
    selfcheck as action_normalizer_selfcheck,
)
from tool_profile import (
    AGENT_NAME,
    PROFILE_ID,
    ToolProfileError,
    check_profile_fixture,
    effective_permission_summary,
    profile_record,
    profile_selfcheck,
    special_project_directories,
)

PROMPT_PROTOCOL_VERSION = "workspace-protocol-v2"

# Suite-level constraints that apply to every case. They are prepended by the
# runner to the case prompt from tasks.json; the case prompt stays the exact
# case content. The checks are instruction + trace checks, not a sandbox.
PROMPT_PROTOCOL_TEMPLATE = """Common trial protocol ({protocol_version}).
Workspace root: {workspace}

- Work only inside the workspace root above. Every read, write, command, and
  tool must stay inside it.
- Do not read, write, or execute the source checkout, its grader, its
  fixtures, hidden expected outputs, or any path outside the workspace.
- Do not launch nested agents or model sessions, do not use the network, and
  do not install packages.
- The artifact paths and JSON field schema are fully disclosed in the case
  task below. Route facts and supporting evidence must be discovered from the
  visible workspace docs, applied fixture effects, and any entry text
  explicitly pasted into the task.
- Read-only inspection of command strings (for example grep on a document) is
  allowed; actually invoking a lifecycle command is not.
- One exception to the workspace-only rule: when the case task itself
  instructs a non-signalling process-liveness probe, use only `kill -0` on the
  pid or a Python `os.kill(pid, 0)` check that distinguishes ProcessLookupError
  from PermissionError, send no signal, and inspect no path outside the
  workspace (including /proc). A denied or non-executed probe is not evidence
  that the pid is dead; one successful probe is enough, then continue.
- The grader checks your recorded actions and artifacts against the case
  contract. This common protocol is not itself a shell sandbox; any
  case-specific native tool permission profile is documented separately.
"""


def pasted_entry_text(repo: Path) -> str:
    """Return only the generated root-route block used by the pasted-entry case."""
    entry = (repo / "README.md").read_text(encoding="utf-8")
    begin = "<!-- BEGIN GENERATED START-HERE ROUTE -->"
    end = "<!-- END GENERATED START-HERE ROUTE -->"
    if entry.count(begin) != 1 or entry.count(end) != 1:
        raise ValueError("README.md must contain one canonical generated route for the pasted-entry case")
    start = entry.index(begin)
    finish = entry.index(end) + len(end)
    return entry[start:finish]


def render_child_prompt(workspace: Path, case: dict, repo: Path | None = None) -> str:
    """Prepend common constraints and render any canonical entry text by value."""
    protocol = PROMPT_PROTOCOL_TEMPLATE.format(
        protocol_version=PROMPT_PROTOCOL_VERSION, workspace=workspace
    )
    rendered = protocol + "\n" + case["prompt"]
    if case.get("execution_profile") == PROFILE_ID:
        rendered += (
            "\n\nThe runner selected the named OpenCode runtime permission profile and "
            "checked its effective matrix before this invocation. Its admitted "
            "permission keys are read, list, glob, grep, and edit; OpenCode's edit "
            "permission covers file editing, writing, and patching. Unspecified "
            "tools inherit the default deny. Do not use Python or shell. This is a tool-permission "
            "fixture, not a claim that the host lacks a Python executable. The "
            "common liveness-probe exception does not apply."
        )
    if case.get("pasted_canonical_entry") == "root_route":
        if repo is None:
            raise ValueError("a source repository is required to render pasted canonical entry text")
        rendered += "\n\nCanonical entry text pasted by the user:\n\n" + pasted_entry_text(repo)
    return rendered


def scratch_path_for(repo: Path, output_path: Path) -> Path:
    """Derive a unique raw-trace scratch path from the output/trial identity.

    Different output names always yield different scratch files, so a rerun
    can never overwrite an earlier trial's preserved raw trace.
    """
    name = output_path.name
    for suffix in (".jsonl", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(".trial"):
        name = name[: -len(".trial")]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._") or "trial"
    return repo / ".tmp/fresh-agent-trials" / f"{name}.raw-events.jsonl"


def selfcheck() -> int:
    """Offline regressions for prompt constraints and raw-trace uniqueness."""
    failures: list[str] = []
    workspace = Path("/tmp/of-eval-workspace")
    case = {"case_id": "selfcheck", "prompt": "Do the selfcheck case task."}
    rendered = render_child_prompt(workspace, case)
    for needle in (
        PROMPT_PROTOCOL_VERSION,
        str(workspace),
        "outside the workspace",
        "nested agents",
        "model sessions",
        "network",
        "JSON field schema are fully disclosed",
        "kill -0",
        "os.kill",
        "not itself a shell sandbox",
        case["prompt"],
    ):
        if needle not in rendered:
            failures.append(f"rendered child prompt is missing {needle!r}")
    if rendered.index(case["prompt"]) < rendered.index(PROMPT_PROTOCOL_VERSION):
        failures.append("case prompt must follow the common protocol block")
    file_only_case = {
        "case_id": "selfcheck-file-profile",
        "execution_profile": PROFILE_ID,
        "prompt": "Use the file-only discovery fixture.",
    }
    file_only_prompt = render_child_prompt(workspace, file_only_case)
    for needle in (
        "effective matrix",
        "permission keys are read, list, glob, grep, and edit",
        "edit permission covers file editing, writing, and patching",
        "default deny",
        "Do not use Python or shell",
        "host lacks a Python executable",
    ):
        if needle not in file_only_prompt:
            failures.append(f"file-only profile prompt is missing the fixture boundary {needle!r}")

    repo = Path("/tmp/of-eval-repo")
    first = scratch_path_for(repo, repo / ".tmp/fresh-agent-trials/docs-fix.trial.json")
    second = scratch_path_for(repo, repo / ".tmp/fresh-agent-trials/docs-fix-rerun.trial.json")
    if first == second:
        failures.append("different trials resolved to the same raw-trace scratch path")
    if first.name != "docs-fix.raw-events.jsonl":
        failures.append(f"scratch name does not derive from output identity: {first.name}")
    if scratch_path_for(repo, repo / ".tmp/fresh-agent-trials/docs-fix.trial.json") != first:
        failures.append("the same output identity did not resolve to a stable scratch path")

    failures.extend(action_normalizer_selfcheck())
    failures.extend(profile_selfcheck())

    for failure in failures:
        print(f"FAIL: selfcheck: {failure}")
    if failures:
        print(f"\n{len(failures)} runner selfcheck failure(s)")
        return 1
    print(f"note: runner selfcheck: prompt constraints ({PROMPT_PROTOCOL_VERSION}), raw-trace "
          "uniqueness, runtime permission-profile, and normalized tool-outcome regressions behaved as expected")
    print("ok")
    return 0


def _event_epoch(event: dict) -> float | None:
    raw = next((event[key] for key in ("timestamp", "time", "created", "at", "ts") if key in event), None)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e15:
            return value / 1e6
        if value > 1e12:
            return value / 1e3
        return value
    if isinstance(raw, str):
        text = raw.strip().replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(text).timestamp()
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None


def _first_action_seconds(events: list, started_epoch: float) -> tuple[float | None, str | None]:
    """First timestamped real tool action, with ms/us/ISO unit normalization."""
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        if not is_native_tool_event(event):
            continue
        stamp = _event_epoch(event)
        if stamp is None:
            continue
        seconds = stamp - started_epoch
        if 0 <= seconds < 86400 * 30:
            return round(seconds, 3), None
    return None, "no timestamped tool event in the native stream"


def extract_session_id(events: list) -> str | None:
    for event in events:
        if not isinstance(event, dict):
            continue
        for key in ("sessionID", "session_id", "sessionId"):
            if isinstance(event.get(key), str):
                return event[key]
        for value in event.values():
            if isinstance(value, dict):
                for key in ("sessionID", "session_id", "sessionId"):
                    if isinstance(value.get(key), str):
                        return value[key]
    return None


def extract_events(output: str) -> list:
    return parse_native_events(output)


def _artifact_refs(workspace: Path, case: dict) -> dict:
    refs = {}
    for deliverable in case["deliverables"]:
        path = workspace / deliverable["path"]
        if path.is_file():
            refs[deliverable["path"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    return refs


def _snapshot_digest(root: Path) -> tuple[str, int]:
    """Content hash of the prepared snapshot; independent of git HEAD.

    Runner-owned local Git internals are excluded so the digest is stable
    across repeated prepares; every source/config file is still hashed.
    """
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return digest.hexdigest(), count


def _child_environment() -> dict:
    """Environment for the child session: no bytecode, controlled `python`."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    interpreter_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
    return env


def _effective_profile_probe(binary: str, workspace: Path, profile_json: str, env: dict) -> dict:
    """Check the selected runtime agent without launching a model or retaining config output."""
    before = special_project_directories(workspace)
    probe_env = dict(env)
    probe_env["OPENCODE_CONFIG_CONTENT"] = profile_json
    exit_code: int | None = None
    error_kind = None
    summary = None
    try:
        proc = subprocess.run(
            [binary, "debug", "agent", AGENT_NAME],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            env=probe_env,
        )
        exit_code = proc.returncode
        summary = effective_permission_summary((proc.stdout or "") + "\n" + (proc.stderr or ""))
    except subprocess.TimeoutExpired:
        error_kind = "timeout"
    except OSError as exc:
        error_kind = type(exc).__name__
    after = special_project_directories(workspace)
    matrix_matches = isinstance(summary, dict) and summary.get("matches_admitted_matrix") is True
    tree_clean = not before["opencode"] and not before["node_modules"] and not after["opencode"] and not after["node_modules"]
    status = "verified" if exit_code == 0 and matrix_matches and tree_clean else "failed"
    result = {
        "status": status,
        "method": "non-model `opencode debug agent entry-files-only` with OPENCODE_CONFIG_CONTENT",
        "exit_code": exit_code,
        "error_kind": error_kind,
        "agent_name": AGENT_NAME,
        "effective_matrix_matches": matrix_matches,
        "effective_permission_matrix": summary,
        "project_opencode_absent_before": not before["opencode"],
        "project_opencode_absent_after": not after["opencode"],
        "node_modules_absent_before": not before["node_modules"],
        "node_modules_absent_after": not after["node_modules"],
        "model_invocation": False,
        "raw_debug_output_retained": False,
        "argv": [Path(binary).name, "debug", "agent", AGENT_NAME],
    }
    return result


def _git_head(repo: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _write_scratch(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=None)
    parser.add_argument("--model", default=None, help="caller-selected provider/model; no private pin")
    parser.add_argument("--variant", default=None,
                        help="caller-selected model variant (for example max); passed to the opencode run argv")
    parser.add_argument("--output", default=None, help="sanitized trial record path")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--opencode-binary", default="opencode")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--events-file", default=None,
                        help="replay a captured native JSON stream (offline self-test; labeled replay)")
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--selfcheck", action="store_true",
                        help="offline prompt-constraint and raw-trace uniqueness regressions (no agent)")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    for name in ("case", "model", "output"):
        if getattr(args, name) is None:
            parser.error(f"--{name} is required unless --selfcheck is given")

    repo = Path(args.repo).resolve()
    case = grader.case_by_id(repo, args.case)
    workspace = Path(args.workspace).resolve() if args.workspace else (
        repo / ".tmp/fresh-agent-trials" / args.case
    )
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = repo / output_path
    try:
        rendered_prompt = render_child_prompt(workspace, case, repo)
        pasted_entry_sha256 = (
            hashlib.sha256(pasted_entry_text(repo).encode("utf-8")).hexdigest()
            if case.get("pasted_canonical_entry") == "root_route"
            else None
        )
    except (OSError, ValueError) as exc:
        print(f"FAIL: could not render the disclosed case prompt: {type(exc).__name__}")
        return 2
    rendered_prompt_sha = hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()
    try:
        prepare_record = grader.prepare(repo, case, workspace)
    except grader.GraderError as exc:
        print(f"FAIL: refusing to prepare the trial workspace: {exc}")
        return 2
    child_tool_profile = None
    profile_json = None
    profile_blocked = False
    if case.get("execution_profile") is not None:
        if case.get("execution_profile") != PROFILE_ID:
            print("FAIL: refusing an unknown child tool profile")
            return 2
        try:
            profile_json = check_profile_fixture(repo, workspace)
        except ToolProfileError as exc:
            print(f"FAIL: refusing to run without the declared tool profile: {exc}")
            return 2
        if args.events_file is not None:
            permission_probe = {"status": "not_performed", "model_invocation": False}
        else:
            permission_probe = _effective_profile_probe(
                args.opencode_binary,
                workspace,
                profile_json,
                _child_environment(),
            )
            profile_blocked = permission_probe.get("status") != "verified"
        child_tool_profile = profile_record(
            configured=True,
            applied=not (args.events_file is not None) and not profile_blocked,
            replay=args.events_file is not None,
            permission_probe=permission_probe,
        )
    snapshot_sha, snapshot_files = _snapshot_digest(workspace)

    replay = args.events_file is not None
    started_epoch = time.time()
    events: list = []
    output = ""
    session_id = None
    exit_code: int | None = None
    harness_version = None
    launch_error = None
    argv_record: list[str] = []
    owned_process_remaining = False
    agent_process_started = False

    if replay:
        output = Path(args.events_file).read_text(encoding="utf-8")
        events = extract_events(output)
        session_id = extract_session_id(events)
        exit_code = 0
    elif profile_blocked:
        launch_error = "non-model effective permission probe did not verify the file-only matrix or clean project tree"
    else:
        child_env = _child_environment()
        if profile_json is not None:
            child_env["OPENCODE_CONFIG_CONTENT"] = profile_json
        try:
            version_proc = subprocess.run([args.opencode_binary, "--version"], capture_output=True, text=True,
                                          timeout=30, env=child_env)
            harness_version = (version_proc.stdout or version_proc.stderr).strip() or None
        except (OSError, subprocess.TimeoutExpired):
            harness_version = None
        argv_run = [args.opencode_binary, "run", "--format", "json", "--dir", str(workspace),
                    "--model", args.model]
        if child_tool_profile is not None:
            argv_run.insert(2, "--pure")
            argv_run += ["--agent", AGENT_NAME]
        if args.variant:
            argv_run += ["--variant", args.variant]
        argv_record = list(argv_run)
        try:
            proc = subprocess.Popen([*argv_run, rendered_prompt], cwd=workspace, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True,
                                    env=child_env)
            agent_process_started = True
            identity = process_identity(proc.pid)
            try:
                output = proc.communicate(timeout=args.timeout_seconds)[0] or ""
            except subprocess.TimeoutExpired:
                terminate_identity_checked(identity, grace_seconds=5.0)
                try:
                    output = proc.communicate(timeout=10)[0] or ""
                except subprocess.TimeoutExpired:
                    terminate_identity_checked(identity, grace_seconds=0.0)
                    try:
                        output = proc.communicate(timeout=5)[0] or ""
                    except subprocess.TimeoutExpired:
                        output = ""
                launch_error = f"trial exceeded {args.timeout_seconds}s; owned process group terminated and reaped"
            exit_code = proc.returncode
            owned_process_remaining = identity_alive(identity)
            if owned_process_remaining:
                launch_error = (launch_error or "") + " owned process identity still alive after timeout"
        except OSError as exc:
            launch_error = f"could not launch {args.opencode_binary}: {exc}"
        events = extract_events(output)
        session_id = extract_session_id(events)

    if child_tool_profile is not None:
        child_tool_profile["applied_to_child_invocation"] = agent_process_started
    execution_mode = (
        "events-file-replay" if replay
        else "profile-probe-blocked" if profile_blocked
        else "fresh-opencode-run" if agent_process_started
        else "launch-failed"
    )

    replacements = [
        (str(workspace), "<WORKSPACE>"),
        (str(repo), "<REPO>"),
        (str(Path.home()), "<HOME>"),
    ]
    observed_actions = normalize_tool_actions(
        events,
        replacements=tuple(replacements),
        redact=redact_secret_shapes,
    )
    first_action, first_action_reason = _first_action_seconds(events, started_epoch)
    ended_epoch = time.time()
    scratch_path = scratch_path_for(repo, output_path)
    _write_scratch(scratch_path, output)
    trial = sanitize_value({
        "case_id": case["case_id"],
        "task_mode": case.get("mode", "coached"),
        "fixture_conditions": prepare_record.get("fixture_conditions", []),
        "variant": args.variant,
        "variant_in_argv": bool(args.variant) and bool(argv_record),
        "execution_mode": execution_mode,
        "fresh_separate_invocation": agent_process_started,
        "replay": replay,
        "model": args.model,
        "invocation_argv": argv_record,
        "workspace": str(workspace),
        "workspace_identity": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
        "source_snapshot_sha256": snapshot_sha,
        "source_snapshot_files": snapshot_files,
        "source_git_head": _git_head(repo),
        "harness": "opencode",
        "harness_version": harness_version,
        "session_id": session_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_epoch)),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended_epoch)),
        "exit_code": exit_code,
        "launch_error": launch_error,
        "model_process_started": agent_process_started,
        "owned_process_remaining": owned_process_remaining,
        "observed_actions": observed_actions,
        "observed_action_schema_version": 2,
        "outcome_schema_version": OUTCOME_SCHEMA_VERSION,
        "child_tool_profile": child_tool_profile,
        "action_extraction": {
            "method": "versioned allowlisted actions correlated by native tool-call ID; shell exit code only from native result metadata",
            "events_observed": len(events),
            "actions_observed": len(observed_actions),
            "raw_events_scratch": str(scratch_path.relative_to(repo)),
            "raw_events_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "raw_events_bytes": len(output.encode("utf-8")),
            "note": "Raw events stay in git-ignored scratch; provider metadata and tool output text are omitted.",
        },
        "child_environment": {
            "applied": agent_process_started,
            "PYTHONDONTWRITEBYTECODE": "1",
            "python_on_path": str(Path(sys.executable).resolve().parent),
            "note": "Set for the child session so validator runs create no bytecode under frozen dirs; "
                    "`python` on the child PATH is this interpreter.",
        },
        "child_prompt": {
            "protocol_version": PROMPT_PROTOCOL_VERSION,
            "rendered_sha256": rendered_prompt_sha,
            "delivered": agent_process_started,
            "pasted_canonical_entry": case.get("pasted_canonical_entry"),
            "pasted_entry_source": "README.md" if pasted_entry_sha256 is not None else None,
            "pasted_entry_sha256": pasted_entry_sha256,
            "note": "The runner prepends the common protocol block and, for the pasted-entry case, the canonical route block from README.md; "
                    "the exact task prompt remains in tasks.json. Rendered text is hashed, not retained, because it embeds the workspace path.",
        },
        "first_useful_action_seconds": first_action,
        "first_useful_action_unknown_reason": first_action_reason,
        "clarification_count": None,
        "plain_text_tail": plain_text_tail(output),
        "artifact_refs": _artifact_refs(workspace, case),
        "claim": "single-trial observation; not a measured result",
    }, replacements)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(trial, indent=2, default=str) + "\n", encoding="utf-8")

    verdict = grader.grade(repo, case, workspace, trial)
    verdict_path = output_path.with_suffix(".verdict.json")
    verdict_path.write_text(json.dumps(verdict, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"trial": str(output_path), "verdict": str(verdict_path),
                      "success": verdict["success"],
                      "failure_reasons": verdict.get("failure_reasons", [])}, indent=2))
    return 0 if verdict["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
