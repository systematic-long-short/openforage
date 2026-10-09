#!/usr/bin/env python3
"""Offline research evaluator, immutable series freezer, and explicit native runner."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS_ROOT = HERE.parent
REPO_DEFAULT = HERE.parents[2]
V2_DIR = EVALS_ROOT / "v2"
RUNS_REL = Path(".tmp/fresh-agent-research/runs")
WORK_PARENT = Path(tempfile.gettempdir()) / "openforage-public-research" / "workspaces"
MAX_TIMEOUT = 900.0
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
CASE_ID = "paper-backed-coverage-challenger"
sys.dont_write_bytecode = True

for search_path in (
    str(HERE), str(EVALS_ROOT), str(V2_DIR),
    str(REPO_DEFAULT / "examples/opencode-lifecycle"), str(REPO_DEFAULT / "scripts"),
):
    if search_path not in sys.path:
        sys.path.insert(0, search_path)

from onboarding.completion import (  # noqa: E402
    load_fixture_contract,
    render_finish_commands,
    selected_input_paths,
    validate_series_manifest_path,
)

RESEARCH_CONTRACT = load_fixture_contract(REPO_DEFAULT, "research", CASE_ID)


def _contract_path_by_name(paths: list[str], basename: str) -> str:
    matches = [path for path in paths if Path(path).name == basename]
    if len(matches) != 1:
        raise ValueError("canonical research contract does not declare exactly one requested path")
    return matches[0]


TASK_REL = _contract_path_by_name(RESEARCH_CONTRACT["available_input_paths"], "task.md")
RESULT_SCHEMA_REL = _contract_path_by_name(RESEARCH_CONTRACT["available_input_paths"], "research-result.schema.json")
REPORT_SCHEMA_REL = _contract_path_by_name(RESEARCH_CONTRACT["available_input_paths"], "comparison-report.schema.json")
CODE_REL = _contract_path_by_name(RESEARCH_CONTRACT["deliverables"], "challenger.py")
REPORT_REL = _contract_path_by_name(RESEARCH_CONTRACT["deliverables"], "comparison-report.json")
RESULT_REL = _contract_path_by_name(RESEARCH_CONTRACT["deliverables"], "research-result.json")

import v2.runner as v2_runner  # noqa: E402
from v2.diagnostics import (  # noqa: E402
    build_failure_diagnostics,
    research_comparison_diagnostic,
    runtime_unavailable_diagnostic,
)
from v2.manifest import source_tree_sha256  # noqa: E402
from v2.native_events import normalize_codex, normalize_opencode  # noqa: E402
from v2.opencode_profile import (  # noqa: E402
    ProfileError,
    config_text,
    special_project_dirs,
    verify_effective_profile,
)
from v2.paths import (  # noqa: E402
    PathSafetyError,
    exclusive_json,
    make_exclusive_directory,
    mkdir_chain_no_symlinks,
    raw_file_exclusive,
    safe_relative,
)
from v2.processes import run_bounded  # noqa: E402
from v2.schema import loads_strict  # noqa: E402
from v2.snapshot import (  # noqa: E402
    SnapshotError,
    copy_public_tree,
    digest_map,
    file_map,
    initialize_public_workspace,
)

from series import SeriesError, build_series, verify_series, write_series_once  # noqa: E402
from synthetic_evaluator import (  # noqa: E402
    EvaluationError,
    evaluate_suite,
    load_fixture,
)
from research_grader import (  # noqa: E402
    GradeError,
    grade_contribution,
)

EXPECTED_VERSIONS = v2_runner.EXPECTED_VERSIONS


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_workspace_path(root: Path, relative: str, *, must_exist: bool) -> Path:
    try:
        safe = safe_relative(relative)
    except PathSafetyError as exc:
        raise ValueError("requested workspace path is unsafe") from exc
    target = root
    for part in safe.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("requested workspace path contains a symlink")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("requested workspace path escapes its root")
    if must_exist and not target.is_file():
        raise ValueError("requested workspace file does not exist")
    if not must_exist and target.exists():
        raise ValueError("refusing to overwrite an existing workspace artifact")
    return target


def _write_json_once(path: Path, payload: dict) -> None:
    if path.exists() or path.is_symlink():
        raise PathSafetyError(f"refusing to overwrite existing artifact: {path.name}")
    exclusive_json(path, payload)


def _safe_series_path(root: Path, relative: str) -> str:
    try:
        safe = safe_relative(relative)
    except PathSafetyError as exc:
        raise SeriesError("series manifest path is unsafe") from exc
    target = root / safe
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise SeriesError("selected series manifest is missing or unsafe")
    return safe.as_posix()


def _read_text(root: Path, relative: str) -> str:
    target = _safe_workspace_path(root, relative, must_exist=True)
    return target.read_text(encoding="utf-8")


def _render_prompt(root: Path, workspace: Path, series_path: str, series_id: str, series_digest: str) -> str:
    task = _read_text(root, TASK_REL)
    result_schema = json.loads(_read_text(root, RESULT_SCHEMA_REL))
    report_schema = json.loads(_read_text(root, REPORT_SCHEMA_REL))
    series_path = validate_series_manifest_path("research", series_path)
    contract = load_fixture_contract(root, "research", CASE_ID)
    inputs = selected_input_paths(contract, "research", series_path)
    finish_commands = render_finish_commands(contract, "research", series_path)
    commands_by_id = {item["check_id"]: item["command"] for item in finish_commands}
    contract_block = "\n".join(
        [
            "Explicit canonical fixture contract:",
            "Available input paths:",
            *[f"  - {path}" for path in inputs],
            "Required deliverables:",
            *[f"  - {path}" for path in contract["deliverables"]],
            "Optional scratch (not a deliverable or artifact-hash input):",
            *[f"  - {path}" for path in contract["scratch_paths"]],
            "Fixed finish-check IDs: " + ", ".join(contract["fixed_finish_check_ids"]),
            "Required finish sequence:",
            *[f"  {index}. {step}" for index, step in enumerate(contract["finish_sequence"], 1)],
            "Permitted commands (from the same canonical contract):",
            *[f"  - {commands_by_id[check_id]}" for check_id in contract["fixed_finish_check_ids"]],
        ]
    )
    eval_command = commands_by_id["research-development-evaluation"]
    completion_command = commands_by_id["research-completion-check"]
    selector_command = completion_command.removesuffix(" --check-completion")
    protocol = f"""Common research-evaluation protocol (paper-backed-coverage-v1).
Workspace root: {workspace}
Case id: {CASE_ID}
Selected frozen series: {series_path}
Series id: {series_id}
Series manifest SHA-256: {series_digest}

- Read and write only within this prepared workspace. It is a public instruction-isolated fixture, not a perfect blind evaluation or an OS security boundary.
- Begin with complete native content reads of workspace `README.md` and `AGENTS.md` through observed EOF; they must be the first content sources consulted. If native output gives a continuation offset, follow that exact offset until EOF before running the bounded read-only `{selector_command}` selector or reading `docs/agents/start-here.md`. Then complete the required reads of `examples/fresh-agent-evals/research/README.md` and `research/task.md` before editing. The grader verifies native action order, arguments, completion, full-source coverage, and identity; partial output and injected protocol/task text never count as a complete read.
- `scripts/start_here.py` is allowed read-only with its generic task selector or the exact fixture/manifest projection above; `--check-completion` is allowed only for that explicit task-bound fixture. Generation and other helper flags are not part of the trial grammar. Bounded structured `read`/`grep` content access is distinct from `list`/`glob` filename discovery; discovery does not prove content was read.
- Do not inspect the evaluation grader or use the reserved held-out input while developing. The suite is public; this is an instruction boundary, not a claim of perfect blindness.
- Do not access the source checkout or paths outside this workspace, network, credentials, package installers, campaign/search/submission commands, nested agents/models, or staging/commit/push commands.
- Keep the public callback shape `def challenger(ctx)` and yield one individual JSON graph payload at a time. `ctx` is the documented test-only fixture context, not an OpenForage API object.
- Use native file/edit/patch tools for `challenger.py` and `research-result.json`; the development-evaluation command is the recorded writer for `comparison-report.json`.
- The task requires a locally authored executable callback, an independently generated development report, and a typed source/research/result artifact. A plan or a wrapper around the bundled campaign example is not the deliverable.
- Use this exact command once after writing the callback to create the development report:
  {eval_command}
- Then write `trial-output/research-result.json` with the implementation, report, excerpt, and series hashes. Human rationale belongs only in `human_review`; it is not a machine fact.
- Report incomplete comparisons with their actual counts. No comparison requires challenger superiority. Quality bits and eligible denominators are synthetic-fixture-only.
- The reserved held-out input is executed independently by the trusted grader after the native subject finishes; do not include it in the development report.
- Use a repair loop after all three artifacts: run `git diff --check`, then this exact completion check: `{completion_command}`. If either check fails or the completion checker reports a repair, fix the relevant artifact and rerun both in that order. Stop only after a successful completion checker, and make that successful checker the final check. It is filesystem-only and does not execute the evaluator or callback; native trace review establishes every attempted command and outcome.
- Do not alter frozen suite files or validators. Authentic repository validation sources remain visible and intact in the workspace.
"""
    return (
        protocol + "\n" + contract_block + "\n\nTask:\n" + task
        + "\n\nTyped research-result schema:\n```json\n"
        + json.dumps(result_schema, indent=2, sort_keys=True)
        + "\n```\n\nTyped development-report schema:\n```json\n"
        + json.dumps(report_schema, indent=2, sort_keys=True)
        + "\n```\n"
    )


def _prepare_research_workspace(repo: Path, workspace: Path, env: dict) -> dict:
    repo = repo.resolve()
    workspace = Path(os.path.abspath(workspace))
    if workspace == repo or workspace.is_relative_to(repo):
        raise PathSafetyError("native workspace must be outside the source checkout")
    make_exclusive_directory(workspace, workspace.parent)
    from v2.paths import ensure_clean_ancestors
    ensure_clean_ancestors(workspace)
    selected_files = copy_public_tree(repo, workspace)
    required = ("README.md", "AGENTS.md", "CLAUDE.md")
    if any(not (workspace / item).is_file() or (workspace / item).is_symlink() for item in required):
        raise SnapshotError("prepared public route files are missing or unsafe")
    (workspace / "trial-output").mkdir()
    git_setup = initialize_public_workspace(repo, workspace, selected_files, env)
    files = file_map(workspace)
    return {
        "initial_file_sha256": files,
        "snapshot_sha256": digest_map(files),
        "source_tree_sha256": source_tree_sha256(repo),
        "snapshot_file_count": len(files),
        "workspace_git_root_verified": git_setup["workspace_git_root_verified"],
        "workspace_public_selection_sha256": git_setup["selected_paths_sha256"],
        "root_route_files_visible": list(required),
    }


def _requested_argument(value: str | None, kind: str) -> dict:
    return v2_runner.requested_argument(value, kind)


def _native_unavailable(
    repo: Path,
    run_dir: Path,
    *,
    series_id: str,
    manifest_path: str,
    manifest_digest: str,
    harness: str,
    run_id: str,
    reason: str,
    observed_version: str | None = None,
) -> dict:
    diagnostics = [runtime_unavailable_diagnostic(reason, repo=repo)]
    record = {
        "record_schema_version": 1,
        "series_id": series_id,
        "series_manifest_path": manifest_path,
        "series_manifest_sha256": manifest_digest,
        "case_id": CASE_ID,
        "harness": harness,
        "execution_mode": "unavailable",
        "fresh_native_invocation": False,
        "run_id": run_id,
        "status": "unavailable",
        "reason": reason,
        "observed_version": observed_version,
        "native_invocation_started": False,
        "model_invocation": False,
        "diagnostics": diagnostics,
    }
    relative = run_dir.relative_to(repo).as_posix()
    _write_json_once(run_dir / "unavailable.json", record)
    return {
        "status": "unavailable",
        "harness": harness,
        "reason": reason,
        "run_id": run_id,
        "series_id": series_id,
        "record": f"{relative}/unavailable.json",
        "diagnostics": diagnostics,
    }


def _make_portable_patch(workspace: Path, repo: Path, final_files: dict) -> str:
    pieces = []
    for relative in sorted((CODE_REL, REPORT_REL, RESULT_REL)):
        if relative not in final_files:
            continue
        path = workspace / relative
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        pieces.extend(difflib.unified_diff(
            [], lines, fromfile="/dev/null", tofile=f"b/{relative}", lineterm="\n",
        ))
        if pieces and not pieces[-1].endswith("\n"):
            pieces.append("\n")
    patch = "".join(pieces)
    # Portable exports omit common secret shapes and machine-local paths.
    from bridge_core import redact_secret_shapes
    patch = redact_secret_shapes(patch)
    for original, replacement in (
        (str(workspace.resolve()), "<WORKSPACE>"),
        (str(repo.resolve()), "<REPO>"),
        (str(Path.home().resolve()), "<HOME>"),
    ):
        patch = patch.replace(original, replacement)
    return patch


def _run_native(args, repo: Path) -> int:
    if args.harness not in {"opencode", "codex"}:
        raise ValueError("--native-run requires an explicit --harness")
    if args.case != CASE_ID:
        raise ValueError(f"--case must be {CASE_ID}")
    if not args.model or not args.model.strip() or any(char in args.model for char in "\0\r\n"):
        raise ValueError("--native-run requires one explicit non-empty --model selector")
    if args.harness == "opencode" and (not args.variant or args.reasoning_effort is not None):
        raise ValueError("OpenCode requires explicit --variant and does not accept --reasoning-effort")
    if args.harness == "codex" and (not args.reasoning_effort or args.variant is not None):
        raise ValueError("Codex requires explicit --reasoning-effort and does not accept --variant")
    for selector in (args.variant, args.reasoning_effort):
        if selector is not None and (not selector.strip() or any(char in selector for char in "\0\r\n")):
            raise ValueError("harness-specific selectors must be non-empty single argv values")
    if args.run_id is None or RUN_ID_RE.fullmatch(args.run_id) is None:
        raise ValueError("--native-run requires a new short safe --run-id")
    if not 0 < args.timeout_seconds <= MAX_TIMEOUT:
        raise ValueError(f"--timeout-seconds must be > 0 and <= {MAX_TIMEOUT:g}")
    if args.expected_snapshot_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", args.expected_snapshot_sha256) is None:
        raise ValueError("--expected-snapshot-sha256 must be 64 lowercase hexadecimal characters")

    manifest_path = _safe_series_path(repo, args.series_manifest)
    manifest, manifest_digest = verify_series(repo, manifest_path)
    series_id = manifest["series_id"]
    run_parent = repo / RUNS_REL / series_id
    mkdir_chain_no_symlinks(run_parent)
    run_dir = make_exclusive_directory(run_parent / args.run_id, run_parent)

    binary = args.opencode_binary if args.harness == "opencode" else args.codex_binary
    if shutil.which(binary) is None:
        summary = _native_unavailable(
            repo, run_dir, series_id=series_id, manifest_path=manifest_path,
            manifest_digest=manifest_digest, harness=args.harness, run_id=args.run_id,
            reason="selected native runtime executable is unavailable",
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    env = v2_runner._child_environment()
    workspace = WORK_PARENT / args.run_id
    try:
        mkdir_chain_no_symlinks(WORK_PARENT)
        clean_source = v2_runner._clean_source_preflight(repo, env)
        if clean_source.get("status") != "pass":
            raise GradeError("clean source truthfulness/links preflight failed; no native subject launched")
        prepared = _prepare_research_workspace(repo, workspace, env)
        workspace_manifest, workspace_manifest_digest = verify_series(workspace, manifest_path)
        if workspace_manifest_digest != manifest_digest or workspace_manifest != manifest:
            raise GradeError("prepared workspace does not match the selected frozen research series")
        if args.expected_snapshot_sha256 is not None and prepared["snapshot_sha256"] != args.expected_snapshot_sha256:
            raise GradeError("prepared source snapshot differs from the caller-pinned comparison snapshot")
    except (OSError, ValueError, SeriesError, SnapshotError, PathSafetyError, GradeError) as exc:
        message = f"Research trial preflight failed: {type(exc).__name__}"
        diagnostics = build_failure_diagnostics(
            [message], [], args.harness, scope="research", repo=repo,
        )
        print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
        return 2

    version, version_probe = v2_runner._harness_version(binary, args.harness, workspace, env)
    if (
        version != EXPECTED_VERSIONS[args.harness]
        or version_probe.get("status") != "observed"
        or version_probe.get("timed_out") is not False
        or version_probe.get("process_group_remaining") is not False
    ):
        summary = _native_unavailable(
            repo, run_dir, series_id=series_id, manifest_path=manifest_path,
            manifest_digest=manifest_digest, harness=args.harness, run_id=args.run_id,
            reason="installed native runtime version is not the frozen adapter target",
            observed_version=version,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    child_env = dict(env)
    child_profile = {}
    if args.harness == "opencode":
        try:
            child_profile = verify_effective_profile(binary, workspace, child_env)
        except ProfileError as exc:
            message = f"OpenCode named-agent profile could not be verified: {type(exc).__name__}"
            diagnostics = build_failure_diagnostics(
                [message], [], args.harness, scope="research", repo=repo, workspace=workspace,
            )
            print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
            return 2
        child_env["OPENCODE_CONFIG_CONTENT"] = config_text()
    else:
        child_profile = {
            "profile_id": "codex-workspace-write",
            "sandbox": "workspace-write",
            "approval": "never",
            "no_daemon": True,
            "ignore_user_config_flag": True,
            "rules_and_auth_environment_overridden": False,
            "rules_and_auth_contents_recorded": False,
            "note": "Native workspace-write selection; configured authentication/rules are not archived; no sandbox bypass is used.",
        }

    prompt = _render_prompt(repo, workspace, manifest_path, series_id, manifest_digest)
    command = v2_runner._native_argv(
        args.harness, binary, args.model, workspace, prompt,
        args.variant, args.reasoning_effort,
    )
    started = time.time()
    try:
        process = run_bounded(command, cwd=workspace, env=child_env, timeout=args.timeout_seconds)
    except OSError as exc:
        summary = _native_unavailable(
            repo, run_dir, series_id=series_id, manifest_path=manifest_path,
            manifest_digest=manifest_digest, harness=args.harness, run_id=args.run_id,
            reason=f"selected native runtime could not start ({type(exc).__name__})",
            observed_version=version,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    ended = time.time()
    raw_stdout, raw_stderr = process.stdout, process.stderr
    events_path = run_dir / "raw-events.jsonl"
    stderr_path = run_dir / "raw-stderr.bin"
    try:
        raw_file_exclusive(events_path, raw_stdout)
        raw_file_exclusive(stderr_path, raw_stderr)
    except (OSError, PathSafetyError) as exc:
        print(f"FAIL: could not preserve private native streams: {type(exc).__name__}")
        return 2

    normalized = (
        normalize_opencode(raw_stdout, workspace, repo)
        if args.harness == "opencode"
        else normalize_codex(raw_stdout, workspace, repo)
    )
    if args.harness == "opencode":
        after = special_project_dirs(workspace)
        child_profile["project_runtime_trees_absent_after_native_run"] = not any(after.values())
        child_profile["project_runtime_trees_after_native_run"] = after

    final_files = file_map(workspace)
    artifacts = {
        relative: final_files[relative]
        for relative in (CODE_REL, REPORT_REL, RESULT_REL)
        if relative in final_files
    }
    workspace_identity = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()
    trial = {
        "record_schema_version": 1,
        "series_id": series_id,
        "series_manifest_path": manifest_path,
        "series_manifest_sha256": manifest_digest,
        "case_id": CASE_ID,
        "harness": args.harness,
        "execution_mode": "fresh-native-run",
        "fresh_native_invocation": True,
        "run_id": args.run_id,
        "workspace_identity_sha256": workspace_identity,
        "snapshot_sha256": prepared["snapshot_sha256"],
        "initial_file_sha256": prepared["initial_file_sha256"],
        "native_exit_code": process.returncode,
        "native_timed_out": process.timed_out,
        "process_group_remaining": process.process_group_remaining,
        "native_session_id_sha256": normalized.get("session_id_sha256"),
        "native_trace_complete": normalized.get("trace_complete") is True,
        "native_actions": normalized.get("actions", []),
        "raw_events_path": events_path.relative_to(repo).as_posix(),
        "raw_events_sha256": _sha256(raw_stdout),
        "raw_events_bytes": len(raw_stdout),
        "raw_stderr_path": stderr_path.relative_to(repo).as_posix(),
        "raw_stderr_sha256": _sha256(raw_stderr),
        "raw_stderr_bytes": len(raw_stderr),
        "artifact_sha256": artifacts,
        "expected_snapshot_sha256": args.expected_snapshot_sha256,
    }
    try:
        verdict = grade_contribution(repo, workspace, trial, manifest_path)
    except (GradeError, SeriesError, EvaluationError, OSError, ValueError, PathSafetyError) as exc:
        verdict = {
            "verdict_schema_version": 1,
            "series_id": series_id,
            "series_manifest_sha256": manifest_digest,
            "case_id": CASE_ID,
            "evidence_scope": "fresh-native-trial",
            "success": False,
            "status": "fail",
            "failure_reasons": [f"independent grading failed closed: {type(exc).__name__}"],
            "diagnostics": build_failure_diagnostics(
                [f"Independent research grading failed closed: {type(exc).__name__}"],
                normalized.get("actions", []), args.harness,
                scope="research", artifact_paths=(CODE_REL, REPORT_REL, RESULT_REL),
                repo=repo, workspace=workspace,
            ),
            "artifact_hashes": artifacts,
            "native_command_evidence": None,
            "independent_report": None,
            "limitations": ["No live or out-of-sample measurement is produced by this synthetic fixture."],
        }

    run_relative = run_dir.relative_to(repo).as_posix()
    try:
        _write_json_once(run_dir / "trial.json", trial)
        _write_json_once(run_dir / "verdict.json", verdict)
        patch = _make_portable_patch(workspace, repo, final_files)
        patch_path = run_dir / "contribution.patch"
        raw_file_exclusive(patch_path, patch.encode("utf-8"))
        _write_json_once(run_dir / "provenance.json", {
            "record_schema_version": 1,
            "run_id": args.run_id,
            "series_id": series_id,
            "series_manifest_sha256": manifest_digest,
            "snapshot_sha256": prepared["snapshot_sha256"],
            "source_tree_sha256": prepared["source_tree_sha256"],
            "harness": args.harness,
            "harness_version": version,
            "requested_model_selector": _requested_argument(args.model, "model"),
            "requested_variant": _requested_argument(args.variant, "variant"),
            "requested_reasoning_effort": _requested_argument(args.reasoning_effort, "reasoning"),
            "selector_note": "Caller argv requests only; backend-served model is not observed.",
            "native_started_at": _utc(started),
            "native_ended_at": _utc(ended),
            "public_suite_is_instruction_isolated": True,
            "workspace_identity_sha256": workspace_identity,
            "portable_patch_path": f"{run_relative}/contribution.patch",
            "portable_patch_sha256": _sha256(patch.encode("utf-8")),
        })
    except (OSError, PathSafetyError) as exc:
        print(f"FAIL: sanitized evidence write refused: {type(exc).__name__}")
        return 2

    summary = {
        "status": verdict.get("status", "fail"),
        "success": verdict.get("success", False),
        "harness": args.harness,
        "case_id": CASE_ID,
        "run_id": args.run_id,
        "series_id": series_id,
        "trial_record": f"{run_relative}/trial.json",
        "verdict": f"{run_relative}/verdict.json",
        "portable_patch": f"{run_relative}/contribution.patch",
        "provenance": f"{run_relative}/provenance.json",
        "snapshot_sha256": prepared["snapshot_sha256"],
        "failure_count": len(verdict.get("failure_reasons", [])),
        "failure_reasons": verdict.get("failure_reasons", []),
        "artifact_hashes": verdict.get("artifact_hashes"),
        "native_command_evidence": verdict.get("native_command_evidence"),
        "diagnostics": verdict.get("diagnostics", []),
        "entry_route": verdict.get("entry_route"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if verdict.get("success") is True else 1


def _freeze(args, repo: Path) -> int:
    if not args.series_id or not args.output:
        raise ValueError("--freeze requires both --series-id and --output")
    payload = build_series(repo, args.series_id)
    output = write_series_once(repo, args.output, payload)
    print(json.dumps({
        "created": output.relative_to(repo).as_posix(),
        "series_id": args.series_id,
        "frozen_files": len(payload["frozen_files"]),
        "manifest_sha256": _sha256(output.read_bytes()),
        "native_calls": 0,
    }, indent=2, sort_keys=True))
    return 0


def _check_freeze(args, repo: Path) -> int:
    if not args.series_manifest:
        raise ValueError("--check-freeze requires --series-manifest")
    manifest, digest = verify_series(repo, args.series_manifest)
    print(json.dumps({
        "series_id": manifest["series_id"],
        "manifest_path": args.series_manifest,
        "manifest_sha256": digest,
        "frozen_files": len(manifest["frozen_files"]),
        "drift": False,
        "native_calls": 0,
    }, indent=2, sort_keys=True))
    return 0


def _evaluate_development(args, repo: Path) -> int:
    if not args.series_manifest or not args.implementation:
        raise ValueError("--evaluate-development requires --series-manifest and --implementation")
    if args.report_out is not None and args.report_out != REPORT_REL:
        raise ValueError(f"--report-out must be exactly {REPORT_REL}")
    manifest_path = _safe_series_path(repo, args.series_manifest)
    manifest, manifest_digest = verify_series(repo, manifest_path)
    fixture, fixture_bytes = load_fixture(repo)
    provenance_path = _safe_workspace_path(
        repo, "examples/fresh-agent-evals/research/source-provenance.json", must_exist=True,
    )
    source_record = json.loads(provenance_path.read_text(encoding="utf-8"))
    excerpt_path = _safe_workspace_path(repo, source_record["source_excerpt_path"], must_exist=True)
    source_digest = _sha256(excerpt_path.read_bytes())
    report, _full = evaluate_suite(
        repo,
        repo,
        args.implementation,
        series_id=manifest["series_id"],
        series_manifest_sha256=manifest_digest,
        source_excerpt_sha256=source_digest,
        include_heldout=False,
    )
    # Recheck after callback execution: evaluation input and executable helpers
    # are immutable across the bounded worker boundary.
    _manifest_after, digest_after = verify_series(repo, manifest_path)
    fixture_after, fixture_bytes_after = load_fixture(repo)
    if digest_after != manifest_digest or fixture_after != fixture or fixture_bytes_after != fixture_bytes:
        raise GradeError("frozen source or fixture drifted during development evaluation")
    if args.report_out:
        output = _safe_workspace_path(repo, args.report_out, must_exist=False)
        _write_json_once(output, report)
        print(json.dumps({
            "status": report["comparison_status"],
            "report_path": args.report_out,
            "report_sha256": _sha256(output.read_bytes()),
            "implementation_sha256": report["implementation_sha256"],
            "series_manifest_sha256": manifest_digest,
            "completed_development_pairs": report["paired_run_count"],
            "native_calls": 0,
            "diagnostics": [item for item in [research_comparison_diagnostic(report, repo=repo)] if item is not None],
        }, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _grade(args, repo: Path) -> int:
    if not args.trial or not args.series_manifest or not args.output:
        raise ValueError("--grade requires --trial, --series-manifest, and --output")
    trial_path = _safe_workspace_path(repo, args.trial, must_exist=True)
    if trial_path.relative_to(repo).parts[: len(RUNS_REL.parts)] != RUNS_REL.parts:
        raise ValueError("--trial must be under ignored fresh-agent research run scratch")
    try:
        trial = loads_strict(trial_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("trial record is unreadable") from exc
    if not isinstance(trial, dict):
        raise ValueError("trial record must be a JSON object")
    if args.workspace:
        workspace = Path(args.workspace)
    else:
        run_id = trial.get("run_id")
        if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("trial record has no safe run id for workspace resolution")
        workspace = WORK_PARENT / run_id
    verdict = grade_contribution(repo, workspace, trial, args.series_manifest)
    output = _safe_workspace_path(repo, args.output, must_exist=False)
    if output.relative_to(repo).parts[:1] != (".tmp",):
        raise ValueError("--grade output must remain under ignored .tmp scratch")
    _write_json_once(output, verdict)
    print(json.dumps({
        "status": verdict["status"],
        "success": verdict["success"],
        "verdict_path": args.output,
        "failure_count": len(verdict["failure_reasons"]),
        "diagnostics": verdict.get("diagnostics", []),
        "entry_route": verdict.get("entry_route"),
    }, indent=2, sort_keys=True))
    return 0 if verdict["success"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--selfcheck", action="store_true", help="run deterministic offline checks; never launch a harness")
    action.add_argument("--freeze", action="store_true", help="create a new immutable named-series manifest")
    action.add_argument("--check-freeze", action="store_true", help="verify an explicitly selected named-series manifest")
    action.add_argument("--evaluate-development", action="store_true", help="execute the callback on development-only synthetic inputs")
    action.add_argument("--native-run", action="store_true", help="explicitly launch one caller-selected OpenCode or Codex subject")
    action.add_argument("--grade", action="store_true", help="independently regrade one retained native trial")
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--series-id")
    parser.add_argument("--series-manifest")
    parser.add_argument("--output", help="exclusive series or verdict output path")
    parser.add_argument("--implementation", help="workspace-relative callback source")
    parser.add_argument("--report-out", help="exclusive workspace-relative development report output")
    parser.add_argument("--trial", help="workspace-relative retained trial.json path")
    parser.add_argument("--workspace", help="explicit prepared native workspace path; never stored in portable records")
    parser.add_argument("--case", choices=(CASE_ID,))
    parser.add_argument("--harness", choices=("opencode", "codex"))
    parser.add_argument("--model", help="required caller-selected model; no default")
    parser.add_argument("--variant", help="required caller-selected OpenCode variant")
    parser.add_argument("--reasoning-effort", help="required caller-selected Codex reasoning effort")
    parser.add_argument("--run-id", help="required unique native evidence directory id")
    parser.add_argument("--expected-snapshot-sha256", help="pin this subject to an earlier prepared input snapshot")
    parser.add_argument("--opencode-binary", default="opencode")
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)

    if args.selfcheck:
        from research_selfcheck import run_selfcheck
        return run_selfcheck(Path(args.repo).resolve())

    repo = Path(args.repo).resolve()
    try:
        if args.freeze:
            return _freeze(args, repo)
        if args.check_freeze:
            return _check_freeze(args, repo)
        if args.evaluate_development:
            return _evaluate_development(args, repo)
        if args.native_run:
            if not args.series_manifest:
                raise ValueError("--native-run requires --series-manifest")
            return _run_native(args, repo)
        if args.grade:
            return _grade(args, repo)
    except (OSError, ValueError, SeriesError, EvaluationError, GradeError, PathSafetyError) as exc:
        message = f"Research evaluation failed: {type(exc).__name__}"
        diagnostics = build_failure_diagnostics(
            [message], [], args.harness or "unknown", scope="research", repo=repo,
        )
        print(json.dumps({"status": "fail", "summary": message, "diagnostics": diagnostics}, indent=2))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
