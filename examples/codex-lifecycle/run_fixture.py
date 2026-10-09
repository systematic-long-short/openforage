#!/usr/bin/env python3
"""Bounded Codex CLI lifecycle marker fixture.

The actual run is explicitly invoked by its caller. Offline validation uses
``--selfcheck`` and never starts Codex. A fixture run uses one fresh parent
``exec``, then a fresh child ``exec``, two exact-thread history resumes, and
one due dispatch from a finite external driver. No OpenForage APIs are called.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "examples/opencode-lifecycle"))

from bridge_core import identity_alive, process_identity, terminate_identity_checked  # noqa: E402
from codex_events import (  # noqa: E402
    exact_resume_matches,
    explicit_tool_observations,
    extract_final_message,
    extract_thread_id,
    history_ack_matches,
    parse_final_json,
    parse_jsonl,
    readiness_ack_matches,
    selfcheck as event_selfcheck,
)

HARD_DEADLINE_SECONDS = 40 * 60
TARGET_WALL_SECONDS = 25 * 60
DEFAULT_PROCESS_TIMEOUT_SECONDS = 360.0
CLEANUP_GRACE_SECONDS = 5.0
CLEANUP_RESERVE_SECONDS = 15.0
EXTERNAL_DUE_DELAY_SECONDS = 0.5
BLOCKED_DUE_WINDOW_SECONDS = 0.25
MAX_NATIVE_TOOL_OBSERVATIONS = 100
MODEL_SELECTOR_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,160}$")
EFFORT_SELECTOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class FixtureError(RuntimeError):
    """A stable, public-safe fixture failure code."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_hashes() -> dict[str, str]:
    paths = (
        HERE / "run_fixture.py",
        HERE / "codex_events.py",
        HERE / "fixture/AGENTS.md",
        REPO_ROOT / "examples/opencode-lifecycle/bridge_core.py",
    )
    return {
        path.relative_to(REPO_ROOT).as_posix(): _sha256_bytes(path.read_bytes())
        for path in paths
    }


def _safe_codex_version(binary: str, timeout: float = 15.0) -> tuple[str | None, int | None]:
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return None, None
    except OSError:
        return None, None
    raw = (proc.stdout or proc.stderr or "").strip().splitlines()
    if proc.returncode != 0 or not raw:
        return None, proc.returncode
    match = re.search(r"\b([0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*)\b", raw[0])
    return (f"codex-cli {match.group(1)}" if match else None), proc.returncode


def _environment_metadata(environ: Mapping[str, str] | None = None) -> dict:
    """Record only the OS and whether user-scoped Codex instructions exist."""
    environ = os.environ if environ is None else environ
    codex_home = environ.get("CODEX_HOME")
    instruction_root = Path(codex_home) if codex_home else Path.home() / ".codex"
    try:
        instruction_present: bool | None = (instruction_root / "AGENTS.md").exists()
    except (OSError, RuntimeError, ValueError):
        instruction_present = None
    return {
        "os_platform": sys.platform,
        "user_agents_md_present": instruction_present,
    }


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_is_within(left, right) or _path_is_within(right, left)


def _isolated_git_environment() -> dict[str, str]:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
    ):
        env.pop(name, None)
    return env


def _git_path_is_ignored(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "--quiet", "--no-index", relative],
            capture_output=True,
            text=True,
            timeout=15,
            env=_isolated_git_environment(),
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _resolved_paths(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path, Path, dict]:
    repo_root = REPO_ROOT.resolve()
    scratch_lexical = repo_root / ".tmp"
    if scratch_lexical.is_symlink() or not scratch_lexical.is_dir():
        raise FixtureError("ignored_tmp_root_must_be_an_existing_directory")
    scratch_root = scratch_lexical.resolve()
    if scratch_root == repo_root or not scratch_root.is_relative_to(repo_root):
        raise FixtureError("ignored_tmp_root_must_remain_inside_the_checkout")

    try:
        temp_root_lexical = Path(os.path.abspath(tempfile.gettempdir()))
        temp_root = temp_root_lexical.resolve()
    except (OSError, RuntimeError, ValueError):
        raise FixtureError("system_temp_root_resolution_failed") from None

    def absolute_lexical(value: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise FixtureError("path_argument_invalid")
        path = Path(value)
        if not path.is_absolute():
            path = repo_root / path
        try:
            return Path(os.path.abspath(path))
        except (OSError, RuntimeError, ValueError):
            raise FixtureError("path_argument_invalid") from None

    def resolved(path: Path, label: str) -> Path:
        try:
            return path.resolve()
        except (OSError, RuntimeError, ValueError):
            raise FixtureError(f"{label}_path_resolution_failed") from None

    workspace_lexical = absolute_lexical(args.workspace)
    state_lexical = absolute_lexical(args.state)
    output_lexical = absolute_lexical(args.output)
    workspace = resolved(workspace_lexical, "workspace")
    state = resolved(state_lexical, "state")
    output = resolved(output_lexical, "output")

    if state_lexical == scratch_lexical or not state_lexical.is_relative_to(scratch_lexical):
        raise FixtureError("state_path_must_be_inside_ignored_tmp")
    if state == scratch_root or not state.is_relative_to(scratch_root):
        raise FixtureError("state_path_symlink_escapes_ignored_tmp")
    if output_lexical == scratch_lexical or not output_lexical.is_relative_to(scratch_lexical):
        raise FixtureError("output_path_must_be_inside_ignored_tmp")
    if output == scratch_root or not output.is_relative_to(scratch_root):
        raise FixtureError("output_path_symlink_escapes_ignored_tmp")

    workspace_in_scratch = (
        workspace_lexical != scratch_lexical
        and workspace_lexical.is_relative_to(scratch_lexical)
    )
    temp_roots = {temp_root_lexical, temp_root}
    workspace_in_system_temp = any(
        workspace_lexical != root and workspace_lexical.is_relative_to(root)
        for root in temp_roots
    )
    workspace_outside_checkout = (
        not _path_is_within(workspace_lexical, repo_root)
        and not _path_is_within(repo_root, workspace_lexical)
    )
    if workspace_in_scratch:
        if workspace == scratch_root or not workspace.is_relative_to(scratch_root):
            raise FixtureError("workspace_path_symlink_escapes_ignored_tmp")
    elif workspace_in_system_temp and workspace_outside_checkout:
        if (
            workspace == temp_root
            or not workspace.is_relative_to(temp_root)
            or _path_is_within(workspace, repo_root)
            or _path_is_within(repo_root, workspace)
        ):
            raise FixtureError("workspace_path_symlink_escapes_system_temp")
    else:
        raise FixtureError("workspace_must_be_fresh_under_ignored_tmp_or_system_temp_outside_checkout")

    named_paths = (
        ("workspace", workspace),
        ("state", state),
        ("output", output),
    )
    for index, (left_label, left) in enumerate(named_paths):
        for right_label, right in named_paths[index + 1:]:
            if _paths_overlap(left, right):
                raise FixtureError("workspace_state_output_paths_must_not_overlap")

    for lexical, path, label in (
        (state_lexical, state, "state_directory"),
        (workspace_lexical, workspace, "workspace_directory"),
        (output_lexical, output, "output_file"),
    ):
        if lexical.exists() or lexical.is_symlink() or path.exists() or path.is_symlink():
            raise FixtureError(f"existing_{label}_refused_to_preserve_prior_evidence")
    if not workspace.parent.is_dir():
        raise FixtureError("workspace_parent_must_already_exist")
    return workspace, state, output, _environment_metadata(environ)


def _prepare_workspace(workspace: Path) -> None:
    workspace.mkdir(exist_ok=False)
    instructions = (HERE / "fixture/AGENTS.md").read_text(encoding="utf-8")
    (workspace / "AGENTS.md").write_text(instructions, encoding="utf-8")
    env = {**_isolated_git_environment(), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        ["git", "-C", str(workspace), "init", "--quiet"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if proc.returncode != 0 or not (workspace / ".git").exists():
        raise FixtureError("isolated_git_root_initialization_failed")
    root_proc = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    root_text = (root_proc.stdout or "").strip()
    if root_proc.returncode != 0 or not root_text:
        raise FixtureError("isolated_git_root_verification_failed")
    try:
        actual_root = Path(root_text).resolve()
    except (OSError, RuntimeError, ValueError):
        raise FixtureError("isolated_git_root_verification_failed") from None
    if actual_root != workspace.resolve():
        raise FixtureError("isolated_git_root_verification_failed")


def _marker_absent_from_workspace(workspace: Path, marker: str) -> bool:
    marker_bytes = marker.encode("utf-8")
    try:
        for current, directories, files in os.walk(workspace, followlinks=False):
            directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
            for name in files:
                path = Path(current) / name
                if path.is_symlink():
                    return False
                if marker_bytes in path.read_bytes():
                    return False
    except OSError:
        return False
    return True


def _codex_argv(
    binary: str,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    prompt: str,
    target_thread_id: str | None,
) -> list[str]:
    argv = [
        binary,
        "--no-daemon",
        "--ask-for-approval",
        "never",
        "-c",
        "features.goals=true",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "--model",
        model,
        "--cd",
        str(workspace),
        "--sandbox",
        "read-only",
        "exec",
    ]
    if target_thread_id is not None:
        argv.extend(["resume", "--json", "--ignore-user-config", target_thread_id, prompt])
    else:
        argv.extend(["--json", "--ignore-user-config", prompt])
    return argv


def _public_argv(
    binary: str,
    model: str,
    reasoning_effort: str,
    target_thread_id: str | None,
) -> list[str]:
    argv = [
        Path(binary).name,
        "--no-daemon",
        "--ask-for-approval",
        "never",
        "-c",
        "features.goals=true",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "--model",
        model,
        "--cd",
        "<WORKSPACE>",
        "--sandbox",
        "read-only",
        "exec",
    ]
    if target_thread_id is not None:
        argv.extend(["resume", "--json", "--ignore-user-config", target_thread_id, "<PROMPT>"])
    else:
        argv.extend(["--json", "--ignore-user-config", "<PROMPT>"])
    return argv


def _identity_fingerprint(identity: dict | None) -> tuple | None:
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int):
        return None
    if identity.get("starttime") is None:
        return None
    return identity.get("boot_id"), identity.get("pid"), identity.get("starttime")


def _identity_is_alive(identity: dict | None) -> bool | None:
    if identity is None:
        return None
    try:
        return identity_alive(identity)
    except Exception:  # noqa: BLE001 - record inability without exposing host details
        return None


def _cleanup_owned_process(
    proc: subprocess.Popen | None,
    identity: dict | None,
    timed_out: bool,
    *,
    terminate=None,
    alive_probe=None,
) -> dict:
    """Use only the recorded process identity and group for timeout cleanup."""
    terminate = terminate or terminate_identity_checked
    alive_probe = alive_probe or _identity_is_alive
    cleanup = {
        "timeout_triggered": timed_out,
        "identity_captured": identity is not None,
        "termination_target_scope": (
            "recorded_process_group"
            if isinstance(identity, dict) and isinstance(identity.get("pgid"), int) and identity["pgid"] > 0
            else "recorded_process_identity"
        ),
        "termination_attempted": False,
        "process_group_cleanup_attempted": False,
        "sigterm_sent": False,
        "sigkill_sent": False,
        "identity_alive_after_cleanup": None,
        "leader_reaped": proc is None,
        "broad_kill_used": False,
    }
    if proc is None:
        return cleanup
    try:
        running = proc.poll() is None
    except Exception:  # noqa: BLE001
        running = True
    alive = alive_probe(identity)
    if timed_out or running or alive is True:
        try:
            result = terminate(identity, grace_seconds=CLEANUP_GRACE_SECONDS)
        except Exception:  # noqa: BLE001 - cleanup remains recorded and bounded
            result = {}
        cleanup["termination_attempted"] = bool(result.get("attempted"))
        cleanup["process_group_cleanup_attempted"] = bool(
            result.get("attempted")
            and isinstance(identity, dict)
            and isinstance(identity.get("pgid"), int)
            and identity["pgid"] > 0
        )
        cleanup["sigterm_sent"] = bool(result.get("terminated"))
        cleanup["sigkill_sent"] = bool(result.get("killed"))
    try:
        proc.wait(timeout=CLEANUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # A second identity-checked group cleanup is bounded and never falls
        # back to kill-by-name, a broad PID match, or an unowned process.
        try:
            result = terminate(identity, grace_seconds=0.0)
        except Exception:  # noqa: BLE001
            result = {}
        cleanup["termination_attempted"] = cleanup["termination_attempted"] or bool(result.get("attempted"))
        cleanup["process_group_cleanup_attempted"] = cleanup["process_group_cleanup_attempted"] or bool(
            result.get("attempted")
            and isinstance(identity, dict)
            and isinstance(identity.get("pgid"), int)
            and identity["pgid"] > 0
        )
        cleanup["sigterm_sent"] = cleanup["sigterm_sent"] or bool(result.get("terminated"))
        cleanup["sigkill_sent"] = cleanup["sigkill_sent"] or bool(result.get("killed"))
        try:
            proc.wait(timeout=CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    except Exception:  # noqa: BLE001
        pass
    try:
        cleanup["leader_reaped"] = proc.poll() is not None
    except Exception:  # noqa: BLE001
        cleanup["leader_reaped"] = False
    cleanup["identity_alive_after_cleanup"] = alive_probe(identity)
    return cleanup


def _run_codex_call(
    *,
    phase: str,
    binary: str,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    state: Path,
    repo: Path,
    prompt: str,
    target_thread_id: str | None,
    process_timeout: float,
    hard_deadline_monotonic: float,
) -> dict:
    raw_path = state / "events" / f"{phase}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    argv = _codex_argv(binary, model, reasoning_effort, workspace, prompt, target_thread_id)
    safe_argv = _public_argv(binary, model, reasoning_effort, target_thread_id)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    remaining = hard_deadline_monotonic - started_monotonic
    effective_timeout = min(process_timeout, max(0.0, remaining - CLEANUP_RESERVE_SECONDS))
    exit_code: int | None = None
    error_kind = None
    timed_out = False
    proc = None
    identity = None
    cleanup = _cleanup_owned_process(None, None, timed_out=False)

    try:
        with raw_path.open("xb") as raw_stream:
            if effective_timeout <= 0:
                error_kind = "hard_deadline_exhausted"
            else:
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
                try:
                    proc = subprocess.Popen(
                        argv,
                        cwd=workspace,
                        stdout=raw_stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        env=env,
                    )
                except OSError as exc:
                    error_kind = type(exc).__name__
                if proc is not None:
                    try:
                        identity = process_identity(proc.pid)
                    except Exception:  # noqa: BLE001
                        identity = None
                    try:
                        exit_code = proc.wait(timeout=effective_timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                    finally:
                        cleanup = _cleanup_owned_process(proc, identity, timed_out=timed_out)
                        exit_code = proc.returncode
    except OSError as exc:
        error_kind = error_kind or type(exc).__name__

    ended_monotonic = time.monotonic()
    ended_at = _utc_now()
    try:
        raw_bytes = raw_path.read_bytes()
    except OSError:
        raw_bytes = b""
        error_kind = error_kind or "raw_event_read_failed"
    raw_text = raw_bytes.decode("utf-8", errors="replace")
    events = parse_jsonl(raw_text)
    thread_id = extract_thread_id(events)
    message = extract_final_message(events)
    final_json = parse_final_json(events)
    observed_tools = explicit_tool_observations(events, limit=MAX_NATIVE_TOOL_OBSERVATIONS)
    if timed_out:
        error_kind = error_kind or "process_timeout"
    process_record = {
        "phase": phase,
        "argv": safe_argv,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": round(max(0.0, ended_monotonic - started_monotonic), 3),
        "exit_code": exit_code,
        "error_kind": error_kind,
        "timed_out": timed_out,
        "thread_id": thread_id,
        "native_event_count": len(events),
        "final_message_json_valid": final_json is not None,
        "observed_native_tools": observed_tools,
        "raw_events_path": raw_path.relative_to(repo).as_posix(),
        "raw_events_sha256": _sha256_bytes(raw_bytes),
        "raw_events_bytes": len(raw_bytes),
        "started_in_new_process_group": proc is not None,
        "process_identity_captured": identity is not None,
        "process_identity_fingerprint": _identity_fingerprint(identity),
        "cleanup": cleanup,
        "_events": events,
        "_message": message,
        "_final_json": final_json,
        "_started_monotonic": started_monotonic,
        "_ended_monotonic": ended_monotonic,
    }
    if target_thread_id is not None:
        process_record["target_thread_id"] = target_thread_id
    return process_record


class _FiniteExternalDriver:
    """One-run external event queue with terminal stop and duplicate refusal."""

    def __init__(self) -> None:
        self.state = "running"
        self.known_ids: set[str] = set()
        self.pending: list[dict] = []
        self.dispatch_count = 0
        self.codex_process_invocations = 0
        self.duplicate_refusals = 0

    def schedule(self, event: dict) -> bool:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return False
        if event_id in self.known_ids:
            self.duplicate_refusals += 1
            return False
        if self.state == "stopped":
            return False
        self.known_ids.add(event_id)
        self.pending.append(event)
        return True

    def set_state(self, state: str) -> bool:
        if state not in {"running", "paused", "stopped"}:
            return False
        if self.state == "stopped" and state != "stopped":
            return False
        self.state = state
        return True

    def dispatch_due(self, now_monotonic: float) -> list[dict]:
        if self.state != "running":
            return []
        due = [event for event in self.pending if event["due_monotonic"] <= now_monotonic]
        self.pending = [event for event in self.pending if event["due_monotonic"] > now_monotonic]
        self.dispatch_count += len(due)
        return sorted(due, key=lambda event: event["due_monotonic"])


def _selfcheck_driver() -> list[str]:
    failures: list[str] = []
    driver = _FiniteExternalDriver()
    event = {"event_id": "evt-inert-once", "due_monotonic": 1.0, "target_thread_id": "thread-test"}
    if not driver.schedule(event):
        failures.append("inert external driver rejected a first event")
    if driver.dispatch_due(0.99):
        failures.append("inert external driver dispatched before due time")
    if len(driver.dispatch_due(1.0)) != 1 or driver.dispatch_count != 1:
        failures.append("inert external driver did not dispatch the due event once")
    if driver.schedule(event) or driver.duplicate_refusals != 1:
        failures.append("inert external driver did not refuse a duplicate event id")

    paused = _FiniteExternalDriver()
    paused_event = {"event_id": "evt-inert-paused", "due_monotonic": 2.0}
    paused.schedule(paused_event)
    paused.set_state("paused")
    if paused.dispatch_due(2.0) or paused.dispatch_count != 0:
        failures.append("paused inert driver produced an invocation")
    if paused.codex_process_invocations != 0:
        failures.append("paused inert driver started a Codex process")

    stopped = _FiniteExternalDriver()
    stopped_event = {"event_id": "evt-inert-stopped", "due_monotonic": 3.0}
    stopped.schedule(stopped_event)
    stopped.set_state("stopped")
    if stopped.dispatch_due(3.0) or stopped.dispatch_count != 0:
        failures.append("stopped inert driver produced an invocation")
    if stopped.codex_process_invocations != 0:
        failures.append("stopped inert driver started a Codex process")
    if stopped.set_state("running"):
        failures.append("terminal inert driver stop was reactivated")
    return failures


def _selfcheck_cleanup() -> list[str]:
    """Exercise timeout cleanup wiring with an inert process/identity double."""
    failures: list[str] = []

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return self.returncode

    identity = {"pid": 4242, "boot_id": "inert", "starttime": 7, "pgid": 4242}
    process = FakeProcess()
    calls = []
    alive_states = iter((True, False))

    def fake_terminate(observed_identity, *, grace_seconds):
        calls.append((observed_identity, grace_seconds))
        return {"attempted": True, "terminated": True, "killed": False}

    cleanup = _cleanup_owned_process(
        process,
        identity,
        timed_out=True,
        terminate=fake_terminate,
        alive_probe=lambda _identity: next(alive_states),
    )
    if len(calls) != 1 or calls[0][0] is not identity:
        failures.append("timeout cleanup did not pass the exact recorded process identity")
    if cleanup["broad_kill_used"] is not False or cleanup["termination_attempted"] is not True:
        failures.append("timeout cleanup used a broad kill or omitted its exact-identity attempt")
    if cleanup["process_group_cleanup_attempted"] is not True or cleanup["termination_target_scope"] != "recorded_process_group":
        failures.append("timeout cleanup did not target the recorded process group")
    if cleanup["leader_reaped"] is not True or cleanup["identity_alive_after_cleanup"] is not False:
        failures.append("inert exact-identity cleanup did not record a reaped leader")
    return failures


def _selfcheck_argv() -> list[str]:
    failures: list[str] = []
    fresh = _codex_argv(
        "codex", "provider/model", "medium", Path("/tmp/fixture-workspace"), "<prompt>", None
    )
    resumed = _codex_argv(
        "codex", "provider/model", "medium", Path("/tmp/fixture-workspace"), "<prompt>", "thread-recorded"
    )
    for argv in (fresh, resumed):
        if "features.goals=true" not in argv or "--ignore-user-config" not in argv:
            failures.append("Codex invocation omitted the per-process goals override or config isolation")
        if "--ephemeral" in argv or "--last" in argv:
            failures.append("Codex invocation selected ephemeral history or an implicit last-thread target")
        if argv.index("--sandbox") > argv.index("exec"):
            failures.append("Codex global sandbox option appears after the exec subcommand")
    if resumed[resumed.index("exec") + 1:resumed.index("exec") + 3] != ["resume", "--json"]:
        failures.append("resume-specific JSON options are not placed after exec resume")
    if resumed[-2:] != ["thread-recorded", "<prompt>"]:
        failures.append("exact recorded thread ID and prompt are not the final resume arguments")
    return failures


def _parent_exit_before_child_passed(evidence: dict, parent_child_cli_ok: bool) -> bool:
    return bool(
        parent_child_cli_ok is True
        and isinstance(evidence, dict)
        and evidence.get("distinct_thread_ids") is True
        and evidence.get("parent_exited_before_child_started") is True
    )


def _selfcheck_parent_exit_evidence() -> list[str]:
    failures: list[str] = []
    evidence = {
        "distinct_thread_ids": True,
        "parent_exited_before_child_started": True,
    }
    if not _parent_exit_before_child_passed(evidence, True):
        failures.append("successful parent/child CLI evidence with distinct native threads was rejected")
    if _parent_exit_before_child_passed(evidence, False):
        failures.append("a failed child bootstrap passed parent-exit-before-child-continuation")
    if _parent_exit_before_child_passed({**evidence, "distinct_thread_ids": False}, True):
        failures.append("missing or non-distinct native thread IDs passed parent-exit-before-child-continuation")
    if _parent_exit_before_child_passed({**evidence, "parent_exited_before_child_started": False}, True):
        failures.append("missing parent-exit ordering passed parent-exit-before-child-continuation")
    return failures


def _selfcheck_admission() -> list[str]:
    """Exercise path admission with temporary ignored-scratch fixtures only."""
    failures: list[str] = []
    scratch = REPO_ROOT / ".tmp"
    if scratch.is_symlink() or not scratch.is_dir():
        return ["admission selfcheck requires the existing non-symlink ignored .tmp directory"]

    try:
        with tempfile.TemporaryDirectory(prefix="codex-admission-selfcheck-", dir=scratch) as temp_name:
            base = Path(temp_name)
            user_home = base / "fake-codex-home"
            user_home.mkdir()
            (user_home / "AGENTS.md").write_text("selfcheck-only instruction", encoding="utf-8")
            environ = {"CODEX_HOME": str(user_home)}

            def args_for(workspace: Path, state: Path | None = None, output: Path | None = None):
                return argparse.Namespace(
                    workspace=str(workspace),
                    state=str(state if state is not None else base / "state"),
                    output=str(output if output is not None else base / "record.json"),
                )

            def expect_rejection(label: str, args: argparse.Namespace, expected: str) -> None:
                try:
                    _resolved_paths(args, environ=environ)
                except FixtureError as exc:
                    if expected not in str(exc):
                        failures.append(f"{label} returned an unexpected admission refusal")
                else:
                    failures.append(f"{label} was not refused")

            workspace = base / "workspace"
            state = base / "state"
            output = base / "record.json"
            if not _git_path_is_ignored(base / "scratch-policy-check"):
                failures.append(".tmp admission fixture path is not ignored by repository Git rules")
            try:
                admitted = _resolved_paths(args_for(workspace, state, output), environ=environ)
            except FixtureError:
                failures.append("fresh, separate workspace/state/output paths under ignored .tmp were rejected")
            else:
                expected_paths = tuple(path.resolve() for path in (workspace, state, output))
                if admitted[:3] != expected_paths:
                    failures.append("fresh ignored-.tmp paths did not resolve to their distinct destinations")
                expected_environment = {
                    "os_platform": sys.platform,
                    "user_agents_md_present": True,
                }
                if admitted[3] != expected_environment:
                    failures.append("inherited user AGENTS.md presence was not retained as path-free metadata")

            try:
                _prepare_workspace(base / "verified-git-root")
            except (FixtureError, OSError, subprocess.SubprocessError):
                failures.append("nested isolated Git root was not created and verified")

            expect_rejection(
                "workspace/state ancestor overlap",
                args_for(base / "overlap", base / "overlap" / "state", base / "overlap-record.json"),
                "workspace_state_output_paths_must_not_overlap",
            )
            expect_rejection(
                "state/output equality",
                args_for(base / "separate-workspace", base / "same-path", base / "same-path"),
                "workspace_state_output_paths_must_not_overlap",
            )

            existing_workspace = base / "existing-workspace"
            existing_workspace.mkdir()
            expect_rejection(
                "existing workspace",
                args_for(existing_workspace, base / "new-state", base / "new-output.json"),
                "existing_workspace_directory_refused_to_preserve_prior_evidence",
            )
            existing_state = base / "existing-state"
            existing_state.mkdir()
            expect_rejection(
                "existing state directory",
                args_for(base / "new-workspace", existing_state, base / "another-output.json"),
                "existing_state_directory_refused_to_preserve_prior_evidence",
            )
            existing_output = base / "existing-output.json"
            existing_output.write_text("preserve this fixture evidence", encoding="utf-8")
            expect_rejection(
                "existing output file",
                args_for(base / "another-workspace", base / "another-state", existing_output),
                "existing_output_file_refused_to_preserve_prior_evidence",
            )
            if existing_output.read_text(encoding="utf-8") != "preserve this fixture evidence":
                failures.append("existing output evidence changed during admission refusal")

            expect_rejection(
                "source checkout root",
                args_for(REPO_ROOT, base / "source-state", base / "source-output.json"),
                "workspace_must_be_fresh_under_ignored_tmp_or_system_temp_outside_checkout",
            )

            escaped_workspace = base / "workspace-symlink-escape"
            escaped_workspace.symlink_to(REPO_ROOT, target_is_directory=True)
            expect_rejection(
                "workspace symlink escape",
                args_for(escaped_workspace, base / "escape-state", base / "escape-output.json"),
                "workspace_path_symlink_escapes_ignored_tmp",
            )
            escaped_state = base / "state-symlink-escape"
            escaped_state.symlink_to(REPO_ROOT, target_is_directory=True)
            expect_rejection(
                "state symlink escape",
                args_for(base / "safe-workspace", escaped_state, base / "safe-output.json"),
                "state_path_symlink_escapes_ignored_tmp",
            )
            escaped_output = base / "output-symlink-escape"
            escaped_output.symlink_to(REPO_ROOT, target_is_directory=True)
            expect_rejection(
                "output symlink escape",
                args_for(base / "another-safe-workspace", base / "another-safe-state", escaped_output),
                "output_path_symlink_escapes_ignored_tmp",
            )

            system_temp = Path(tempfile.gettempdir()).resolve()
            external_workspace = system_temp / f"codex-admission-selfcheck-{os.getpid()}-{secrets.token_hex(6)}"
            try:
                external_admitted = _resolved_paths(
                    args_for(external_workspace, base / "system-temp-state", base / "system-temp-output.json"),
                    environ=environ,
                )
            except FixtureError:
                failures.append("fresh system-temp workspace was rejected")
            else:
                if external_admitted[0] != external_workspace or external_workspace.exists():
                    failures.append("system-temp admission check did not remain a fresh, uncreated destination")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        failures.append("admission selfcheck could not complete its temporary path cases")
    return failures


def selfcheck() -> int:
    """Deterministic synthetic checks only; never launches Codex or a model."""
    failures = (
        event_selfcheck()
        + _selfcheck_admission()
        + _selfcheck_parent_exit_evidence()
        + _selfcheck_driver()
        + _selfcheck_cleanup()
        + _selfcheck_argv()
    )
    for failure in failures:
        print(f"FAIL: inert-synthetic-selfcheck: {failure}")
    if failures:
        print(f"\n{len(failures)} inert-synthetic-selfcheck failure(s)")
        return 1
    print("note: inert-synthetic-selfcheck passed; no Codex process or model was started")
    print("ok")
    return 0


def _wait_until(target_monotonic: float, hard_deadline_monotonic: float) -> bool:
    while True:
        now = time.monotonic()
        if now >= target_monotonic:
            return True
        if now >= hard_deadline_monotonic:
            return False
        time.sleep(min(0.05, target_monotonic - now, hard_deadline_monotonic - now))


def _deadline_event(event_id: str, target_thread_id: str, delay: float) -> dict:
    now_mono = time.monotonic()
    due_mono = now_mono + delay
    due_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)
    return {
        "event_id": event_id,
        "target_thread_id": target_thread_id,
        "due_monotonic": due_mono,
        "due_at_utc": due_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def _blocked_window(
    driver: _FiniteExternalDriver,
    *,
    state: str,
    event_id: str,
    target_thread_id: str,
    hard_deadline_monotonic: float,
) -> dict:
    event = _deadline_event(event_id, target_thread_id, delay=BLOCKED_DUE_WINDOW_SECONDS / 2)
    driver.set_state("running")
    queued = driver.schedule(event)
    driver.set_state(state)
    before = driver.dispatch_count
    process_invocations_before = driver.codex_process_invocations
    window_end = event["due_monotonic"] + BLOCKED_DUE_WINDOW_SECONDS
    waited = _wait_until(window_end, hard_deadline_monotonic)
    dispatched = driver.dispatch_due(time.monotonic()) if waited else []
    after = driver.dispatch_count
    return {
        "state": state,
        "event_id": event_id,
        "queued_before_state_change": queued,
        "due_at_utc": event["due_at_utc"],
        "window_seconds": BLOCKED_DUE_WINDOW_SECONDS,
        "dispatch_count_before": before,
        "dispatch_count_after": after,
        "codex_process_invocations_before": process_invocations_before,
        "codex_process_invocations_after": driver.codex_process_invocations,
        "zero_invocations": (
            waited
            and not dispatched
            and after == before
            and driver.codex_process_invocations == process_invocations_before
        ),
        "bounded_window_completed": waited,
    }


def _history_prompt(event_id: str) -> str:
    return (
        "Continue this exact Codex thread. Recall the one-time marker from the earlier history; "
        "this prompt intentionally does not contain it. Do not use tools, execute shell commands, "
        "access the network, use libraries, launch agents, or write files. Return only a JSON object "
        f"with exactly event_id and recalled_marker, using event_id {json.dumps(event_id)}."
    )


def _run_main(args: argparse.Namespace) -> int:
    if (
        not isinstance(args.model, str)
        or MODEL_SELECTOR_RE.fullmatch(args.model) is None
        or args.model.startswith("/")
        or any(part in {"", ".", ".."} for part in args.model.split("/"))
        or re.search(r"/(?:home|Users|var|tmp)/", args.model) is not None
    ):
        raise FixtureError("model_selector_invalid")
    if not isinstance(args.reasoning_effort, str) or EFFORT_SELECTOR_RE.fullmatch(args.reasoning_effort) is None:
        raise FixtureError("reasoning_effort_selector_invalid")
    if not isinstance(args.timeout_seconds, (int, float)) or args.timeout_seconds <= 0:
        raise FixtureError("per_process_timeout_must_be_positive")
    workspace, state, output_path, environment_metadata = _resolved_paths(args)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.mkdir(exist_ok=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    hard_deadline_monotonic = time.monotonic() + HARD_DEADLINE_SECONDS
    run_started_at = _utc_now()
    run_started_monotonic = time.monotonic()
    record: dict = {
        "schema_version": 1,
        "fixture_id": "codex-lifecycle-marker-v1",
        "provenance": "fixture-attempt-without-native-observation",
        "verdict": "failed",
        "started_at": run_started_at,
        "ended_at": None,
        "codex_version": None,
        "version_exit_code": None,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "environment": environment_metadata,
        "target_wall_seconds": TARGET_WALL_SECONDS,
        "hard_wall_seconds": HARD_DEADLINE_SECONDS,
        "per_process_timeout_seconds": float(args.timeout_seconds),
        "source_hashes": _source_hashes(),
        "workspace_identity_sha256": _sha256_bytes(str(workspace).encode("utf-8")),
        "state_identity_sha256": _sha256_bytes(str(state).encode("utf-8")),
        "allowed_cli_argv": [],
        "cli_exit_codes": [],
        "processes": [],
        "observed_native_tools": [],
        "native_goal_task_observation": {"status": "unknown", "events": []},
        "process_session_evidence": {
            "parent_thread_id": None,
            "child_thread_id": None,
            "distinct_thread_ids": False,
            "distinct_cli_process_identities": False,
            "parent_exited_before_child_started": False,
            "maximum_concurrent_codex_processes": 1,
            "exact_resume_targets_consistent": False,
            "sequential_scope_only": True,
            "parent_created_concurrent_daemon_claimed": False,
        },
        "history_recall": {
            "session_history_only": True,
            "global_memories_tested": False,
            "marker_sha256": None,
            "marker_written_to_workspace_before_resumes": None,
            "resume_prompts_omitted_marker": None,
            "checks": [],
        },
        "external_scheduler": {
            "type": "external-finite-driver",
            "native_codex_scheduler_claimed": False,
            "dispatches": [],
            "paused_window": None,
            "stopped_window": None,
            "duplicate_event_refusal": None,
            "active_process_pause_or_kill_tested": False,
            "openforage_callbacks_invoked": False,
            "search_simulation_or_submission_invoked": False,
        },
        "cleanup": {"all_started_process_leaders_reaped": True, "per_process": []},
        "unknowns": [
            "native scheduler and active-process pause/stop",
            "global memory; marker checks concern only resumed thread history",
            "parent-kill, hangup, reboot, and concurrent-daemon survival",
            "crash recovery and duplicate Codex process ownership",
            "OpenForage callbacks, search, simulation, or submission",
        ],
        "failures": [],
    }
    failures: list[str] = []
    processes: list[dict] = []
    workspace_created = False
    try:
        _prepare_workspace(workspace)
        workspace_created = True
    except (FixtureError, OSError, subprocess.SubprocessError):
        failures.append("isolated_workspace_setup_failed")

    if workspace_created:
        codex_version, version_exit_code = _safe_codex_version(args.codex_binary)
        record["codex_version"] = codex_version
        record["version_exit_code"] = version_exit_code
        if codex_version is None or version_exit_code != 0:
            failures.append("codex_version_probe_failed")
    if workspace_created:
        run_id = secrets.token_hex(8)
        parent_prompt = (
            "This is a bounded process/session identity control. Do not use tools, execute shell "
            "commands, access the network, use libraries, launch agents, or write files. Return only "
            "the JSON object {\"parent_ready\":true}."
        )
        parent = _run_codex_call(
            phase="parent",
            binary=args.codex_binary,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workspace=workspace,
            state=state,
            repo=REPO_ROOT,
            prompt=parent_prompt,
            target_thread_id=None,
            process_timeout=float(args.timeout_seconds),
            hard_deadline_monotonic=hard_deadline_monotonic,
        )
        processes.append(parent)
        parent_message = parent.pop("_final_json")
        parent.pop("_message", None)
        parent.pop("_events", None)
        if parent["exit_code"] != 0:
            failures.append("parent_cli_exit_nonzero_or_missing")
        if parent["thread_id"] is None:
            failures.append("parent_thread_id_missing")
        if parent_message != {"parent_ready": True}:
            failures.append("parent_structured_acknowledgment_invalid")

        bootstrap_event_id = f"evt-bootstrap-{run_id}"
        marker = secrets.token_urlsafe(24)
        record["history_recall"]["marker_sha256"] = _sha256_bytes(marker.encode("utf-8"))
        child_prompt = (
            "This is a bounded child-session history fixture. Remember the one-time marker below "
            "in this exact thread history, but do not write it to any file. If a native Codex goal or "
            "task mechanism is exposed, use it to create a goal acknowledging readiness and complete "
            "that goal this turn; do not simulate such a mechanism with prose or a plan. Do not use "
            "shell commands, access the network, use libraries, launch agents, or write files. Reply "
            "only with a JSON object containing exactly ready and event_id, with ready true and the "
            "bootstrap event id below.\n"
            f"One-time marker: {marker}\n"
            f"Bootstrap event id: {bootstrap_event_id}"
        )
        child = _run_codex_call(
            phase="child-bootstrap",
            binary=args.codex_binary,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workspace=workspace,
            state=state,
            repo=REPO_ROOT,
            prompt=child_prompt,
            target_thread_id=None,
            process_timeout=float(args.timeout_seconds),
            hard_deadline_monotonic=hard_deadline_monotonic,
        )
        processes.append(child)
        child_message = child.pop("_final_json")
        child.pop("_message", None)
        child.pop("_events", None)
        if child["exit_code"] != 0:
            failures.append("child_bootstrap_cli_exit_nonzero_or_missing")
        child_thread_id = child["thread_id"]
        if child_thread_id is None:
            failures.append("child_thread_id_missing")
        if not readiness_ack_matches(child_message, bootstrap_event_id):
            failures.append("child_bootstrap_structured_acknowledgment_invalid")

        parent_process_id = parent.get("process_identity_fingerprint")
        child_process_id = child.get("process_identity_fingerprint")
        parent_exited_before_child = bool(
            parent.get("started_in_new_process_group") is True
            and child.get("started_in_new_process_group") is True
            and parent.get("exit_code") is not None
            and parent.get("cleanup", {}).get("leader_reaped") is True
            and parent["_ended_monotonic"] <= child["_started_monotonic"]
        )
        record["process_session_evidence"].update({
            "parent_thread_id": parent.get("thread_id"),
            "child_thread_id": child_thread_id,
            "distinct_thread_ids": bool(parent.get("thread_id") and child_thread_id and parent["thread_id"] != child_thread_id),
            "distinct_cli_process_identities": bool(parent_process_id and child_process_id and parent_process_id != child_process_id),
            "parent_exited_before_child_started": parent_exited_before_child,
        })
        if not parent_exited_before_child:
            failures.append("parent_did_not_exit_before_child_start")
        if record["process_session_evidence"]["distinct_thread_ids"] is not True:
            failures.append("parent_and_child_thread_ids_not_distinct")
        if record["process_session_evidence"]["distinct_cli_process_identities"] is not True:
            failures.append("parent_and_child_process_identities_not_distinct")

        if child_thread_id is not None:
            absent_before_resumes = _marker_absent_from_workspace(workspace, marker)
            record["history_recall"]["marker_written_to_workspace_before_resumes"] = not absent_before_resumes
            if not absent_before_resumes:
                failures.append("history_marker_found_in_workspace_before_resume")
            history_checks: list[dict] = []
            for index in (1, 2):
                event_id = f"evt-history-{index}-{run_id}"
                prompt = _history_prompt(event_id)
                prompt_omits_marker = marker not in prompt
                if not prompt_omits_marker:
                    failures.append(f"history_resume_{index}_prompt_contains_marker")
                marker_absent_before_resume = _marker_absent_from_workspace(workspace, marker)
                if not marker_absent_before_resume:
                    failures.append(f"history_marker_found_in_workspace_before_resume_{index}")
                resumed = _run_codex_call(
                    phase=f"history-resume-{index}",
                    binary=args.codex_binary,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    workspace=workspace,
                    state=state,
                    repo=REPO_ROOT,
                    prompt=prompt,
                    target_thread_id=child_thread_id,
                    process_timeout=float(args.timeout_seconds),
                    hard_deadline_monotonic=hard_deadline_monotonic,
                )
                processes.append(resumed)
                message = resumed.pop("_final_json")
                resumed.pop("_message", None)
                resumed.pop("_events", None)
                ack = history_ack_matches(message, event_id, marker)
                returned_marker = message.get("recalled_marker") if isinstance(message, dict) else None
                target_matches = exact_resume_matches(
                    resumed.get("target_thread_id"), child_thread_id, resumed.get("thread_id")
                )
                check = {
                    "phase": f"history-resume-{index}",
                    "event_id": event_id,
                    "target_thread_id": child_thread_id,
                    "returned_thread_id": resumed.get("thread_id"),
                    "target_matches_recorded_child": resumed.get("target_thread_id") == child_thread_id,
                    "returned_thread_matches_target": resumed.get("thread_id") == resumed.get("target_thread_id"),
                    "event_id_matches": isinstance(message, dict) and message.get("event_id") == event_id,
                    "marker_matches": ack,
                    "marker_sha256": record["history_recall"]["marker_sha256"],
                    "returned_marker_sha256": (
                        _sha256_bytes(returned_marker.encode("utf-8")) if isinstance(returned_marker, str) else None
                    ),
                    "marker_absent_before_resume": marker_absent_before_resume,
                    "resume_prompt_omitted_marker": prompt_omits_marker,
                    "cli_exit_code": resumed.get("exit_code"),
                }
                history_checks.append(check)
                if resumed.get("exit_code") != 0:
                    failures.append(f"history_resume_{index}_cli_exit_nonzero_or_missing")
                if not target_matches:
                    failures.append(f"history_resume_{index}_exact_target_mismatch")
                if not ack:
                    failures.append(f"history_resume_{index}_acknowledgment_mismatch")

            driver = _FiniteExternalDriver()
            dispatch_event_id = f"evt-due-{run_id}"
            due_event = _deadline_event(dispatch_event_id, child_thread_id, EXTERNAL_DUE_DELAY_SECONDS)
            queued = driver.schedule(due_event)
            due_wait_completed = queued and _wait_until(due_event["due_monotonic"], hard_deadline_monotonic)
            due_dispatches = driver.dispatch_due(time.monotonic()) if due_wait_completed else []
            dispatch_record = {
                "event_id": dispatch_event_id,
                "due_at_utc": due_event["due_at_utc"],
                "target_thread_id": child_thread_id,
                "target_matches_recorded_child": False,
                "returned_thread_id": None,
                "acknowledgment_matches": False,
                "cli_exit_code": None,
                "dispatch_count": len(due_dispatches),
            }
            if not due_dispatches:
                failures.append("external_due_event_not_dispatched")
            else:
                dispatched = due_dispatches[0]
                dispatched_event_id = dispatched["event_id"]
                prompt = _history_prompt(dispatched_event_id)
                driver.codex_process_invocations += 1
                marker_absent_before_due_resume = _marker_absent_from_workspace(workspace, marker)
                if not marker_absent_before_due_resume:
                    failures.append("history_marker_found_in_workspace_before_external_due_resume")
                resumed = _run_codex_call(
                    phase="external-due-resume",
                    binary=args.codex_binary,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    workspace=workspace,
                    state=state,
                    repo=REPO_ROOT,
                    prompt=prompt,
                    target_thread_id=dispatched["target_thread_id"],
                    process_timeout=float(args.timeout_seconds),
                    hard_deadline_monotonic=hard_deadline_monotonic,
                )
                processes.append(resumed)
                message = resumed.pop("_final_json")
                resumed.pop("_message", None)
                resumed.pop("_events", None)
                returned_marker = message.get("recalled_marker") if isinstance(message, dict) else None
                ack = history_ack_matches(message, dispatched_event_id, marker)
                target_matches = exact_resume_matches(
                    resumed.get("target_thread_id"), child_thread_id, resumed.get("thread_id")
                )
                dispatch_record.update({
                    "target_matches_recorded_child": target_matches,
                    "returned_thread_id": resumed.get("thread_id"),
                    "acknowledgment_matches": ack,
                    "returned_marker_sha256": (
                        _sha256_bytes(returned_marker.encode("utf-8")) if isinstance(returned_marker, str) else None
                    ),
                    "marker_sha256": record["history_recall"]["marker_sha256"],
                    "marker_absent_before_resume": marker_absent_before_due_resume,
                    "cli_exit_code": resumed.get("exit_code"),
                })
                history_checks.append({
                    "phase": "external-due-resume",
                    "event_id": dispatched_event_id,
                    "target_thread_id": child_thread_id,
                    "returned_thread_id": resumed.get("thread_id"),
                    "target_matches_recorded_child": target_matches,
                    "returned_thread_matches_target": resumed.get("thread_id") == child_thread_id,
                    "event_id_matches": isinstance(message, dict) and message.get("event_id") == dispatched_event_id,
                    "marker_matches": ack,
                    "marker_sha256": record["history_recall"]["marker_sha256"],
                    "returned_marker_sha256": dispatch_record.get("returned_marker_sha256"),
                    "resume_prompt_omitted_marker": marker not in prompt,
                    "cli_exit_code": resumed.get("exit_code"),
                })
                if resumed.get("exit_code") != 0:
                    failures.append("external_due_resume_cli_exit_nonzero_or_missing")
                if not target_matches:
                    failures.append("external_due_resume_exact_target_mismatch")
                if not ack:
                    failures.append("external_due_resume_acknowledgment_mismatch")

            duplicate_refused = not driver.schedule(due_event)
            paused_window = _blocked_window(
                driver,
                state="paused",
                event_id=f"evt-paused-{run_id}",
                target_thread_id=child_thread_id,
                hard_deadline_monotonic=hard_deadline_monotonic,
            )
            stopped_window = _blocked_window(
                driver,
                state="stopped",
                event_id=f"evt-stopped-{run_id}",
                target_thread_id=child_thread_id,
                hard_deadline_monotonic=hard_deadline_monotonic,
            )
            driver.set_state("stopped")
            record["external_scheduler"].update({
                "driver_state_at_end": driver.state,
                "dispatches": [dispatch_record],
                "paused_window": paused_window,
                "stopped_window": stopped_window,
                "duplicate_event_refusal": {
                    "refused": duplicate_refused,
                    "refusal_count": driver.duplicate_refusals,
                    "evidence_scope": "deterministic external-driver control only",
                },
                "active_process_pause_or_kill_tested": False,
                "external_driver_invocation_count": driver.dispatch_count,
                "external_driver_codex_process_invocation_count": driver.codex_process_invocations,
            })
            if not duplicate_refused:
                failures.append("external_driver_duplicate_event_not_refused")
            if not paused_window["zero_invocations"]:
                failures.append("external_driver_paused_due_window_invoked")
            if not stopped_window["zero_invocations"]:
                failures.append("external_driver_stopped_due_window_invoked")
            if not due_wait_completed:
                failures.append("external_due_window_exceeded_hard_deadline")
            record["history_recall"]["checks"] = history_checks
            record["history_recall"]["resume_prompts_omitted_marker"] = all(
                item.get("resume_prompt_omitted_marker") is True for item in history_checks
            )
            record["process_session_evidence"]["exact_resume_targets_consistent"] = bool(
                history_checks
                and all(
                    item["target_matches_recorded_child"]
                    and item["returned_thread_matches_target"]
                    for item in history_checks
                )
            )
        else:
            failures.append("history_fixture_stopped_without_recorded_child_thread_id")

    public_processes = []
    for process in processes:
        record["allowed_cli_argv"].append(process["argv"])
        record["cli_exit_codes"].append({"phase": process["phase"], "exit_code": process["exit_code"]})
        public_processes.append({key: value for key, value in process.items() if not key.startswith("_") and key != "process_identity_fingerprint"})
        record["cleanup"]["per_process"].append({"phase": process["phase"], **process["cleanup"]})
    record["processes"] = public_processes
    record["cleanup"]["all_started_process_leaders_reaped"] = all(
        process["cleanup"].get("leader_reaped") is True
        and process["cleanup"].get("identity_alive_after_cleanup") is not True
        for process in processes
    )
    if not record["cleanup"]["all_started_process_leaders_reaped"]:
        failures.append("owned_codex_process_cleanup_incomplete")

    all_tools = [
        tool
        for process in processes
        for tool in process.get("observed_native_tools", [])
        if isinstance(tool, dict) and isinstance(tool.get("tool"), str) and isinstance(tool.get("status"), str)
    ]
    record["observed_native_tools"] = all_tools[:MAX_NATIVE_TOOL_OBSERVATIONS]
    goal_task_events = [
        item for item in record["observed_native_tools"]
        if re.search(r"goal|task", item["tool"], re.I)
    ]
    record["native_goal_task_observation"] = {
        "status": "observed" if goal_task_events else "unknown",
        "events": goal_task_events,
        "feature_flag_is_operation_evidence": False,
        "model_prose_is_tool_evidence": False,
    }
    record["process_session_evidence"]["distinct_cli_process_identities"] = bool(
        len(processes) >= 2
        and processes[0].get("process_identity_fingerprint") is not None
        and processes[1].get("process_identity_fingerprint") is not None
        and processes[0]["process_identity_fingerprint"] != processes[1]["process_identity_fingerprint"]
    )
    record["process_session_evidence"]["distinct_thread_ids"] = bool(
        record["process_session_evidence"]["parent_thread_id"]
        and record["process_session_evidence"]["child_thread_id"]
        and record["process_session_evidence"]["parent_thread_id"] != record["process_session_evidence"]["child_thread_id"]
    )

    external = record["external_scheduler"]
    duplicate_control = external.get("duplicate_event_refusal")
    paused_control = external.get("paused_window")
    stopped_control = external.get("stopped_window")
    dispatches = external.get("dispatches")
    due_dispatch_passed = bool(
        isinstance(dispatches, list)
        and dispatches
        and isinstance(dispatches[0], dict)
        and dispatches[0].get("acknowledgment_matches") is True
        and dispatches[0].get("target_matches_recorded_child") is True
        and dispatches[0].get("cli_exit_code") == 0
    )
    parent_child_cli_ok = bool(
        len(processes) >= 2
        and processes[0].get("exit_code") == 0
        and processes[1].get("exit_code") == 0
    )
    history_recall_passed = bool(
        record["history_recall"]["checks"]
        and all(
            item.get("marker_matches") is True
            and item.get("cli_exit_code") == 0
            and item.get("target_matches_recorded_child") is True
            and item.get("returned_thread_matches_target") is True
            for item in record["history_recall"]["checks"]
        )
    )
    core_passed = (
        record["process_session_evidence"]["distinct_thread_ids"]
        and record["process_session_evidence"]["distinct_cli_process_identities"]
        and record["process_session_evidence"]["parent_exited_before_child_started"]
        and record["process_session_evidence"]["exact_resume_targets_consistent"]
        and len(record["history_recall"]["checks"]) == 3
        and history_recall_passed
        and record["history_recall"]["resume_prompts_omitted_marker"] is True
        and record["history_recall"]["marker_written_to_workspace_before_resumes"] is False
        and isinstance(duplicate_control, dict)
        and duplicate_control.get("refused") is True
        and isinstance(paused_control, dict)
        and paused_control.get("zero_invocations") is True
        and isinstance(stopped_control, dict)
        and stopped_control.get("zero_invocations") is True
        and record["cleanup"]["all_started_process_leaders_reaped"]
        and not failures
    )
    record["capability_results"] = {
        "distinct-session-identity": "passed" if record["process_session_evidence"]["distinct_thread_ids"] and parent_child_cli_ok else "failed",
        "separate-cli-invocation": "passed" if record["process_session_evidence"]["distinct_cli_process_identities"] and parent_child_cli_ok else "failed",
        "parent-exit-before-child-continuation": (
            "passed"
            if _parent_exit_before_child_passed(record["process_session_evidence"], parent_child_cli_ok)
            else "failed"
        ),
        "exact-id-history-continuation": "passed" if record["process_session_evidence"]["exact_resume_targets_consistent"] and history_recall_passed else "failed",
        "history-marker-recall": "passed" if history_recall_passed else "failed",
        "native-goal-task-operation": "observed" if goal_task_events else "unknown",
        "external-finite-driver": "passed" if due_dispatch_passed else "failed",
        "active-pause-stop": "unknown",
        "global-memory": "unknown",
    }
    record["provenance"] = "actual-native-observation" if any(process.get("native_event_count", 0) for process in processes) else "fixture-attempt-without-native-observation"
    record["verdict"] = "passed" if core_passed else "failed"
    record["failures"] = failures
    record["ended_at"] = _utc_now()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": output_path.relative_to(REPO_ROOT).as_posix(),
        "verdict": record["verdict"],
        "provenance": record["provenance"],
        "failures": record["failures"],
    }, indent=2))
    return 0 if record["verdict"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="caller-selected Codex model; there is no default model invocation")
    parser.add_argument("--reasoning-effort", help="caller-selected model reasoning-effort value")
    parser.add_argument("--workspace", help="new isolated workspace under ignored .tmp or system temp outside the checkout")
    parser.add_argument("--state", help="new raw-event state directory under ignored .tmp")
    parser.add_argument("--output", help="new sanitized public-record path under ignored .tmp")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_PROCESS_TIMEOUT_SECONDS,
                        help="timeout for each Codex CLI process; the fixture also has a 40-minute hard deadline")
    parser.add_argument("--codex-binary", default="codex", help="Codex CLI executable name or path")
    parser.add_argument("--selfcheck", action="store_true", help="run inert synthetic event/admission checks; no Codex process")
    args = parser.parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    for name in ("model", "reasoning_effort", "workspace", "state", "output"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required unless --selfcheck is used")
    try:
        return _run_main(args)
    except FixtureError as exc:
        print(f"FAIL: Codex lifecycle fixture: {exc}")
        return 2
    except (OSError, subprocess.SubprocessError):
        print("FAIL: Codex lifecycle fixture: setup or process-control error")
        return 2


if __name__ == "__main__":
    sys.exit(main())
