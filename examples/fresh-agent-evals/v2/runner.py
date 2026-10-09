#!/usr/bin/env python3
"""Explicit native OpenCode/Codex runner for one frozen contribution trial."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO_DEFAULT / "scripts"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from onboarding.completion import (  # noqa: E402
    fixture_deliverable,
    load_fixture_contract,
    render_finish_commands,
)

from grader import GradeError, grade_trial  # noqa: E402
from diagnostics import build_failure_diagnostics, runtime_unavailable_diagnostic  # noqa: E402
from manifest import ManifestError, verify_manifest  # noqa: E402
from native_events import normalize_codex, normalize_opencode  # noqa: E402
from opencode_profile import ProfileError, special_project_dirs, verify_effective_profile  # noqa: E402
from paths import PathSafetyError, exclusive_json, make_exclusive_directory, mkdir_chain_no_symlinks, raw_file_exclusive  # noqa: E402
from processes import run_bounded  # noqa: E402
from schema import read_json  # noqa: E402
from snapshot import SnapshotError, file_map, prepare_snapshot  # noqa: E402

RUNS_REL = ".tmp/native-contribution-wave/runs"
WORK_PARENT = Path(tempfile.gettempdir()) / "openforage-native-contribution-v2" / "workspaces"
EXPECTED_VERSIONS = {"opencode": "1.18.31", "codex": "0.156.1"}
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
MODEL_SELECTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*){0,4}\Z")
ARG_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SECRET_ARGUMENT_RE = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}|bearer\s+\S+)")
MAX_TIMEOUT = 900.0


def _utc(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def requested_argument(value: str | None, kind: str) -> dict:
    """Retain safe explicit CLI selectors; unsafe values are represented only by digest."""
    if value is None:
        return {"status": "not_applicable", "source": "explicit_cli_argv"}
    pattern = MODEL_SELECTOR_RE if kind == "model" else ARG_LABEL_RE
    if len(value) <= 192 and pattern.fullmatch(value) and not SECRET_ARGUMENT_RE.search(value):
        return {
            "status": "requested_only",
            "value": value,
            "source": "explicit_cli_argv",
            "backend_served_value": "not_observed",
        }
    return {
        "status": "unknown",
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "unknown_reason": "unsafe selector shape or secret-like value; raw argument omitted",
        "source": "explicit_cli_argv",
        "backend_served_value": "not_observed",
    }


def _child_environment() -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Keep the invoked venv/bin path, not its resolved symlink target.
    env["PATH"] = str(Path(os.path.abspath(sys.executable)).parent) + os.pathsep + env.get("PATH", "")
    return env


def _harness_version(binary: str, harness: str, workspace: Path, env: dict) -> tuple[str | None, dict]:
    try:
        result = run_bounded([binary, "--version"], cwd=workspace, env=env, timeout=30)
    except OSError as exc:
        return None, {"status": "unavailable", "error_kind": type(exc).__name__}
    text = (result.stdout + result.stderr).decode("utf-8", "replace")
    match = VERSION_RE.search(text)
    version = match.group(1) if match else None
    return version, {
        "status": "observed" if result.returncode == 0 and version else "unknown",
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "process_group_remaining": result.process_group_remaining,
    }


def render_prompt(case: dict, schema: dict, workspace: Path, repo: Path = REPO_DEFAULT) -> str:
    completion_contract = None
    contract_text = ""
    if case["case_id"] == "first-finished-contribution-primary":
        completion_contract = load_fixture_contract(repo, "contribute", case["case_id"])
        commands = render_finish_commands(completion_contract, "contribute", None)
        command_by_id = {item["check_id"]: item["command"] for item in commands}
        completion_rel = fixture_deliverable(repo, "contribute", case["case_id"], "completion.json")
        contract_text = "\n".join(
            [
                "Exact public fixture contract from canonical onboarding:",
                "Available input paths:",
                *[f"  - {path}" for path in completion_contract["available_input_paths"]],
                "Required deliverables:",
                *[f"  - {path}" for path in completion_contract["deliverables"]],
                "Optional scratch (not a deliverable or artifact-hash input):",
                *[f"  - {path}" for path in completion_contract["scratch_paths"]],
                "Fixed finish-check IDs: " + ", ".join(completion_contract["fixed_finish_check_ids"]),
                "Required finish sequence:",
                *[f"  {index}. {step}" for index, step in enumerate(completion_contract["finish_sequence"], 1)],
                "Permitted finish commands:",
                *[f"  - {command_by_id[check_id]}" for check_id in completion_contract["fixed_finish_check_ids"]],
                f"Write `{completion_rel}` only after the final validator record exists. Use a repair loop: run `git diff --check`, then the exact completion-check command; if either fails or the checker reports a repair, fix the relevant artifact and rerun both in that order. Stop only after a successful completion checker, and make that successful checker the final check.",
            ]
        )
        selector = command_by_id["docs-completion-check"].removesuffix(" --check-completion")
    else:
        selector = "python scripts/start_here.py --task contribute"
    protocol = f"""Common trial protocol (finished-contribution-v2).
Workspace root: {workspace}

- Read, write, and execute only inside that isolated workspace. Its nearest Git root is the workspace itself; the root README.md, AGENTS.md, and CLAUDE.md are visible there.
- Use the measurable entry route before task work: complete native content reads of `README.md` and `AGENTS.md` to observed EOF must be the first content sources. If a native read reports a continuation offset, follow that exact offset until EOF before running `{selector}` or reading `docs/agents/start-here.md`; then complete the required read of `docs/agents/first-contribution.md`. The grader requires these ordered native observations; partial output and injected prompt text alone never satisfy a full read or route step.
- The public `scripts/start_here.py` is allowed read-only with one bounded generic task route or the exact task-bound fixture selector above, plus the exact fixed `--check-completion` route at the end; optional `--format text|json`/`--harness <bounded-name>` are not part of that final invocation. Do not use generation or other flags in a trial.
- Do not inspect the public suite's case, oracle, answer, or grader material directly. This is a public, instruction-isolated suite, not a blind security sandbox. A repository validator may inspect its own test sources during its normal checks.
- Do not access the source checkout, paths outside the workspace, the network, credentials, package installers, registration/search/submission commands, nested agents/models, or stage/commit/push commands.
- The completion artifact path and full JSON Schema follow. In `contribution.changed_paths`, list only changed public repository content; exclude the completion record itself and optional scratch. Use exact enum values discovered from repository documents; record validation commands relative to the workspace without absolute host paths.
- In `route.authoritative_sources`, list only canonical files actually consulted through completed, recorded read operations; automatically injected instructions may inform your work but are not independently proven read events for this field.
- Prefer native local file/read/edit tools and standalone repository validation commands. Bounded structured read/search arguments and filename-only list/glob discovery are distinct evidence types; discovery never proves content was read. v2 recognizes fully classified, quote-aware `&&` chains with only the exact harmless `PYTHONDONTWRITEBYTECODE=1` prefix, at most eight segments, and Codex's exact outer login-shell plus at most one nested login-shell wrapper. A shell read or validator segment is supported only by explicit native exit evidence; zero proves each segment in an admitted AND chain passed, while nonzero does not locate a validator failure. Unsupported shell forms remain unknown.
- Human review fields are prose only. They are not automated quality claims.
- Tool traces and the sandbox/profile are evidence boundaries, not proof of host-wide isolation.
"""
    return (
        protocol + "\n" + contract_text + "\n\nCase id: " + case["case_id"] + "\n\nTask:\n" + case["task"]
        + "\n\nWrite `trial-output/completion.json` with this JSON Schema:\n```json\n"
        + json.dumps(schema, indent=2, sort_keys=True) + "\n```\n"
    )


def _preflight_validator(repo: Path, workspace: Path, harness_env: dict, case_id: str) -> dict:
    validator = workspace / ".github/scripts/validate_agent_docs.py"
    oracle = read_json(repo / "examples/fresh-agent-evals/v2/oracle.json")
    allowed = oracle["grade_contract"]["accepted_baseline_validation_modes"][case_id]
    mode = "truthfulness" if "truthfulness" in allowed else "links"
    args = [sys.executable, str(validator), mode, "--root", str(workspace)]
    try:
        result = run_bounded(args, cwd=workspace, env=harness_env, timeout=180)
    except OSError as exc:
        return {"mode": mode, "status": "unknown", "exit_code": None, "error_kind": type(exc).__name__}
    return {
        "mode": mode,
        "command_signature": f"validate_agent_docs.py {mode} --root <prepared-workspace>",
        "status": "observed_failure" if result.returncode not in {None, 0} and not result.timed_out and not result.process_group_remaining else "passed" if result.returncode == 0 and not result.timed_out and not result.process_group_remaining else "unknown",
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "process_group_remaining": result.process_group_remaining,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }


def _clean_source_preflight(repo: Path, env: dict) -> dict:
    validator = repo / ".github/scripts/validate_agent_docs.py"
    try:
        result = run_bounded(
            [sys.executable, str(validator), "truthfulness", "links", "--root", str(repo)],
            cwd=repo, env=env, timeout=180,
        )
    except OSError as exc:
        return {"status": "unknown", "exit_code": None, "error_kind": type(exc).__name__}
    return {
        "modes": ["truthfulness", "links"],
        "command_signature": "validate_agent_docs.py truthfulness links --root <source-checkout>",
        "status": "pass" if result.returncode == 0 and not result.timed_out and not result.process_group_remaining else "fail",
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "process_group_remaining": result.process_group_remaining,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }


def _native_argv(harness: str, binary: str, model: str, workspace: Path,
                 prompt: str, variant: str | None, reasoning: str | None) -> list[str]:
    if harness == "opencode":
        return [
            binary, "run", "--pure", "--dir", str(workspace), "--model", model,
            "--variant", variant or "", "--agent", "build", "--format", "json", prompt,
        ]
    return [
        binary, "--no-daemon", "--ask-for-approval", "never", "--model", model,
        "--cd", str(workspace), "--sandbox", "workspace-write", "-c",
        f"model_reasoning_effort={reasoning}", "exec", "--json",
        "--ignore-user-config", prompt,
    ]


def _case_by_id(repo: Path, case_id: str) -> dict:
    cases = read_json(repo / "examples/fresh-agent-evals/v2/cases.json")
    for case in cases.get("cases", []):
        if isinstance(case, dict) and case.get("case_id") == case_id:
            return case
    raise ValueError("case id is not in the versioned contribution suite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=("opencode", "codex"))
    parser.add_argument("--case",
                        choices=("first-finished-contribution-primary", "first-finished-contribution-heldout"))
    parser.add_argument("--model", help="caller-selected model; never defaulted or archived verbatim")
    parser.add_argument("--variant", help="required for OpenCode; caller-selected variant")
    parser.add_argument("--reasoning-effort", help="required for Codex; caller-selected reasoning effort")
    parser.add_argument("--include-heldout", action="store_true",
                        help="explicitly permit the reserved held-out case")
    parser.add_argument("--run-id", help="unique evidence directory id; generated if omitted")
    parser.add_argument("--expected-snapshot-sha256",
                        help="refuse launch unless the prepared workspace exactly matches a prior comparison snapshot")
    parser.add_argument("--opencode-binary", default="opencode")
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--series-manifest", help="explicit named rubric manifest for this native run")
    parser.add_argument("--selfcheck", action="store_true", help="run offline parser/rubric/path regressions only")
    args = parser.parse_args(argv)
    if args.selfcheck:
        from selfcheck import selfcheck
        return selfcheck()
    for required in ("harness", "case", "model"):
        if getattr(args, required) is None:
            parser.error(f"--{required} is required unless --selfcheck is given")
    if not isinstance(args.model, str) or not args.model.strip() or any(x in args.model for x in "\0\r\n"):
        parser.error("--model must be a non-empty single argv value")
    if not args.series_manifest:
        parser.error("--series-manifest is required for native trials")
    for name in ("variant", "reasoning_effort"):
        value = getattr(args, name)
        if value is not None and (not value.strip() or any(char in value for char in "\0\r\n")):
            parser.error(f"--{name.replace('_', '-')} must be a non-empty single argv value")
    if args.harness == "opencode" and (not args.variant or args.reasoning_effort is not None):
        parser.error("OpenCode requires --variant and does not accept --reasoning-effort")
    if args.harness == "codex" and (not args.reasoning_effort or args.variant is not None):
        parser.error("Codex requires --reasoning-effort and does not accept --variant")
    if not (0 < args.timeout_seconds <= MAX_TIMEOUT):
        parser.error(f"--timeout-seconds must be > 0 and <= {MAX_TIMEOUT:g}")
    if args.expected_snapshot_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", args.expected_snapshot_sha256) is None:
        parser.error("--expected-snapshot-sha256 must be 64 lowercase hexadecimal characters")
    if args.case.endswith("heldout") and not args.include_heldout:
        parser.error("the held-out case is reserved; pass --include-heldout only for an independent check")

    repo = Path(args.repo).resolve()
    try:
        manifest, manifest_hash = verify_manifest(repo, args.series_manifest)
        case = _case_by_id(repo, args.case)
        schema = read_json(repo / "examples/fresh-agent-evals/v2/completion.schema.json")
        run_id = args.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:10]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) is None:
            raise PathSafetyError("run id must be a short safe identifier")
        run_parent = repo / RUNS_REL
        run_dir = make_exclusive_directory(run_parent / run_id, run_parent)
        mkdir_chain_no_symlinks(WORK_PARENT)
        workspace = WORK_PARENT / run_id
        env = _child_environment()
        clean_source = _clean_source_preflight(repo, env)
        if clean_source.get("status") != "pass":
            raise GradeError("clean source truthfulness/links preflight failed; no native trial launched")
        prepare = prepare_snapshot(repo, args.case, workspace, env)
        if args.expected_snapshot_sha256 is not None and prepare["snapshot_sha256"] != args.expected_snapshot_sha256:
            raise GradeError("prepared workspace differs from the caller-pinned comparison snapshot")
        baseline_preflight = _preflight_validator(repo, workspace, env, args.case)
        if baseline_preflight.get("status") != "observed_failure":
            raise GradeError("seeded fixture did not produce its expected real baseline validator failure")
    except (OSError, ValueError, ManifestError, SnapshotError, PathSafetyError, GradeError) as exc:
        message = f"v2 trial preflight failed: {type(exc).__name__}"
        diagnostics = build_failure_diagnostics([message], [], args.harness or "unknown", scope="docs", repo=repo)
        print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
        return 2

    binary = args.opencode_binary if args.harness == "opencode" else args.codex_binary
    version, version_probe = _harness_version(binary, args.harness, workspace, env)
    if (
        version != EXPECTED_VERSIONS[args.harness]
        or version_probe.get("status") != "observed"
        or version_probe.get("timed_out") is not False
        or version_probe.get("process_group_remaining") is not False
    ):
        diagnostic = runtime_unavailable_diagnostic(
            "The selected native runtime is unavailable or does not match the frozen adapter target.",
            repo=repo,
        )
        print(json.dumps({"status": "unavailable", "diagnostics": [diagnostic]}, indent=2))
        return 2
    profile = None
    child_env = dict(env)
    if args.harness == "opencode":
        try:
            profile = verify_effective_profile(binary, workspace, child_env)
        except ProfileError as exc:
            message = f"OpenCode effective named-agent profile could not be verified: {type(exc).__name__}"
            diagnostics = build_failure_diagnostics([message], [], args.harness, scope="docs", repo=repo, workspace=workspace)
            print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
            return 2
        from opencode_profile import config_text
        child_env["OPENCODE_CONFIG_CONTENT"] = config_text()
    else:
        profile = {
            "profile_id": "codex-workspace-write",
            "sandbox": "workspace-write",
            "approval": "never",
            "no_daemon": True,
            "ignore_user_config_flag": True,
            "rules_and_auth_home_environment_overridden": False,
            "rules_and_auth_contents_recorded": False,
            "note": "Native sandbox selected; runner inherits configured auth/rules environment without recording it.",
        }

    prompt = render_prompt(case, schema, workspace, repo)
    command = _native_argv(
        args.harness, binary, args.model, workspace, prompt,
        args.variant, args.reasoning_effort,
    )
    if any(not isinstance(token, str) or "\0" in token for token in command):
        print("FAIL: native argv contains a malformed argument")
        return 2

    started_epoch = time.time()
    try:
        process = run_bounded(command, cwd=workspace, env=child_env, timeout=args.timeout_seconds)
    except OSError as exc:
        diagnostic = runtime_unavailable_diagnostic(
            f"The selected native runtime could not start ({type(exc).__name__}).", repo=repo,
        )
        print(json.dumps({"status": "unavailable", "diagnostics": [diagnostic]}, indent=2))
        return 2
    ended_epoch = time.time()
    raw_stdout = process.stdout
    raw_stderr = process.stderr
    stdout_path = run_dir / "raw-stdout.jsonl"
    stderr_path = run_dir / "raw-stderr.bin"
    try:
        raw_file_exclusive(stdout_path, raw_stdout)
        raw_file_exclusive(stderr_path, raw_stderr)
    except (OSError, PathSafetyError) as exc:
        print(f"FAIL: could not preserve private raw native streams: {type(exc).__name__}")
        return 2

    normalized = (
        normalize_opencode(raw_stdout, workspace, repo)
        if args.harness == "opencode"
        else normalize_codex(raw_stdout, workspace, repo)
    )
    if args.harness == "opencode":
        after = special_project_dirs(workspace)
        profile["project_runtime_trees_absent_after_native_run"] = not any(after.values())
        profile["project_runtime_trees_after_native_run"] = after
    final_files = file_map(workspace)
    completion_rel = fixture_deliverable(
        repo, "contribute", "first-finished-contribution-primary", "completion.json",
    )
    artifact_refs = {
        relative: digest
        for relative, digest in final_files.items()
        if relative in {completion_rel, prepare["fixture_path"]}
    }
    trial = {
        "record_version": 2,
        "series_id": manifest["series_id"],
        "series_manifest_sha256": manifest_hash,
        "series_manifest_path": Path(args.series_manifest).as_posix(),
        "case_id": args.case,
        "case_role": case["role"],
        "harness": args.harness,
        "harness_version": version,
        "execution_mode": "fresh-native-run",
        "fresh_native_invocation": True,
        "requested_model_selector": requested_argument(args.model, "model"),
        "requested_variant": requested_argument(args.variant, "variant"),
        "requested_reasoning_effort": requested_argument(args.reasoning_effort, "reasoning"),
        "invocation_argument_policy": "requested selectors are caller argv observations, not proof of the backend-served model; raw argv and provider environment are not archived",
        "workspace_identity": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
        "source_tree_sha256": prepare["source_tree_sha256"],
        "snapshot_sha256": prepare["snapshot_sha256"],
        "expected_snapshot_sha256": args.expected_snapshot_sha256,
        "snapshot_file_count": prepare["snapshot_file_count"],
        "initial_file_sha256": prepare["initial_files"],
        "fixture_path": prepare["fixture_path"],
        "clean_fixture_source_sha256": prepare["clean_fixture_source_sha256"],
        "seeded_fixture_sha256": prepare["seeded_fixture_sha256"],
        "workspace_git_root_verified": prepare["workspace_git_root_verified"],
        "root_route_files_visible": prepare["root_route_files_visible"],
        "ancestor_instructions_outside_workspace": prepare["ancestor_instructions_outside_workspace"],
        "public_suite_is_instruction_isolated": prepare["public_suite_is_instruction_isolated"],
        "clean_source_preflight": clean_source,
        "seed_validator_preflight": baseline_preflight,
        "child_profile": profile,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "started_at": _utc(started_epoch),
        "started_epoch": started_epoch,
        "ended_at": _utc(ended_epoch),
        "harness_exit_code": process.returncode,
        "harness_timed_out": process.timed_out,
        "process_group_remaining": process.process_group_remaining,
        "process_group_cleanup_attempted": process.cleanup_attempted,
        "native_session_id_sha256": normalized.get("session_id_sha256"),
        "session_id_sha256": normalized.get("session_id_sha256"),
        "native_trace_complete": normalized.get("trace_complete"),
        "native_events_observed": normalized.get("events_observed"),
        "native_invalid_lines": normalized.get("invalid_lines"),
        "native_unclassified_items": normalized.get("unclassified_item_count", 0),
        "native_unclassified_tools": normalized.get("unclassified_tool_count", 0),
        "clarification_observations": normalized.get("clarification_observations"),
        "observed_actions": normalized.get("actions", []),
        "raw_events_path": stdout_path.relative_to(repo).as_posix(),
        "raw_events_sha256": hashlib.sha256(raw_stdout).hexdigest(),
        "raw_events_bytes": len(raw_stdout),
        "raw_stderr_path": stderr_path.relative_to(repo).as_posix(),
        "raw_stderr_sha256": hashlib.sha256(raw_stderr).hexdigest(),
        "raw_stderr_bytes": len(raw_stderr),
        "artifact_refs_at_process_exit": artifact_refs,
        "notes": "Sanitized native-event summary only; raw streams remain in git-ignored private scratch.",
    }

    try:
        verdict = grade_trial(repo, workspace, trial)
    except (GradeError, ManifestError, OSError, ValueError, PathSafetyError) as exc:
        verdict = {
            "series_id": manifest["series_id"],
            "series_manifest_sha256": manifest_hash,
            "case_id": args.case,
            "harness": args.harness,
            "evidence_scope": "fresh-native-trial",
            "success": False,
            "status": "fail",
            "failure_reasons": [f"independent grading failed closed: {type(exc).__name__}"],
            "diagnostics": build_failure_diagnostics(
                [f"Independent documentation grading failed closed: {type(exc).__name__}"],
                normalized.get("actions", []), args.harness,
                scope="docs", artifact_paths=(COMPLETION_REL,), repo=repo, workspace=workspace,
            ),
            "synthetic_regression": False,
        }
    verdict["run_id"] = run_id
    trial_path = run_dir / "trial.json"
    verdict_path = run_dir / "verdict.json"
    try:
        exclusive_json(trial_path, trial)
        exclusive_json(verdict_path, verdict)
    except (OSError, PathSafetyError) as exc:
        print(f"FAIL: evidence write refused: {type(exc).__name__}")
        return 2
    print(json.dumps({
        "run_id": run_id,
        "harness": args.harness,
        "case_id": args.case,
        "status": verdict.get("status", "fail"),
        "success": verdict.get("success", False),
        "trial_record": trial_path.relative_to(repo).as_posix(),
        "verdict": verdict_path.relative_to(repo).as_posix(),
        "workspace_identity": trial["workspace_identity"],
        "snapshot_sha256": trial["snapshot_sha256"],
        "source_tree_sha256": trial["source_tree_sha256"],
        "failure_count": len(verdict.get("failure_reasons", [])),
        "artifact_diff": verdict.get("artifact_diff"),
        "validation": verdict.get("validation"),
        "native_trace": verdict.get("native_trace"),
        "diagnostics": verdict.get("diagnostics", []),
        "entry_route": verdict.get("entry_route"),
    }, indent=2))
    return 0 if verdict.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
