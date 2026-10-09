"""Offline synthetic regressions for the paper-backed research evaluation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS_ROOT = HERE.parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(EVALS_ROOT / "v2") not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT / "v2"))

from callback_worker import _local_callback_source  # noqa: E402
from contract import FixtureContext  # noqa: E402
from research_grader import (  # noqa: E402
    CASE_ID,
    CODE_REL,
    REPORT_REL,
    RESULT_REL,
    _research_action_problems,
    _expected_machine_claims,
    _expected_eval_command,
    _final_completion_check_evidence,
    _native_command_evidence,
    grade_contribution,
)
from research_access import BLOCKED_SUBJECT_PATHS  # noqa: E402
from onboarding.completion import (  # noqa: E402
    SCRATCH_REL,
    load_fixture_contract,
    render_finish_commands,
    selfcheck_problems as completion_selfcheck_problems,
    series_directory,
)
from series import SeriesError, build_series, frozen_paths, verify_series, write_series_once  # noqa: E402
from synthetic_evaluator import (  # noqa: E402
    _RunAccumulator,
    canonical_json_equal,
    load_fixture,
)
from v2.paths import mkdir_chain_no_symlinks  # noqa: E402
from v2.diagnostics import compose_research_diagnostics  # noqa: E402
from v2.evidence import (  # noqa: E402
    command_sha256_matches,
    content_read_observations,
    entry_route_evidence,
    explicit_exit_code,
    normalize_access_observations,
    observed_route_sources,
)
from v2.grader import _path_write_sequences  # noqa: E402
from v2.native_arguments import ARGUMENT_SCHEMA_VERSION, NATIVE_ARGUMENT_TOOLS, normalize_native_arguments  # noqa: E402
from v2.native_events import _native_directory_frame, normalize_codex, normalize_opencode  # noqa: E402
from v2.commands import RESERVED_NAMES, command_semantic_key  # noqa: E402
from v2.shell_parser import MAX_COMMAND_BYTES, parse_shell_command  # noqa: E402
from v2.snapshot import (  # noqa: E402
    copy_public_tree,
    digest_map,
    file_map,
    initialize_public_workspace,
)

_RESEARCH_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "research_native_runner", HERE / "runner.py",
)
if _RESEARCH_RUNNER_SPEC is None or _RESEARCH_RUNNER_SPEC.loader is None:
    raise RuntimeError("research runner prompt module is unavailable")
_RESEARCH_RUNNER = importlib.util.module_from_spec(_RESEARCH_RUNNER_SPEC)
_RESEARCH_RUNNER_SPEC.loader.exec_module(_RESEARCH_RUNNER)
render_research_prompt = _RESEARCH_RUNNER._render_prompt

REGRESSION_ONLY_CALLBACK = '''\
def challenger(ctx):
    yield next(ctx.stream_for(ctx.eligible_pairs[0]))
'''
MANIFEST_REL = "examples/fresh-agent-evals/research/series/selfcheck-series-c.json"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _protected_evaluator_hashes(root: Path) -> dict[str, str]:
    closure = set(frozen_paths(root))
    protected = BLOCKED_SUBJECT_PATHS | {
        relative for relative in closure if Path(relative).name in RESERVED_NAMES
    }
    return {
        relative: _digest((root / relative).read_bytes())
        for relative in sorted(protected)
    }


def _access_action(tool: str, command: str) -> dict:
    raw = command.encode("utf-8")
    action = {
        "tool": tool,
        "command": command,
        "command_sha256": _digest(raw),
        "command_bytes": len(raw),
        "command_truncated": False,
    }
    if tool in NATIVE_ARGUMENT_TOOLS:
        path = command[len("<WORKSPACE>/"):] if command.startswith("<WORKSPACE>/") else command
        arguments_input = {"path": path}
        if tool == "glob":
            arguments_input["pattern"] = "**/*"
        elif tool == "grep":
            arguments_input["pattern"] = "selfcheck"
        argument_workspace = Path(tempfile.gettempdir()) / "research-access-selfcheck-workspace"
        arguments, problem = normalize_native_arguments(tool, arguments_input, argument_workspace)
        identity = _digest(f"selfcheck:{tool}:{command}".encode("utf-8"))
        action.update({
            "native_tool_call_id_sha256": identity,
            "event_sequence": 1,
            "completion_sequence": 1,
            "correlation_conflict": False,
            "outcome_schema_version": 1,
            "outcome": {"native_tool_status": "completed", "shell_exit_code_status": "not_applicable"},
            "native_arguments_schema_version": ARGUMENT_SCHEMA_VERSION,
            "native_arguments_status": "observed" if arguments is not None and problem is None else "unknown",
            "native_arguments_problem": problem,
            "native_arguments": arguments,
            "native_arguments_sha256": _digest(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode())
            if arguments is not None else None,
            "native_arguments_bytes": len(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode())
            if arguments is not None else None,
            "native_arguments_event_sequence": 1,
            "native_arguments_completion_sequence": 1,
            "native_arguments_identity_sha256": identity,
            "native_arguments_completion_status": "completed",
        })
        if arguments is not None:
            action["command"] = arguments["path"]
            encoded = action["command"].encode("utf-8")
            action["command_sha256"] = _digest(encoded)
            action["command_bytes"] = len(encoded)
            if tool == "read":
                framed_output = "1: synthetic read result"
                framed_bytes = framed_output.encode("utf-8")
                action["outcome"].update({
                    "output_hash_status": "observed",
                    "output_bytes": len(framed_bytes),
                    "output_sha256": _digest(framed_bytes),
                    "native_output_type": "text",
                })
                action.update({
                    "native_output_result_type": "file_content",
                    "native_output_names_status": "unknown",
                    "native_output_content_status": "observed",
                    "native_output_content_bytes": len("synthetic read result".encode("utf-8")),
                    "native_output_file_first_line": 1,
                    "native_output_file_last_line": 1,
                    "native_output_file_line_count": 1,
                    "native_output_file_complete": False,
                    "native_output_event_sequence": 1,
                    "native_output_identity_sha256": identity,
                })
    return action


def _complete_access_trace(actions: list, harness: str) -> dict:
    return {
        "actions": actions,
        "events_observed": max(1, len(actions)),
        "invalid_lines": 0,
        "trace_complete": True,
        "unclassified_tool_count" if harness == "opencode" else "unclassified_item_count": 0,
    }


def _shell_event(call_id: str, command: str, stamp: float, *, exit_code: int = 0, status: str = "completed") -> dict:
    return {
        "type": "tool_use",
        "timestamp": stamp,
        "part": {
            "type": "tool",
            "tool": "bash",
            "callID": call_id,
            "state": {
                "status": status,
                "input": {"command": command},
                "output": "synthetic fixed-check output",
                "metadata": {"exit": exit_code},
                "timestamp": stamp,
            },
        },
    }


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _codex_event_jsonl(events: list[dict], workspace: Path, repo: Path) -> dict:
    raw = b"".join(
        (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for event in events
    )
    return normalize_codex(raw, workspace, repo)


def _copy_closure(source: Path, target: Path) -> None:
    for relative in frozen_paths(source):
        origin = source / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)


def _check_research_workspace_public_selection(root: Path, failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="of-research-tracked-ignore-") as temporary:
        source = Path(temporary) / "public-source"
        workspace = Path(temporary) / "research-workspace"
        selected = copy_public_tree(root, source)
        ignored_rel = "docs/published-ignored.md"
        linked_rel = "docs/portable-example.md"
        ignored_path = source / ignored_rel
        linked_path = source / linked_rel
        if ignored_path.exists() or linked_path.exists():
            failures.append("research workspace selection regression paths already exist")
            return
        ignore_file = source / ".gitignore"
        ignore_text = ignore_file.read_text(encoding="utf-8")
        if ignored_rel not in set(ignore_text.splitlines()):
            ignore_file.write_text(ignore_text.rstrip("\n") + f"\n{ignored_rel}\n", encoding="utf-8")
        ignored_path.write_text("# Published ignored page\n", encoding="utf-8")
        linked_path.write_text(
            "# Portable example\n\n"
            "[Published page](published-ignored.md#published-ignored-page).\n",
            encoding="utf-8",
        )
        selected[".gitignore"] = _digest(ignore_file.read_bytes())
        selected[ignored_rel] = _digest(ignored_path.read_bytes())
        selected[linked_rel] = _digest(linked_path.read_bytes())
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        })
        try:
            initialize_public_workspace(root, source, selected, env)
            source_index = source / ".git/index"
            index_before = _digest(source_index.read_bytes())
            source_links = subprocess.run(
                [
                    sys.executable,
                    ".github/scripts/validate_agent_docs.py",
                    "links",
                    "--root",
                    ".",
                ],
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if source_links.returncode != 0:
                failures.append("source links rejected the tracked ignored-name research fixture")
                return

            prepared = _RESEARCH_RUNNER._prepare_research_workspace(source, workspace, env)
            if (
                ignored_rel not in prepared["initial_file_sha256"]
                or linked_rel not in prepared["initial_file_sha256"]
                or not prepared.get("workspace_git_root_verified")
                or not (workspace / ignored_rel).is_file()
                or not (workspace / linked_rel).is_file()
            ):
                failures.append("research workspace initialization dropped the selected tracked public file")
            workspace_links = subprocess.run(
                [
                    sys.executable,
                    ".github/scripts/validate_agent_docs.py",
                    "links",
                    "--root",
                    ".",
                ],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if workspace_links.returncode != 0:
                failures.append("research isolated workspace links rejected the selected tracked public file")
            if _digest(source_index.read_bytes()) != index_before:
                failures.append("research workspace setup changed the source fixture Git index")
        except Exception as exc:  # noqa: BLE001 - no path or target content in diagnostics
            failures.append(f"research tracked-ignore workspace selfcheck failed: {type(exc).__name__}")


def _make_positive_fixture(root: Path, scratch: Path) -> tuple[Path, Path, dict, dict]:
    rubric = scratch / "rubric"
    rubric.mkdir()
    _copy_closure(root, rubric)
    manifest = build_series(rubric, "selfcheck-series-c")
    series_file = write_series_once(rubric, MANIFEST_REL, manifest)
    _loaded, manifest_digest = verify_series(rubric, MANIFEST_REL)

    workspace = scratch / "workspace-positive"
    shutil.copytree(rubric, workspace)
    (workspace / "trial-output").mkdir()
    initial = file_map(workspace)
    snapshot_digest = digest_map(initial)
    code_path = workspace / CODE_REL
    code_path.write_text(REGRESSION_ONLY_CALLBACK, encoding="utf-8")

    provenance_path = workspace / "examples/fresh-agent-evals/research/source-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    excerpt_digest = _digest((workspace / provenance["source_excerpt_path"]).read_bytes())
    runner_path = workspace / "examples/fresh-agent-evals/research/runner.py"
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    cli_result = subprocess.run(
        [
            sys.executable, str(runner_path), "--evaluate-development",
            "--implementation", CODE_REL,
            "--report-out", REPORT_REL,
            "--series-manifest", MANIFEST_REL,
            "--repo", str(workspace),
        ],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if cli_result.returncode != 0:
        raise RuntimeError("development evaluator CLI selfcheck failed")
    report_path = workspace / REPORT_REL
    dev_report = json.loads(report_path.read_text(encoding="utf-8"))
    report_digest = _digest(report_path.read_bytes())
    implementation_digest = _digest(code_path.read_bytes())
    result = {
        "schema_version": 1,
        "task_id": "paper-backed-coverage-challenger-v1",
        "source": {
            "source_excerpt_path": provenance["source_excerpt_path"],
            "source_excerpt_sha256": excerpt_digest,
            "title": provenance["title"],
            "authors": provenance["authors"],
            "year": provenance["year"],
            "identifier": provenance["identifier"],
            "url": provenance["url"],
            "paraphrase": "MAP-Elites maintains strong candidates across user-chosen descriptor cells, making the distribution visible rather than showing only a single best candidate.",
        },
        "research": {
            "mechanism_explanation": "The callback schedules fixture streams by feature and rotation pair; the evaluator keeps the supplied descriptor metadata separate from graph payload claims.",
            "hypothesis": "Under equal completed synthetic evaluator calls, the coverage-oriented schedule may reach a different number of eligible fixture cells than the synthetic uniform selector.",
            "falsifier": "The paired synthetic runs show no coverage difference or the callback exhausts before the target; neither outcome establishes live behavior.",
        },
        "machine_claims": _expected_machine_claims(
            series_path=MANIFEST_REL,
            series_id=manifest["series_id"],
            series_digest=manifest_digest,
            implementation_digest=implementation_digest,
            report_digest=report_digest,
            source_excerpt_digest=excerpt_digest,
        ),
        "human_review": {
            "summary": "Regression-only callback completed its development fixture execution.",
            "rationale": "This small selfcheck callback exercises the contract and grader; it is not a task starter or a research result.",
        },
    }
    result_path = workspace / RESULT_REL
    result_path.write_bytes(_json_bytes(result))

    trial = {
        "series_id": manifest["series_id"],
        "series_manifest_path": MANIFEST_REL,
        "series_manifest_sha256": manifest_digest,
        "case_id": CASE_ID,
        "snapshot_sha256": snapshot_digest,
        "initial_file_sha256": initial,
        "artifact_sha256": {
            CODE_REL: implementation_digest,
            REPORT_REL: report_digest,
            RESULT_REL: _digest(result_path.read_bytes()),
        },
    }
    return rubric, workspace, trial, dev_report


def _trial_with_current_artifacts(workspace: Path, trial: dict) -> dict:
    updated = dict(trial)
    updated["artifact_sha256"] = {
        relative: _digest((workspace / relative).read_bytes())
        for relative in (CODE_REL, REPORT_REL, RESULT_REL)
        if (workspace / relative).is_file() and not (workspace / relative).is_symlink()
    }
    return updated


def _check_accounting(failures: list[str], root: Path) -> None:
    fixture, _raw = load_fixture(root)
    fixture_input = fixture["inputs"][0]
    accumulator = _RunAccumulator(fixture_input, fixture["target_completed_evaluations_per_arm"])
    streams = fixture_input["candidate_streams"]
    pair_map = {item["pair_id"]: item for item in fixture_input["pairs"]}
    valid = next(
        record["payload"] for pair_id, records in streams.items() for record in records
        if pair_map[pair_id]["loaded"] is True and pair_map[pair_id]["eligible"] is True
        and record["depth"] is not None and record["depth"] <= 6
    )
    valid_transport = {"transport_status": "ok", "payload": valid}
    accumulator.add_transport(valid_transport)
    accumulator.add_transport(valid_transport)
    depth_payload = next(
        record["payload"] for records in streams.values() for record in records
        if record["depth"] is not None and record["depth"] > 6
    )
    accumulator.add_transport({"transport_status": "ok", "payload": depth_payload})
    excluded_pair_id = next(item["pair_id"] for item in fixture_input["pairs"] if item["loaded"] and not item["eligible"])
    excluded_payload = streams[excluded_pair_id][0]["payload"]
    accumulator.add_transport({"transport_status": "ok", "payload": excluded_payload})
    unloaded_pair_id = next(item["pair_id"] for item in fixture_input["pairs"] if not item["loaded"])
    unloaded_payload = streams[unloaded_pair_id][0]["payload"]
    accumulator.add_transport({"transport_status": "ok", "payload": unloaded_payload})
    unknown_payload = next(
        record["payload"] for records in streams.values() for record in records
        if record["synthetic_quality_pass"] is None
    )
    accumulator.add_transport({"transport_status": "ok", "payload": unknown_payload})
    accumulator.add_transport({"transport_status": "not_json_compatible"})
    summary = accumulator.snapshot()
    if accumulator.ledger.evaluations != 1 or summary["unique_valid_cell_count"] != 1:
        failures.append("only one trusted valid unique fixture payload counted as an evaluator completion")
    if summary["rejections"] != {
        "duplicate_identity": 1,
        "depth_over_six": 1,
        "excluded_pair": 1,
        "unloaded_pair": 1,
        "unknown_identity": 1,
        "invalid_payload": 1,
    }:
        failures.append("duplicate, depth, excluded, unloaded, unknown, or invalid attempts were not separated")


def _check_native_linkage(failures: list[str], workspace: Path, trusted_root: Path) -> None:
    series_path = MANIFEST_REL
    command = _expected_eval_command(series_path)
    excerpt_path = "examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/primary-source-excerpt.md"

    def file_write(relative: str, call_id: str, sequence: int) -> dict:
        raw = relative.encode("utf-8")
        return {
            "tool": "write",
            "command": relative,
            "command_sha256": _digest(raw),
            "command_bytes": len(raw),
            "command_truncated": False,
            "native_tool_call_id_sha256": _digest(call_id.encode("utf-8")),
            "event_sequence": sequence,
            "completion_sequence": sequence,
            "outcome_schema_version": 1,
            "outcome": {"native_tool_status": "completed"},
            "correlation_conflict": False,
        }

    def shell_action(
        call_id: str,
        sequence: int,
        shell_command: str,
        *,
        exit_code: int | None = 0,
        status: str = "completed",
        truncated: bool = False,
    ) -> dict:
        action = _access_action("bash", shell_command)
        action.update({
            "native_tool_call_id_sha256": _digest(call_id.encode("utf-8")),
            "event_sequence": sequence,
            "completion_sequence": sequence,
            "outcome_schema_version": 1,
            "outcome": {
                "native_tool_status": status,
                "shell_exit_code_status": "observed" if type(exit_code) is int else "missing",
                "shell_exit_code": exit_code,
            },
            "correlation_conflict": False,
        })
        if truncated:
            action["command_truncated"] = True
        return action

    def file_read(relative: str, call_id: str, sequence: int) -> dict:
        action = _access_action("read", f"<WORKSPACE>/{relative}")
        identity = _digest(call_id.encode("utf-8"))
        framed_output = "1: synthetic read result"
        framed_bytes = framed_output.encode("utf-8")
        action.update({
            "native_tool_call_id_sha256": identity,
            "event_sequence": sequence,
            "completion_sequence": sequence,
            "native_arguments_event_sequence": sequence,
            "native_arguments_completion_sequence": sequence,
            "native_arguments_identity_sha256": identity,
            "native_output_event_sequence": sequence,
            "native_output_identity_sha256": identity,
            "outcome_schema_version": 1,
            "outcome": {
                "native_tool_status": "completed",
                "output_bytes": len(framed_bytes),
                "output_hash_status": "observed",
                "output_sha256": _digest(framed_bytes),
                "native_output_type": "text",
                "shell_exit_code_status": "not_applicable",
            },
            "correlation_conflict": False,
        })
        return action

    actions = [
        file_write(CODE_REL, "code-write", 42),
        shell_action("eval-call", 52, command),
        shell_action(
            "hash-before-result",
            55,
            f"sha256sum {excerpt_path} {series_path} {CODE_REL} {REPORT_REL}",
        ),
        file_read(REPORT_REL, "report-read", 56),
        shell_action(
            "shasum-before-result",
            57,
            f"shasum -a 256 {excerpt_path} {series_path} {CODE_REL} {REPORT_REL}",
        ),
        file_write(RESULT_REL, "result-write", 62),
        shell_action(
            "hash-after-result",
            66,
            f"sha256sum {excerpt_path} {series_path} {CODE_REL} {REPORT_REL}",
        ),
        file_read(RESULT_REL, "result-read", 67),
        shell_action(
            "shasum-after-result",
            68,
            f"shasum -a 256 {excerpt_path} {series_path} {CODE_REL} {REPORT_REL}",
        ),
    ]
    evidence, problems = _native_command_evidence(actions, "opencode", workspace, series_path)
    report_writes = evidence.get("write_sequences", {}).get(REPORT_REL)
    if problems or evidence.get("explicit_native_exit_code") != 0 or report_writes != [52]:
        failures.append(f"parsed command/lifecycle helper did not correlate the exact successful native command: {problems}")
    valid_access_problems = _research_action_problems(
        actions, "opencode", workspace, trusted_root,
        trace=_complete_access_trace(actions, "opencode"),
        series_manifest_path=series_path,
    )
    if valid_access_problems:
        failures.append(f"the bounded native-action audit rejected allowed pre/post-result hash reads: {valid_access_problems}")

    post_evaluation_edit = file_write(REPORT_REL, "report-post-evaluation-edit", 64)
    edited_actions = [*actions[:6], post_evaluation_edit, *actions[6:]]
    edited_evidence, edited_problems = _native_command_evidence(
        edited_actions, "opencode", workspace, series_path,
    )
    edited_access_problems = _research_action_problems(
        edited_actions, "opencode", workspace, trusted_root,
        trace=_complete_access_trace(edited_actions, "opencode"),
        series_manifest_path=series_path,
    )
    if (
        edited_evidence.get("write_sequences", {}).get(REPORT_REL) != [52, 64]
        or not any("comparison report was written again" in item for item in edited_problems)
        or not any("native file edit is outside" in item for item in edited_access_problems)
    ):
        failures.append("a completed post-evaluation report file edit was not rejected as a report write")

    missing_evaluator = [action for action in actions if action is not actions[1]]
    missing_evidence, missing_problems = _native_command_evidence(
        missing_evaluator, "opencode", workspace, series_path,
    )
    if not missing_problems or missing_evidence:
        failures.append("absent declared evaluator evidence was accepted")

    truncated_evaluator = [dict(action) for action in actions]
    truncated_evaluator[1]["command_truncated"] = True
    truncated_evidence, truncated_problems = _native_command_evidence(
        truncated_evaluator, "opencode", workspace, series_path,
    )
    truncated_access_problems = _research_action_problems(
        truncated_evaluator, "opencode", workspace, trusted_root,
        trace=_complete_access_trace(truncated_evaluator, "opencode"),
        series_manifest_path=series_path,
    )
    if (
        not truncated_problems
        or truncated_evidence
        or not any("unknown" in item for item in truncated_access_problems)
    ):
        failures.append("truncated evaluator evidence did not remain rejected or unknown")

    duplicate_evaluator = [
        *actions[:2], shell_action("duplicate-eval-call", 53, command), *actions[2:],
    ]
    duplicate_evidence, duplicate_problems = _native_command_evidence(
        duplicate_evaluator, "opencode", workspace, series_path,
    )
    if not duplicate_problems or duplicate_evidence:
        failures.append("duplicate exact development evaluators were accepted")

    nested = [{
        "tool": "bash",
        "command": "opencode run --model caller/model task",
        "command_truncated": False,
    }]
    if not _research_action_problems(
        nested, "opencode", workspace, trusted_root,
        trace=_complete_access_trace(nested, "opencode"),
        series_manifest_path=series_path,
    ):
        failures.append("nested native model launch escaped the parsed-action audit")

    missing_exit = [dict(action) for action in actions]
    missing_exit[1] = shell_action("eval-call", 52, command, exit_code=None)
    failed_evidence, failed_problems = _native_command_evidence(
        missing_exit, "opencode", workspace, series_path,
    )
    if not failed_problems or failed_evidence:
        failures.append("missing explicit native exit metadata was accepted as a command pass")

    incomplete_evaluator = [dict(action) for action in actions]
    incomplete_evaluator[1] = shell_action("eval-call", 52, command, status="running")
    incomplete_evidence, incomplete_problems = _native_command_evidence(
        incomplete_evaluator, "opencode", workspace, series_path,
    )
    if not incomplete_problems or incomplete_evidence:
        failures.append("incomplete native evaluator lifecycle was accepted")

    late_code = [actions[1], file_write(CODE_REL, "late-code-write", 53), *actions[2:]]
    _late_code_evidence, late_code_problems = _native_command_evidence(
        late_code, "opencode", workspace, series_path,
    )
    if not any("after the final callback implementation write" in item for item in late_code_problems):
        failures.append("development evaluator was accepted before the final callback write")

    early_result = [
        actions[0], file_write(RESULT_REL, "early-result-write", 51),
        *actions[1:5], *actions[6:],
    ]
    _early_result_evidence, early_result_problems = _native_command_evidence(
        early_result, "opencode", workspace, series_path,
    )
    if not any("written after the completed development evaluation" in item for item in early_result_problems):
        failures.append("typed result artifact was accepted before evaluator completion")

    mutating_shell = shell_action(
        "mutating-shell", 58, f"touch {REPORT_REL}",
    )
    mutating_actions = [*actions[:5], mutating_shell, *actions[5:]]
    mutating_access_problems = _research_action_problems(
        mutating_actions, "opencode", workspace, trusted_root,
        trace=_complete_access_trace(mutating_actions, "opencode"),
        series_manifest_path=series_path,
    )
    if not any("unknown" in item for item in mutating_access_problems):
        failures.append("unclassified mutating shell evidence was discarded instead of failing the access audit")


def _check_codex_command_evidence(failures: list[str], workspace: Path, trusted_root: Path) -> None:
    series_path = "examples/fresh-agent-evals/research/series/contributor-reliability-research-1.json"
    inner_command = _expected_eval_command(series_path)
    native_command = f"/bin/bash -lc {shlex.quote(inner_command)}"
    encoded_command = native_command.encode("utf-8")
    expected_command_sha256 = "446fca0721b1621c834b3f9391bcb90a7db869eed5506a0b96f2f75b4c3bcb44"

    def command_event(
        event_type: str,
        item_id: str,
        command: str | None,
        *,
        status: str,
        exit_code: int | None = None,
    ) -> dict:
        item = {"id": item_id, "type": "command_execution", "status": status}
        if command is not None:
            item["command"] = command
        if exit_code is not None:
            item["exit_code"] = exit_code
        return {"type": event_type, "item": item}

    def file_change_event(item_id: str, path: str) -> dict:
        return {
            "type": "item.completed",
            "item": {
                "id": item_id,
                "type": "file_change",
                "status": "completed",
                "changes": [{"path": path, "kind": "update"}],
            },
        }

    def audit(trace: dict, selected_series: str = series_path) -> list[str]:
        return _research_action_problems(
            trace["actions"], "codex", workspace, trusted_root,
            trace=trace, series_manifest_path=selected_series,
        )

    events = [
        {"type": "thread.started", "thread_id": "thread-reliability-research"},
        file_change_event("codex-code-write", CODE_REL),
        command_event(
            "item.completed", "codex-fixture-schema-read",
            f"/bin/bash -lc {shlex.quote('cat examples/fresh-agent-evals/research/fixture.schema.json')}",
            status="completed", exit_code=0,
        ),
        *[
            command_event(
                "item.completed", f"codex-prefill-{index}",
                f"/bin/bash -lc {shlex.quote('cat README.md')}",
                status="completed", exit_code=0,
            )
            for index in range(7)
        ],
        command_event("item.started", "item_5", native_command, status="in_progress"),
        command_event(
            "item.completed", "item_5", native_command,
            status="completed", exit_code=0,
        ),
        file_change_event("codex-result-write", RESULT_REL),
    ]
    trace = _codex_event_jsonl(events, workspace, trusted_root)
    evaluator_actions = [
        action for action in trace["actions"]
        if action.get("native_item_id") == "item_5"
    ]
    evaluator = evaluator_actions[0] if len(evaluator_actions) == 1 else {}
    evidence, evidence_problems = _native_command_evidence(
        trace["actions"], "codex", workspace, series_path,
    )
    access_problems = audit(trace)
    if (
        len(encoded_command) != 311
        or hashlib.sha256(encoded_command).hexdigest() != expected_command_sha256
        or trace.get("trace_complete") is not True
        or len(evaluator_actions) != 1
        or evaluator.get("command") != native_command
        or evaluator.get("command_truncated") is not False
        or evaluator.get("command_bytes") != 311
        or evaluator.get("command_sha256") != expected_command_sha256
        or evaluator.get("normalized_command") != inner_command
        or explicit_exit_code(evaluator, "codex") != 0
        or evaluator.get("event_sequence") != 10
        or evaluator.get("completion_sequence") != 11
        or evaluator.get("native_item_id_sha256") != "9462965f2002ffb6e5ebf84d633936910b584441932a9dee26f8497eb9d82c75"
        or evidence_problems
        or evidence.get("explicit_native_exit_code") != 0
        or evidence.get("command_sha256") != expected_command_sha256
        or evidence.get("command_bytes") != 311
        or evidence.get("write_sequences") != {
            CODE_REL: [1], REPORT_REL: [11], RESULT_REL: [12],
        }
        or access_problems
    ):
        failures.append(
            "Codex full 311-byte declared research command, native lifecycle, or allowlisted fixture.schema.json read was not evidenced"
        )

    duplicate_code_events = [*events[:2], file_change_event("codex-code-write", CODE_REL), *events[2:]]
    duplicate_code_trace = _codex_event_jsonl(duplicate_code_events, workspace, trusted_root)
    duplicate_code_actions = [
        action for action in duplicate_code_trace["actions"]
        if action.get("tool") == "file_change" and action.get("native_item_id") == "codex-code-write"
    ]
    duplicate_code_evidence, duplicate_code_problems = _native_command_evidence(
        duplicate_code_trace["actions"], "codex", workspace, series_path,
    )
    if (
        len(duplicate_code_actions) != 1
        or duplicate_code_actions[0].get("change_index") != 0
        or duplicate_code_actions[0].get("terminal_event_count") != 2
        or duplicate_code_evidence.get("write_sequences", {}).get(CODE_REL) != [1]
        or duplicate_code_problems
    ):
        failures.append("duplicate Codex file_change terminal created an independent research write boundary")

    duplicate_report_events = [
        *events[:12],
        file_change_event("codex-report-duplicate", REPORT_REL),
        file_change_event("codex-report-duplicate", REPORT_REL),
        *events[12:],
    ]
    duplicate_report_trace = _codex_event_jsonl(duplicate_report_events, workspace, trusted_root)
    duplicate_report_actions = [
        action for action in duplicate_report_trace["actions"]
        if action.get("tool") == "file_change" and action.get("native_item_id") == "codex-report-duplicate"
    ]
    duplicate_report_evidence, duplicate_report_problems = _native_command_evidence(
        duplicate_report_trace["actions"], "codex", workspace, series_path,
    )
    if (
        len(duplicate_report_actions) != 1
        or duplicate_report_evidence.get("write_sequences", {}).get(REPORT_REL) != [11, 12]
        or not any("comparison report was written again" in item for item in duplicate_report_problems)
    ):
        failures.append("duplicate Codex report terminal created independent research write/causal boundaries")

    conflicting_code_events = [
        *events[:2], file_change_event("codex-code-write", "docs/conflict.md"), *events[2:],
    ]
    conflicting_code_trace = _codex_event_jsonl(conflicting_code_events, workspace, trusted_root)
    conflicting_code_actions = [
        action for action in conflicting_code_trace["actions"]
        if action.get("tool") == "file_change" and action.get("native_item_id") == "codex-code-write"
    ]
    conflicting_code_evidence, conflicting_code_problems = _native_command_evidence(
        conflicting_code_trace["actions"], "codex", workspace, series_path,
    )
    if (
        len(conflicting_code_actions) != 1
        or conflicting_code_actions[0].get("lifecycle_conflict") is not True
        or _path_write_sequences(conflicting_code_actions, CODE_REL, "codex", workspace)
        or conflicting_code_evidence
        or not conflicting_code_problems
        or not any("unknown" in item for item in audit(conflicting_code_trace))
    ):
        failures.append("conflicting Codex file_change terminals retained usable research write evidence")

    missing_identity_events = [
        *events[:1], file_change_event("", CODE_REL), *events[2:],
    ]
    # Empty native IDs are deliberately treated as missing; the remaining item
    # count and the path alone cannot reconstruct the write identity.
    missing_identity_events[1]["item"].pop("id", None)
    missing_identity_trace = _codex_event_jsonl(missing_identity_events, workspace, trusted_root)
    missing_identity_actions = [
        action for action in missing_identity_trace["actions"] if action.get("tool") == "file_change"
    ]
    missing_identity_code = [
        action for action in missing_identity_actions if action.get("path") == CODE_REL
    ]
    missing_identity_evidence, missing_identity_problems = _native_command_evidence(
        missing_identity_trace["actions"], "codex", workspace, series_path,
    )
    if (
        len(missing_identity_actions) != 2
        or len(missing_identity_code) != 1
        or missing_identity_code[0].get("native_item_id_sha256") is not None
        or _path_write_sequences(missing_identity_actions, CODE_REL, "codex", workspace)
        or missing_identity_evidence
        or not missing_identity_problems
        or not any("unknown" in item for item in audit(missing_identity_trace))
    ):
        failures.append("Codex research write was inferred from file-change count/path without native identity")

    missing_index_events = [*events]
    missing_index_events[1] = file_change_event("codex-code-write", CODE_REL)
    missing_index_events[1]["item"]["changes"] = []
    missing_index_trace = _codex_event_jsonl(missing_index_events, workspace, trusted_root)
    missing_index_actions = [
        action for action in missing_index_trace["actions"] if action.get("tool") == "file_change"
    ]
    missing_index_code = [
        action for action in missing_index_actions if action.get("native_item_id") == "codex-code-write"
    ]
    if (
        len(missing_index_actions) != 2
        or len(missing_index_code) != 1
        or missing_index_code[0].get("change_index") is not None
        or _path_write_sequences(missing_index_actions, CODE_REL, "codex", workspace)
        or not any("unknown" in item for item in audit(missing_index_trace))
    ):
        failures.append("Codex research file changes without a change index were attributed as writes")

    truncated_actions = [dict(action) for action in trace["actions"]]
    truncated_evaluator = next(
        (action for action in truncated_actions if action.get("native_item_id") == "item_5"),
        None,
    )
    if truncated_evaluator is not None:
        truncated_evaluator["command"] = native_command[:300]
        truncated_evaluator["command_truncated"] = True
    truncated_trace = dict(trace, actions=truncated_actions)
    truncated_evidence, truncated_evidence_problems = _native_command_evidence(
        truncated_actions, "codex", workspace, series_path,
    )
    truncated_access_problems = audit(truncated_trace)
    if (
        truncated_evaluator is None
        or truncated_evidence
        or not truncated_evidence_problems
        or not any("unknown" in item for item in truncated_access_problems)
        or not any("reserved fixture or protected grader source" in item for item in truncated_access_problems)
    ):
        failures.append("truncated Codex preview or normalized inner command was trusted in place of full native text")

    mismatched_actions = [dict(action) for action in trace["actions"]]
    mismatched_evaluator = next(
        (action for action in mismatched_actions if action.get("native_item_id") == "item_5"),
        None,
    )
    if mismatched_evaluator is not None:
        mismatched_evaluator["command_sha256"] = "0" * 64
    mismatched_evidence, mismatched_problems = _native_command_evidence(
        mismatched_actions, "codex", workspace, series_path,
    )
    if mismatched_evaluator is None or mismatched_evidence or not mismatched_problems:
        failures.append("Codex declared evaluator evidence ignored a full-command digest mismatch")

    suffix_inner = inner_command + " && cat examples/fresh-agent-evals/research/research_grader.py"
    suffix_command = f"/bin/bash -lc {shlex.quote(suffix_inner)}"
    suffix_encoded = suffix_command.encode("utf-8")
    suffix_trace = _codex_event_jsonl([
        {"type": "thread.started", "thread_id": "thread-codex-reserved-suffix"},
        command_event(
            "item.started", "codex-reserved-suffix", suffix_command, status="in_progress",
        ),
        command_event(
            "item.completed", "codex-reserved-suffix", suffix_command,
            status="completed", exit_code=0,
        ),
    ], workspace, trusted_root)
    suffix_action = next(
        (action for action in suffix_trace["actions"] if action.get("native_item_id") == "codex-reserved-suffix"),
        {},
    )
    suffix_evidence, suffix_evidence_problems = _native_command_evidence(
        suffix_trace["actions"], "codex", workspace, series_path,
    )
    suffix_access_problems = audit(suffix_trace)
    parsed_suffix = parse_shell_command(suffix_command, "codex")
    if (
        not 300 < len(suffix_encoded) <= MAX_COMMAND_BYTES
        or suffix_action.get("command") != suffix_command
        or suffix_action.get("command_truncated") is not False
        or not command_sha256_matches(suffix_action)
        or parsed_suffix is None
        or len(parsed_suffix.segments) != 2
        or suffix_evidence
        or not suffix_evidence_problems
        or not any("reserved fixture or protected grader source" in item for item in suffix_access_problems)
    ):
        failures.append("full Codex command suffix containing a reserved grader read was not rejected")

    conflicting_trace = _codex_event_jsonl([
        {"type": "thread.started", "thread_id": "thread-codex-command-conflict"},
        command_event("item.started", "codex-conflicting-evaluator", native_command, status="in_progress"),
        command_event(
            "item.completed", "codex-conflicting-evaluator", native_command + " ",
            status="completed", exit_code=0,
        ),
    ], workspace, trusted_root)
    conflicting_action = conflicting_trace["actions"][0]
    if (
        conflicting_action.get("command") is not None
        or conflicting_action.get("command_sha256") is not None
        or conflicting_action.get("command_truncated") is not None
        or command_sha256_matches(conflicting_action)
        or not any("unknown" in item for item in audit(conflicting_trace))
    ):
        failures.append("conflicting Codex lifecycle command values were not left unknown")

    missing_trace = _codex_event_jsonl([
        {"type": "thread.started", "thread_id": "thread-codex-missing-command"},
        command_event(
            "item.completed", "codex-missing-command", None,
            status="completed", exit_code=0,
        ),
    ], workspace, trusted_root)
    missing_action = missing_trace["actions"][0]
    if (
        missing_action.get("command") is not None
        or missing_action.get("command_sha256") is not None
        or explicit_exit_code(missing_action, "codex") != 0
        or not any("unknown" in item for item in audit(missing_trace))
    ):
        failures.append("Codex command with an explicit exit but missing native text was trusted")

    redacted_secret = "sk-" + "A" * 24
    redacted_command = f"/bin/bash -lc {shlex.quote(f'cat {redacted_secret} README.md')}"
    redacted_trace = _codex_event_jsonl([
        {"type": "thread.started", "thread_id": "thread-codex-redacted-command"},
        command_event(
            "item.completed", "codex-redacted-command", redacted_command,
            status="completed", exit_code=0,
        ),
    ], workspace, trusted_root)
    redacted_action = redacted_trace["actions"][0]
    if (
        "<REDACTED" not in str(redacted_action.get("command"))
        or not command_sha256_matches(redacted_action)
        or parse_shell_command(redacted_action.get("command"), "codex") is not None
        or not any("unknown" in item for item in audit(redacted_trace))
    ):
        failures.append("redacted Codex command text was treated as executable evidence")

    oversized_inner = f"grep {shlex.quote('é' * (MAX_COMMAND_BYTES // 2 + 100))} README.md"
    oversized_command = f"/bin/bash -lc {shlex.quote(oversized_inner)}"
    oversized_bytes = oversized_command.encode("utf-8")
    oversized_trace = _codex_event_jsonl([
        {"type": "thread.started", "thread_id": "thread-codex-oversized-command"},
        command_event(
            "item.completed", "codex-oversized-command", oversized_command,
            status="completed", exit_code=0,
        ),
    ], workspace, trusted_root)
    oversized_action = oversized_trace["actions"][0]
    if (
        len(oversized_command) >= MAX_COMMAND_BYTES
        or len(oversized_bytes) <= MAX_COMMAND_BYTES
        or oversized_action.get("command") is not None
        or oversized_action.get("command_truncated") is not True
        or oversized_action.get("command_bytes") != len(oversized_bytes)
        or oversized_action.get("command_sha256") != _digest(oversized_bytes)
        or command_sha256_matches(oversized_action)
        or not any("unknown" in item for item in audit(oversized_trace))
    ):
        failures.append("oversized Codex command measured in UTF-8 bytes did not remain unknown")


def _check_research_access_audit(failures: list[str], workspace: Path, trusted_root: Path) -> None:
    series_path = MANIFEST_REL
    contract = load_fixture_contract(trusted_root, "research", CASE_ID)

    def problems(
        actions: list,
        harness: str = "opencode",
        *,
        trace: dict | None = None,
        causal_actions: dict[str, object] | None = None,
    ) -> list[str]:
        return _research_action_problems(
            actions,
            harness,
            workspace,
            trusted_root,
            trace=trace if trace is not None else _complete_access_trace(actions, harness),
            series_manifest_path=series_path,
            causal_actions=causal_actions,
        )

    def route_trace(events: list[dict], target_workspace: Path | None = None) -> dict:
        raw = b"".join(
            (json.dumps(event, separators=(",", ":")) + "\n").encode()
            for event in events
        )
        return normalize_opencode(raw, target_workspace or workspace, trusted_root)

    def native_read_event(
        call_id: str,
        relative: str,
        *,
        offset: int | None = 1,
        limit: int | None = None,
        output: str | None = None,
        status: str = "completed",
    ) -> dict:
        native_path = f"<WORKSPACE>/{relative}"
        envelope_path = str((workspace / relative).resolve())
        arguments = {"filePath": native_path}
        if offset is not None:
            arguments["offset"] = offset
        source_lines = (workspace / relative).read_text(encoding="utf-8").splitlines()
        if limit is None:
            limit = len(source_lines) + 1
        arguments["limit"] = limit
        if output is None:
            start_index = 0 if offset in {None, 0} else offset - 1
            selected = source_lines[start_index:start_index + limit]
            body = "\n".join(
                f"{start_index + index + 1}: {line}"
                for index, line in enumerate(selected)
            )
            last_line = start_index + len(selected)
            if last_line == len(source_lines):
                footer = f"(End of file - total {len(source_lines)} lines)"
            else:
                footer = f"(Output capped at 50 KB. Showing lines {start_index + 1}-{last_line}. Use offset={last_line + 1} to continue.)"
            output = (
                f"<path>{envelope_path}</path>\n<type>file</type>\n<content>\n"
                f"{body}\n{footer}\n</content>"
            )
        return {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "read", "callID": call_id,
                "state": {"status": status, "input": arguments, "output": output},
            },
        }

    def shell_route_event(
        call_id: str, command: str, *, exit_code: int = 0,
        status: str = "completed", output: str = "synthetic command output",
    ) -> dict:
        return {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "bash", "callID": call_id,
                "state": {
                    "status": status, "input": {"command": command},
                    "output": output, "metadata": {"exit": exit_code},
                },
            },
        }

    contract_paths = tuple(dict.fromkeys((
        *contract["available_input_paths"],
        *contract["deliverables"],
        *contract["scratch_paths"],
    )))
    useful_reads = (*contract_paths, series_path)
    for relative in useful_reads:
        action = _access_action("read", f"<WORKSPACE>/{relative}")
        observations = normalize_access_observations([action], "opencode", workspace)
        if (
            problems([action])
            or len(observations) != 1
            or observations[0].get("operation") != "file_content_read"
            or observations[0].get("scope", {}).get("safe_paths") != [relative]
        ):
            failures.append(f"contract-declared native read was rejected or misnormalized: {relative}")
            break

    def bounded_shell_trace(harness: str, call_id: str, command: str, output: str) -> dict:
        if harness == "opencode":
            return route_trace([shell_route_event(call_id, command, output=output)])
        wrapped = f"/bin/bash -lc {shlex.quote(command)}"
        return _codex_event_jsonl([
            {"type": "thread.started", "thread_id": f"thread-{call_id}"},
            {
                "type": "item.completed",
                "item": {
                    "id": call_id, "type": "command_execution", "command": wrapped,
                    "status": "completed", "exit_code": 0,
                    "aggregated_output": output,
                },
            },
        ], workspace, trusted_root)

    for index, relative in enumerate(contract_paths):
        for harness in ("opencode", "codex"):
            shell_trace = bounded_shell_trace(
                harness, f"contract-shell-read-{index}-{harness}",
                f"cat {relative}", "synthetic bounded content output",
            )
            shell_actions = shell_trace["actions"]
            observations = normalize_access_observations(shell_actions, harness, workspace)
            if (
                problems(shell_actions, harness, trace=shell_trace)
                or len(observations) != 1
                or observations[0].get("operation") != "file_content_read"
                or observations[0].get("scope", {}).get("safe_paths") != [relative]
            ):
                failures.append(
                    f"{harness} bounded shell read did not follow the canonical contract path: {relative}"
                )
                break

    allowed_shell = [
        _access_action("bash", "python scripts/start_here.py --task research"),
        _access_action(
            "bash",
            f"python scripts/start_here.py --task research --fixture {CASE_ID} --series-manifest {series_path}",
        ),
        _access_action("bash", "cat examples/fresh-agent-evals/research/task.md"),
        _access_action(
            "bash",
            "sed -n '1,8p' examples/fresh-agent-evals/research/callback-contract.md",
        ),
        _access_action(
            "bash",
            "grep -n callback examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/primary-source-excerpt.md",
        ),
        _access_action(
            "bash",
            "rg callback examples/fresh-agent-evals/research/task.md",
        ),
        _access_action("bash", "cat trial-output/challenger.py"),
        _access_action("bash", f"sha256sum {series_path}"),
        _access_action(
            "bash",
            "shasum -a 256 examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/primary-source-excerpt.md",
        ),
        _access_action("bash", "git diff --check"),
        _access_action(
            "bash",
            next(
                item["command"] for item in render_finish_commands(contract, "research", series_path)
                if item["check_id"] == "research-completion-check"
            ),
        ),
    ]
    for action in allowed_shell:
        if problems([action]):
            failures.append("bounded documented shell read or hash command was rejected")
            break

    def normalized_native_file_action(
        tool: str, arguments: dict, call_id: str, *,
        status: str = "completed", output: object | None = None,
    ) -> tuple[dict, dict]:
        if output is None:
            output = "1: synthetic output" if tool == "read" else "synthetic output"
        event = {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": tool, "callID": call_id,
                "state": {"status": status, "input": arguments, "output": output},
            },
        }
        raw = (json.dumps(event, separators=(",", ":")) + "\n").encode()
        trace = normalize_opencode(raw, workspace, trusted_root)
        return trace, trace["actions"][0]

    discovery_trace, discovery_action = normalized_native_file_action(
        "glob", {"path": "examples/fresh-agent-evals/research", "pattern": "research_grader.py"},
        "filename-only-research-grader",
    )
    if problems([discovery_action], trace=discovery_trace):
        failures.append("filename-only discovery of a protected module was treated as reading its contents")
    protected_read_trace, protected_read_action = normalized_native_file_action(
        "read", {"path": "examples/fresh-agent-evals/research/research_grader.py"},
        "protected-content-read",
        output=(
            "<path>examples/fresh-agent-evals/research/research_grader.py</path>\n"
            "<type>file</type>\n<content>\n1: protected source output\n"
            "(End of file - total 1 lines)\n</content>"
        ),
    )
    protected_read_observation = normalize_access_observations(
        [protected_read_action], "opencode", workspace,
    )[0]
    protected_causes: dict[str, object] = {}
    protected_problems = problems(
        [protected_read_action], trace=protected_read_trace,
        causal_actions=protected_causes,
    )
    if (
        not protected_problems
        or protected_read_action.get("native_output_result_type") != "file_content"
        or protected_read_observation.get("returned_evidence") != "content_observed"
    ):
        failures.append("native content read of protected research grader source was accepted")

    frozen_subject_paths = set(frozen_paths(trusted_root))
    if not BLOCKED_SUBJECT_PATHS <= frozen_subject_paths:
        failures.append("research protected-source predicates include paths outside the frozen subject closure")
    for index, relative in enumerate(sorted(BLOCKED_SUBJECT_PATHS)):
        if not (workspace / relative).is_file():
            failures.append(f"frozen protected-source fixture path is absent from the subject workspace: {relative}")
            continue
        native_trace, native_action = normalized_native_file_action(
            "read", {"path": relative}, f"blocked-native-source-{index}",
        )
        if not problems([native_action], trace=native_trace):
            failures.append(f"native read of a BLOCKED_SUBJECT_PATHS record was accepted: {relative}")
            break
        for harness in ("opencode", "codex"):
            shell_trace = bounded_shell_trace(
                harness, f"blocked-shell-source-{index}-{harness}",
                f"cat {relative}", "synthetic protected-source output",
            )
            if not problems(shell_trace["actions"], harness, trace=shell_trace):
                failures.append(
                    f"{harness} shell read of a BLOCKED_SUBJECT_PATHS record was accepted: {relative}"
                )
                break

    denied_trace, denied_action = normalized_native_file_action(
        "read", {"path": "examples/fresh-agent-evals/research/research_grader.py"},
        "denied-protected-read", status="denied", output="Permission denied",
    )
    denied_observation = normalize_access_observations([denied_action], "opencode", workspace)[0]
    if (
        not problems([denied_action], trace=denied_trace)
        or denied_observation.get("attempt_status") != "observed"
        or denied_observation.get("outcome_status") != "denied"
        or denied_observation.get("returned_evidence") != "unknown"
        or content_read_observations([denied_action], "opencode", workspace)
    ):
        failures.append("denied protected native read was lost or mislabeled as returned grader content")

    failed_protected_trace = route_trace([shell_route_event(
        "failed-protected-shell-read",
        "cat examples/fresh-agent-evals/research/research_grader.py",
        exit_code=1, status="failed", output="cat: requested source unavailable",
    )])
    failed_protected_action = failed_protected_trace["actions"][0]
    failed_protected_observation = normalize_access_observations(
        [failed_protected_action], "opencode", workspace,
    )[0]
    if (
        not problems([failed_protected_action], trace=failed_protected_trace)
        or failed_protected_observation.get("attempt_status") != "observed"
        or failed_protected_observation.get("outcome_status") != "failed"
        or failed_protected_observation.get("returned_evidence") != "unknown"
        or content_read_observations([failed_protected_action], "opencode", workspace)
    ):
        failures.append("failed protected shell read was discarded or credited as returned content")
    escaping_trace, escaping_action = normalized_native_file_action(
        "glob", {"path": "../", "pattern": "*.py"}, "escaping-filename-discovery",
    )
    if not any("unknown" in item for item in problems([escaping_action], trace=escaping_trace)):
        failures.append("escaping structured discovery path did not remain unknown")

    exclusion_action = _access_action(
        "bash",
        "rg --files -g AGENTS.md -g '!examples/fresh-agent-evals/**' -g '!trial-output/**'",
    )
    exclusion_problems = problems([exclusion_action])
    exclusion_observations = normalize_access_observations([exclusion_action], "opencode", workspace)
    if (
        exclusion_problems
        or len(exclusion_observations) != 1
        or exclusion_observations[0].get("operation") != "filename_discovery"
        or "examples/fresh-agent-evals/**" not in exclusion_observations[0].get("scope", {}).get("excluded_globs", [])
    ):
        failures.append("former exclusion-only rg --files command remained a protected-read false positive or lost scope")

    exact_exclusion = _access_action(
        "bash", "rg --files -g '!examples/fresh-agent-evals/research/research_grader.py'",
    )
    exact_exclusion_problems = problems([exact_exclusion])
    exact_exclusion_observations = normalize_access_observations(
        [exact_exclusion], "opencode", workspace,
    )
    if (
        exact_exclusion_problems
        or len(exact_exclusion_observations) != 1
        or exact_exclusion_observations[0].get("operation") != "filename_discovery"
        or "examples/fresh-agent-evals/research/research_grader.py"
        not in exact_exclusion_observations[0].get("scope", {}).get("excluded_globs", [])
        or content_read_observations([exact_exclusion], "opencode", workspace)
    ):
        failures.append("protected basename used only as an rg --files exclusion was treated as content access")

    exact_positive_discovery = _access_action(
        "bash", "rg --files -g 'examples/fresh-agent-evals/research/research_grader.py'",
    )
    positive_observations = normalize_access_observations(
        [exact_positive_discovery], "opencode", workspace,
    )
    if (
        problems([exact_positive_discovery])
        or len(positive_observations) != 1
        or positive_observations[0].get("operation") != "filename_discovery"
        or "examples/fresh-agent-evals/research/research_grader.py"
        not in positive_observations[0].get("scope", {}).get("positive_globs", [])
        or content_read_observations([exact_positive_discovery], "opencode", workspace)
    ):
        failures.append("positive protected filename discovery lost its explicit scope or gained read credit")

    unsupported_discovery = _access_action(
        "bash",
        "rg --files -g '!examples/fresh-agent-evals/research/research_grader.py' "
        "-g 'examples/fresh-agent-evals/research/task.md'",
    )
    unsupported_discovery_problems = problems([unsupported_discovery])
    unsupported_observations = normalize_access_observations(
        [unsupported_discovery], "opencode", workspace,
    )
    if (
        not unsupported_discovery_problems
        or len(unsupported_observations) != 1
        or unsupported_observations[0].get("operation") != "unknown"
    ):
        failures.append("unsupported rg --files polarity grammar did not remain unknown")

    with tempfile.TemporaryDirectory(prefix="of-research-directory-read-") as temporary:
        directory_workspace = Path(temporary)
        output_dir = directory_workspace / "trial-output"
        output_dir.mkdir()
        (output_dir / "challenger.py").write_text("def challenger(ctx):\n    return None\n", encoding="utf-8")
        output = {
            "type": "directory",
            "path": "trial-output",
            "entries": ["challenger.py", "comparison-report.json", "research-result.json"],
        }

        def directory_read_event(call_id: str, result: object = output) -> dict:
            state = {"status": "completed", "input": {"filePath": "trial-output"}}
            if result is not None:
                state["output"] = result
            return {"type": "tool_use", "part": {
                "type": "tool", "tool": "read", "callID": call_id, "state": state,
            }}

        raw_directory = (json.dumps(directory_read_event("trial-output-directory-read"), separators=(",", ":")) + "\n").encode()
        directory_trace = normalize_opencode(raw_directory, directory_workspace, trusted_root)
        directory_observation = normalize_access_observations(
            directory_trace["actions"], "opencode", directory_workspace,
        )[0]
        directory_problems = _research_action_problems(
            directory_trace["actions"], "opencode", directory_workspace, trusted_root,
            trace=directory_trace, series_manifest_path=series_path,
        )
        if (
            directory_trace["actions"][0].get("native_output_result_type") != "directory_listing"
            or directory_observation.get("operation") != "directory_listing"
            or directory_observation.get("returned_evidence") != "names_observed"
            or directory_problems
            or content_read_observations(directory_trace["actions"], "opencode", directory_workspace)
        ):
            failures.append("native read of trial-output directory was treated as content access instead of evidenced filename discovery")

        empty_output_dir = directory_workspace / "empty-output"
        empty_output_dir.mkdir()
        empty_output_path = str(empty_output_dir.resolve())
        empty_directory_output = (
            f"<path>{empty_output_path}</path>\n<type>directory</type>\n<entries>\n\n\n"
            "(0 entries)\n</entries>"
        )
        empty_frame = _native_directory_frame(
            empty_directory_output, "empty-output", directory_workspace,
        )
        empty_event = {"type": "tool_use", "part": {
            "type": "tool", "tool": "read", "callID": "trial-output-empty-directory-read",
            "state": {
                "status": "completed", "input": {"filePath": "empty-output"},
                "output": empty_directory_output,
            },
        }}
        empty_trace = route_trace([empty_event], directory_workspace)
        empty_action = empty_trace["actions"][0]
        empty_observation = normalize_access_observations(
            [empty_action], "opencode", directory_workspace,
        )[0]
        empty_route_sources, _selector, empty_route_complete = observed_route_sources(
            [empty_action], "opencode", directory_workspace,
        )
        empty_problems = _research_action_problems(
            [empty_action], "opencode", directory_workspace, trusted_root,
            trace=empty_trace, series_manifest_path=series_path,
        )
        if (
            empty_frame is None
            or empty_frame.get("entries") != []
            or empty_action.get("native_output_result_type") != "directory_listing"
            or empty_action.get("native_output_content_status") == "observed"
            or empty_observation.get("operation") != "directory_listing"
            or empty_observation.get("returned_evidence") not in {"names_observed", "empty_observed"}
            or empty_problems
            or content_read_observations([empty_action], "opencode", directory_workspace)
            or empty_route_sources
            or empty_route_complete
        ):
            failures.append("exact empty native directory envelope gained content or route credit instead of empty names-only evidence")

        prose_trace = normalize_opencode(
            (json.dumps(directory_read_event(
                "trial-output-prose",
                {
                    "type": "directory",
                    "path": "trial-output",
                    "entries": ["Permission denied: examples/fresh-agent-evals/research/research_grader.py"],
                },
            ), separators=(",", ":")) + "\n").encode(),
            directory_workspace, trusted_root,
        )
        prose_observation = normalize_access_observations(
            prose_trace["actions"], "opencode", directory_workspace,
        )[0]
        if (
            prose_trace["actions"][0]["outcome"].get("native_tool_status") != "completed"
            or prose_observation.get("returned_evidence") != "unknown"
            or prose_observation.get("operation") != "directory_listing"
        ):
            failures.append("freeform directory output prose was used to infer permission denial or filename results")

        protected_directory = directory_workspace / "examples/fresh-agent-evals/research"
        protected_directory.mkdir(parents=True)
        (protected_directory / "research_grader.py").write_text("protected filename\n", encoding="utf-8")
        protected_entries = ["research_grader.py"]
        native_protected = route_trace([{
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "read", "callID": "protected-directory-list",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "examples/fresh-agent-evals/research"},
                    "output": {
                        "type": "directory",
                        "path": "examples/fresh-agent-evals/research",
                        "entries": protected_entries,
                    },
                },
            },
        }], directory_workspace)
        shell_protected = route_trace([shell_route_event(
            "protected-directory-ls",
            "ls examples/fresh-agent-evals/research",
            output="research_grader.py\n",
        )], directory_workspace)
        native_protected_action = native_protected["actions"][0]
        shell_protected_action = shell_protected["actions"][0]
        native_protected_observation = normalize_access_observations(
            [native_protected_action], "opencode", directory_workspace,
        )[0]
        shell_protected_observation = normalize_access_observations(
            [shell_protected_action], "opencode", directory_workspace,
        )[0]
        native_protected_problems = _research_action_problems(
            native_protected["actions"], "opencode", directory_workspace, trusted_root,
            trace=native_protected, series_manifest_path=series_path,
        )
        shell_protected_problems = _research_action_problems(
            shell_protected["actions"], "opencode", directory_workspace, trusted_root,
            trace=shell_protected, series_manifest_path=series_path,
        )
        if (
            native_protected_problems
            or shell_protected_problems
            or native_protected_observation.get("operation") != "directory_listing"
            or shell_protected_observation.get("operation") != "directory_listing"
            or native_protected_observation.get("returned_evidence") != "names_observed"
            or shell_protected_observation.get("returned_evidence") != "names_observed"
            or content_read_observations(native_protected["actions"], "opencode", directory_workspace)
            or content_read_observations(shell_protected["actions"], "opencode", directory_workspace)
        ):
            failures.append("equivalent protected native/shell filename listings were not accepted as discovery-only research actions")

        unknown_trace = normalize_opencode(
            (json.dumps(directory_read_event("trial-output-no-framing", None), separators=(",", ":")) + "\n").encode(),
            directory_workspace, trusted_root,
        )
        if normalize_access_observations(
            unknown_trace["actions"], "opencode", directory_workspace,
        )[0].get("operation") != "unknown":
            failures.append("native read of a directory without output framing was not left unknown")

    paginated_read_lines = [f"route line {number:04d} " + "x" * 42 for number in range(1, 1201)]
    literal_source = workspace / "literal-native-frame.md"
    literal_source.write_text("literal separator regression\n", encoding="utf-8")
    literal_source_path = str(literal_source.resolve())
    literal_separator_output = (
        f"<path>{literal_source_path}</path>\n<type>file</type>\n<content>\n"
        "1: literal separator regression\n\n"
        "(End of file - total 1 lines)\n</content>"
    )
    literal_separator_trace = route_trace([native_read_event(
        "research-literal-footer-separator", "literal-native-frame.md", offset=1,
        limit=1, output=literal_separator_output,
    )])
    literal_separator_action = literal_separator_trace["actions"][0]
    literal_separator_credit = content_read_observations(
        [literal_separator_action], "opencode", workspace,
    )
    if (
        literal_separator_action.get("native_output_result_type") != "file_content"
        or literal_separator_action.get("native_output_file_footer") != "eof"
        or len(literal_separator_credit) != 1
    ):
        failures.append("literal native file envelope with the footer separator did not pass the research access audit")

    readme_envelope_path = str((workspace / "README.md").resolve())
    readme_page_one = (
        f"<path>{readme_envelope_path}</path>\n<type>file</type>\n<content>\n"
        + "\n".join(f"{number}: {paginated_read_lines[number - 1]}" for number in range(1, 1060))
        + "\n\n(Output capped at 50 KB. Showing lines 1-1059. Use offset=1060 to continue.)\n</content>"
    )
    readme_page_two = (
        f"<path>{readme_envelope_path}</path>\n<type>file</type>\n<content>\n"
        + "\n".join(f"{number}: {paginated_read_lines[number - 1]}" for number in range(1060, 1201))
        + "\n\n(End of file - total 1200 lines)\n</content>"
    )
    route_events = [
        native_read_event(
            "research-root-readme-page-one", "README.md", offset=1, limit=2000,
            output=readme_page_one,
        ),
        native_read_event(
            "research-root-readme-page-two", "README.md", offset=1060, limit=2000,
            output=readme_page_two,
        ),
        native_read_event("research-root-agents", "AGENTS.md"),
        shell_route_event("research-selector", "python scripts/start_here.py --task research"),
        native_read_event("research-route-readme", "examples/fresh-agent-evals/research/README.md"),
        native_read_event("research-task-route", "examples/fresh-agent-evals/research/task.md"),
    ]
    native_route_trace = route_trace(route_events)
    route = entry_route_evidence(
        native_route_trace["actions"], "opencode", workspace,
        task="research",
        task_route_paths=(
            "examples/fresh-agent-evals/research/README.md",
            "examples/fresh-agent-evals/research/task.md",
        ),
    )
    route_read_credit = content_read_observations(
        native_route_trace["actions"], "opencode", workspace,
    )
    route_access_problems = problems(
        native_route_trace["actions"], trace=native_route_trace,
    )
    if (
        route.get("status") != "pass"
        or route_access_problems
        or len([item for item in route_read_credit if item.get("path") == "README.md"]) != 1
        or len(next(
            item for item in route_read_credit if item.get("path") == "README.md"
        ).get("read_pages", [])) != 2
    ):
        failures.append("research active route/access gates rejected the contiguous native README page chain or credited it before EOF")

    compound_command = (
        "cat README.md AGENTS.md && "
        "python scripts/start_here.py --task research && "
        "cat examples/fresh-agent-evals/research/README.md examples/fresh-agent-evals/research/task.md"
    )
    compound_trace = route_trace([shell_route_event("research-compound-route", compound_command)])
    compound_route = entry_route_evidence(
        compound_trace["actions"], "opencode", workspace,
        task="research",
        task_route_paths=(
            "examples/fresh-agent-evals/research/README.md",
            "examples/fresh-agent-evals/research/task.md",
        ),
    )
    compound_observations = content_read_observations(
        compound_trace["actions"], "opencode", workspace,
    )
    if (
        compound_route.get("status") != "pass"
        or [item["path"] for item in compound_observations]
        != [
            "README.md", "AGENTS.md",
            "examples/fresh-agent-evals/research/README.md",
            "examples/fresh-agent-evals/research/task.md",
        ]
        or [(item.get("segment_index"), item.get("operand_index")) for item in compound_observations]
        != [(0, 0), (0, 1), (2, 0), (2, 1)]
        or len({item["action"]["identity_sha256"] for item in compound_observations}) != 1
        or problems(compound_trace["actions"], trace=compound_trace)
    ):
        failures.append("ordered research root reads, selector, and route reads in one command did not pass with action-level identity and local positions")

    reversed_compound_command = (
        "cat examples/fresh-agent-evals/research/README.md examples/fresh-agent-evals/research/task.md && "
        "python scripts/start_here.py --task research && "
        "cat README.md AGENTS.md"
    )
    reversed_compound_trace = route_trace([
        shell_route_event("research-reversed-compound-route", reversed_compound_command),
    ])
    if entry_route_evidence(
        reversed_compound_trace["actions"], "opencode", workspace,
        task="research",
        task_route_paths=(
            "examples/fresh-agent-evals/research/README.md",
            "examples/fresh-agent-evals/research/task.md",
        ),
    ).get("status") == "pass":
        failures.append("research task reads and selector before root reads passed the compound route")

    ordered_argument_trace = route_trace([shell_route_event(
        "research-ordered-operands", "cat docs/agents/research-security.md README.md",
    )])
    ordered_segment_trace = route_trace([shell_route_event(
        "research-ordered-segments", "cat docs/agents/research-security.md && cat README.md",
    )])
    expected_ordered_reads = [
        ("docs/agents/research-security.md", 0, 0), ("README.md", 0, 1),
    ]
    observed_argument_reads = [
        (item["path"], item.get("segment_index"), item.get("operand_index"))
        for item in content_read_observations(ordered_argument_trace["actions"], "opencode", workspace)
    ]
    observed_segment_reads = [
        (item["path"], item.get("segment_index"), item.get("operand_index"))
        for item in content_read_observations(ordered_segment_trace["actions"], "opencode", workspace)
    ]
    if (
        observed_argument_reads != expected_ordered_reads
        or observed_segment_reads != [
            ("docs/agents/research-security.md", 0, 0), ("README.md", 1, 0),
        ]
    ):
        failures.append("research shell observations did not retain the original operand and segment order")

    def measured_route_with_readme(readme_event: dict) -> tuple[dict, dict]:
        events = [
            readme_event,
            native_read_event("measured-route-agents", "AGENTS.md"),
            shell_route_event("measured-route-selector", "python scripts/start_here.py --task research"),
            native_read_event("measured-route-readme", "examples/fresh-agent-evals/research/README.md"),
            native_read_event("measured-route-task", "examples/fresh-agent-evals/research/task.md"),
        ]
        trace = route_trace(events)
        evidence = entry_route_evidence(
            trace["actions"], "opencode", workspace,
            task="research",
            task_route_paths=(
                "examples/fresh-agent-evals/research/README.md",
                "examples/fresh-agent-evals/research/task.md",
            ),
        )
        return trace, evidence

    partial_research_route, partial_research_evidence = measured_route_with_readme(
        native_read_event(
            "partial-research-route-readme", "README.md", offset=1, limit=1,
            output="partial first line",
        ),
    )
    empty_research_route, empty_research_evidence = measured_route_with_readme(
        native_read_event(
            "empty-research-route-readme", "README.md", output="",
        ),
    )
    if (
        partial_research_evidence.get("status") == "pass"
        or empty_research_evidence.get("status") == "pass"
        or any(
            item.get("path") == "README.md"
            for item in content_read_observations(partial_research_route["actions"], "opencode", workspace)
        )
        or any(
            item.get("path") == "README.md"
            for item in content_read_observations(empty_research_route["actions"], "opencode", workspace)
        )
    ):
        failures.append("partial or empty research root reads earned full-source route credit")

    injected_only = entry_route_evidence(
        [], "opencode", workspace,
        task="research",
        task_route_paths=(
            "examples/fresh-agent-evals/research/README.md",
            "examples/fresh-agent-evals/research/task.md",
        ),
    )
    if injected_only.get("status") == "pass":
        failures.append("injected research task text alone satisfied native route criteria")

    expected = _expected_eval_command(series_path)
    report_change = {
        "tool": "file_change",
        "native_item_id": "contract-declared-report-change",
        "native_item_id_sha256": _digest(b"contract-declared-report-change"),
        "path": REPORT_REL,
        "path_status": "workspace_relative",
        "change_index": 0,
        "event_sequence": 5,
        "completion_sequence": 6,
        "correlation_conflict": False,
        "lifecycle_conflict": False,
    }
    output_edits = [
        *[_access_action("write", relative) for relative in contract["deliverables"] if relative != REPORT_REL],
        *[_access_action("write", relative) for relative in contract["scratch_paths"]],
        _access_action("bash", expected),
        report_change,
    ]
    if problems(output_edits):
        failures.append("native edits/evaluator report write to declared outputs and optional scratch were rejected")

    exact_opencode = _access_action("bash", expected)
    if problems([exact_opencode]):
        failures.append("exact selected-series development evaluator was rejected")
    exact_codex = _access_action("command_execution", f"bash -lc {shlex.quote(expected)}")
    if problems([exact_codex], "codex"):
        failures.append("exact selected-series Codex development evaluator was rejected")

    rejected_actions = [
        _access_action("read", "<WORKSPACE>/examples/fresh-agent-evals/research/fixture.json"),
        _access_action("read", "<WORKSPACE>/examples/fresh-agent-evals/research/research_grader.py"),
        _access_action("read", "<WORKSPACE>/examples/fresh-agent-evals/research/research_selfcheck.py"),
        _access_action("read", "<WORKSPACE>/examples/fresh-agent-evals/research/runner.py"),
        _access_action("write", "trial-output/unexpected.txt"),
        _access_action("write", ".tmp/completion-context/other.md"),
        _access_action("bash", "sha256sum .tmp/completion-context/notes.md"),
        _access_action("bash", "cat examples/fresh-agent-evals/research/fixture.json"),
        _access_action("bash", "cat examples/fresh-agent-evals/research/fixture'.json'"),
        _access_action("bash", r"cat examples/fresh-agent-evals/research/fixtu\re.json"),
        _access_action("bash", "cat examples/fresh-agent-evals/research/research_grader.py"),
        _access_action("bash", "cat examples/fresh-agent-evals/research/research_selfcheck.py"),
        _access_action("bash", "python examples/fresh-agent-evals/research/research_grader.py"),
        _access_action("bash", "python examples/fresh-agent-evals/research/research_selfcheck.py"),
        _access_action("bash", "python examples/fresh-agent-evals/research/runner.py --selfcheck"),
        _access_action(
            "bash",
            "python3.12 -c \"open('examples/fresh-agent-evals/research/fixture.json').read()\"",
        ),
        _access_action(
            "bash",
            'sh -c "cat examples/fresh-agent-evals/research/fixture.json"',
        ),
        _access_action("bash", "node inspect.js"),
    ]
    for action in rejected_actions:
        if not problems([action]):
            failures.append(f"reserved or unsupported subject access was accepted: {action['command']}")
            break
    if not any("unknown" in item for item in problems([rejected_actions[-1]])):
        failures.append("unsupported shell executable did not leave the access audit unknown")

    missing_trace = _research_action_problems(
        [_access_action("read", "examples/fresh-agent-evals/research/task.md")],
        "opencode",
        workspace,
        trusted_root,
        series_manifest_path=series_path,
    )
    if not any("unknown" in item for item in missing_trace):
        failures.append("missing normalized access trace did not remain explicitly unknown")

    incomplete_trace = _complete_access_trace([], "opencode")
    incomplete_trace["trace_complete"] = False
    incomplete = problems([], trace=incomplete_trace)
    if not any("unknown" in item for item in incomplete):
        failures.append("incomplete native access trace did not remain explicitly unknown")

    truncated = _access_action("bash", "cat examples/fresh-agent-evals/research/task.md")
    truncated["command_truncated"] = True
    truncated_problems = problems([truncated])
    if not any("unknown" in item for item in truncated_problems):
        failures.append("truncated native shell command did not remain explicitly unknown")

    unknown_trace = route_trace([shell_route_event(
        "diagnostic-unknown-shell", "cat examples/fresh-agent-evals/research/task.md",
    )])
    unknown_action = unknown_trace["actions"][0]
    unknown_action["command_truncated"] = True
    unknown_causes: dict[str, object] = {}
    unknown_problems = problems(
        [unknown_action], trace=unknown_trace, causal_actions=unknown_causes,
    )
    protected_diagnostics = compose_research_diagnostics(
        protected_problems, [protected_read_action], "opencode", None,
        causal_actions=protected_causes, repo=trusted_root, workspace=workspace,
    )
    unknown_diagnostics = compose_research_diagnostics(
        unknown_problems, [unknown_action], "opencode", None,
        causal_actions=unknown_causes, repo=trusted_root, workspace=workspace,
    )
    protected_diagnostic = next(
        (item for item in protected_diagnostics if "reserved fixture or protected grader source access was observed" in item.get("summary", "")),
        None,
    )
    unknown_diagnostic = next(
        (item for item in unknown_diagnostics if item.get("category") == "unsupported_evidence_format"),
        None,
    )
    if (
        protected_diagnostic is None
        or unknown_diagnostic is None
        or protected_diagnostic.get("native_action", {}).get("action_index") != 0
        or protected_diagnostic.get("native_action", {}).get("tool") != "read"
        or protected_diagnostic.get("native_action", {}).get("command")
        != "examples/fresh-agent-evals/research/research_grader.py"
        or unknown_diagnostic.get("native_action", {}).get("action_index") != 0
        or unknown_diagnostic.get("native_action", {}).get("tool") != "bash"
        or not isinstance(unknown_diagnostic.get("native_action", {}).get("command"), str)
        or "Stop accessing reserved evaluator material" not in protected_diagnostic.get("next_step", "")
        or "Keep the access result unknown" not in unknown_diagnostic.get("next_step", "")
        or "Stop accessing reserved" in unknown_diagnostic.get("next_step", "")
    ):
        failures.append("protected research access and unknown evidence did not produce distinct, action-indexed diagnostics")


def _check_completion_finish_evidence(failures: list[str], workspace: Path, trusted_root: Path) -> None:
    contract = load_fixture_contract(trusted_root, "research", CASE_ID)
    commands = {
        item["check_id"]: item["command"]
        for item in render_finish_commands(contract, "research", MANIFEST_REL)
    }
    writes = {CODE_REL: [0], REPORT_REL: [0], RESULT_REL: [0]}
    prompt = render_research_prompt(
        trusted_root, workspace, MANIFEST_REL, "selfcheck-series", "0" * 64,
    )
    task_text = (HERE / "task.md").read_text(encoding="utf-8")
    if (
        "through observed EOF" not in prompt
        or "exact offset until EOF" not in prompt
        or "repair loop" not in prompt
        or "successful checker the final check" not in prompt
        or "through observed EOF" not in task_text
        or "repair loop" not in task_text
    ):
        failures.append("research prompt/task do not explain complete root reads and the final successful-check repair loop")

    def finish_result(
        harness: str,
        timeline: list[dict],
        *,
        write_sequences: dict | None = None,
    ) -> tuple[dict, list[str], list[dict], dict]:
        events = (
            [{"type": "session", "sessionID": "completion-check-selfcheck", "timestamp": 100.0}]
            if harness == "opencode"
            else [{"type": "thread.started", "thread_id": "completion-check-selfcheck"}]
        )
        for index, operation in enumerate(timeline):
            command = (
                commands["research-git-diff-check"] if operation["op"] == "diff"
                else commands["research-completion-check"] if operation["op"] == "checker"
                else operation["command"]
            )
            identity = operation.get("id", f"research-finish-{harness}-{index}")
            exit_code = operation.get("exit_code")
            status = operation.get("status", "completed")
            stamp = 101.0 + index
            if harness == "opencode":
                events.append(_shell_event(
                    identity, command, stamp, exit_code=exit_code, status=status,
                ))
            else:
                item = {
                    "id": identity,
                    "type": "command_execution",
                    "command": f"/bin/bash -lc {shlex.quote(command)}",
                    "status": status,
                }
                if type(exit_code) is int:
                    item["exit_code"] = exit_code
                events.append({
                    "type": "item.failed" if status == "failed" else "item.completed",
                    "item": item,
                })
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in events)
        trace = (
            normalize_opencode(raw, workspace, trusted_root)
            if harness == "opencode" else normalize_codex(raw, workspace, trusted_root)
        )
        actions = trace["actions"]
        selected_writes = write_sequences if isinstance(write_sequences, dict) else writes
        evidence, problems = _final_completion_check_evidence(
            actions, harness, workspace, MANIFEST_REL, selected_writes,
        )
        return evidence, problems, actions, trace

    one_pass = [
        {"op": "diff", "id": "finish-one-pass-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-one-pass-check", "exit_code": 0},
    ]
    passed_trace: dict | None = None
    passed_actions: list[dict] = []
    for harness in ("opencode", "codex"):
        passed, passed_problems, actions, trace = finish_result(harness, one_pass)
        if passed.get("status") != "passed" or passed_problems:
            failures.append(f"exact successful one-pass research completion check failed for {harness}: " + "; ".join(passed_problems))
        literal_search, literal_search_problems, _literal_search_actions, _ = finish_result(harness, one_pass + [
            {
                "op": "inspection",
                "command": "rg -- 'scripts/start_here.py --check-completion' README.md",
                "exit_code": 0,
            },
        ])
        if (
            literal_search.get("final_check") is not True
            or literal_search_problems
            or len(literal_search.get("completion_attempt_history", [])) != 1
        ):
            failures.append(f"literal checker text in a bounded research search was misidentified as another check for {harness}")
        if harness == "opencode":
            passed_trace, passed_actions = trace, actions
    if passed_trace is None:
        passed_trace = {"trace_complete": True, "unclassified_tool_count": 0}
    passed_access_problems = _research_action_problems(
        passed_actions, "opencode", workspace, trusted_root,
        series_manifest_path=MANIFEST_REL,
        trace=passed_trace,
    )
    if passed_access_problems:
        failures.append("the bounded native one-pass finish-check commands were rejected by the research access audit")

    retry_timeline = [
        {"op": "diff", "id": "finish-first-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-first-failed-check", "exit_code": 1},
        {"op": "diff", "id": "finish-repair-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-final-passing-check", "exit_code": 0},
    ]
    successful_recheck_timeline = [
        {"op": "diff", "id": "finish-success-before-repair-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-success-before-repair-check", "exit_code": 0},
        {"op": "diff", "id": "finish-success-after-repair-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-success-after-repair-check", "exit_code": 0},
    ]
    failed_diff_recheck_timeline = [
        {"op": "diff", "id": "finish-failed-first-diff", "exit_code": 1},
        {"op": "diff", "id": "finish-passing-retry-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-check-after-retried-diff", "exit_code": 0},
    ]
    failed_timeline = [
        {"op": "diff", "id": "finish-failed-diff-a", "exit_code": 0},
        {"op": "checker", "id": "finish-earlier-pass", "exit_code": 0},
        {"op": "diff", "id": "finish-failed-diff-b", "exit_code": 0},
        {"op": "checker", "id": "finish-terminal-fail", "exit_code": 1},
    ]
    unknown_timeline = [
        {"op": "diff", "id": "finish-unknown-diff-a", "exit_code": 0},
        {"op": "checker", "id": "finish-earlier-known-pass", "exit_code": 0},
        {"op": "diff", "id": "finish-unknown-diff-b", "exit_code": 0},
        {"op": "checker", "id": "finish-terminal-unknown", "exit_code": None, "status": "completed"},
    ]
    malformed_timeline = [
        {"op": "diff", "id": "finish-malformed-diff", "exit_code": 0},
        {"op": "checker", "id": "finish-before-malformed-check", "exit_code": 0},
        {
            "op": "malformed",
            "id": "finish-malformed-terminal-check",
            "command": commands["research-completion-check"] + "; true",
            "exit_code": 0,
        },
    ]
    late_diff_timeline = [
        {"op": "diff", "id": "finish-late-diff-before", "exit_code": 0},
        {"op": "checker", "id": "finish-late-diff-check", "exit_code": 0},
        {"op": "diff", "id": "finish-late-diff-after", "exit_code": 0},
    ]

    for harness in ("opencode", "codex"):
        retry, retry_problems, _retry_actions, _ = finish_result(harness, retry_timeline)
        if (
            retry.get("status") != "passed"
            or retry.get("final_check") is not True
            or retry_problems
            or len(retry.get("completion_attempt_history", [])) != 2
            or len(retry.get("diff_attempt_history", [])) != 2
            or retry.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
        ):
            failures.append(f"multiple distinct research check attempts did not select the terminal pass for {harness}: {retry_problems}")

        successful_recheck, successful_recheck_problems, _successful_recheck_actions, _ = finish_result(
            harness, successful_recheck_timeline,
        )
        failed_diff_recheck, failed_diff_recheck_problems, _failed_diff_actions, _ = finish_result(
            harness, failed_diff_recheck_timeline,
        )
        if (
            successful_recheck.get("final_check") is not True
            or successful_recheck_problems
            or len(successful_recheck.get("completion_attempt_history", [])) != 2
            or successful_recheck.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
        ):
            failures.append(f"earlier successful research check was not superseded by a recheck for {harness}")
        if (
            failed_diff_recheck.get("final_check") is not True
            or failed_diff_recheck_problems
            or len(failed_diff_recheck.get("diff_attempt_history", [])) != 2
            or failed_diff_recheck.get("diff_attempt_history", [{}])[0].get("outcome_status") != "failed"
            or failed_diff_recheck.get("diff_attempt_history", [{}, {}])[-1].get("selection") != "terminal"
        ):
            failures.append(f"failed research diff attempt was not retained and superseded by a successful recheck for {harness}")

        failed, failed_problems, _failed_actions, _ = finish_result(harness, failed_timeline)
        if (
            failed.get("status") != "failed"
            or failed.get("completion_exit_code") != 1
            or failed.get("completion_checker_status") != "failed"
            or failed.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
            or not failed_problems
        ):
            failures.append(f"terminal failed research checker fell back to an earlier pass for {harness}")

        unknown, unknown_problems, _unknown_actions, _ = finish_result(harness, unknown_timeline)
        if (
            unknown.get("status") != "unverified"
            or unknown.get("completion_checker_status") != "unverified"
            or unknown.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
            or not unknown_problems
        ):
            failures.append(f"terminal unknown research checker fell back to an earlier pass for {harness}")

        malformed, malformed_problems, _malformed_actions, _ = finish_result(harness, malformed_timeline)
        if (
            malformed.get("final_check") is not False
            or malformed.get("completion_attempt_history", [{}])[-1].get("canonical_command") is not False
            or not malformed_problems
        ):
            failures.append(f"malformed terminal research checker did not invalidate earlier success for {harness}")

        late_write_values = {CODE_REL: [0, 20], REPORT_REL: [0], RESULT_REL: [0]}
        late_write, late_write_problems, _late_write_actions, _ = finish_result(
            harness, one_pass, write_sequences=late_write_values,
        )
        if late_write.get("final_check") is not False or not late_write_problems:
            failures.append(f"late research artifact write did not invalidate completion for {harness}")

        invalid_order = {CODE_REL: [9], REPORT_REL: [9], RESULT_REL: [9]}
        order_result, order_problems, _order_actions, _ = finish_result(
            harness, one_pass, write_sequences=invalid_order,
        )
        if order_result.get("final_check") is not False or not order_problems:
            failures.append(f"research checks before final artifact writes were accepted for {harness}")

        concurrent, concurrent_problems, concurrent_actions, _ = finish_result(harness, [
            {"op": "diff", "id": "finish-concurrent-diff", "exit_code": 0},
            {"op": "checker", "id": "finish-concurrent-a", "exit_code": 0},
            {"op": "checker", "id": "finish-concurrent-b", "exit_code": 0},
        ])
        concurrent_checks = [
            action for action in concurrent_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(commands["research-completion-check"], harness)
        ]
        if len(concurrent_checks) == 2:
            concurrent_checks[0]["event_sequence"] = 10
            concurrent_checks[0]["completion_sequence"] = 20
            concurrent_checks[1]["event_sequence"] = 15
            concurrent_checks[1]["completion_sequence"] = 25
            concurrent, concurrent_problems = _final_completion_check_evidence(
                concurrent_actions, harness, workspace, MANIFEST_REL, writes,
            )
        if concurrent.get("final_check") is not False or not concurrent_problems:
            failures.append(f"overlapping research checker lifecycles were accepted for {harness}")

        overlapping, _overlap_problems, overlapping_actions, _ = finish_result(harness, one_pass)
        overlap_diff = next(
            action for action in overlapping_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(commands["research-git-diff-check"], harness)
        )
        overlap_checker = next(
            action for action in overlapping_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(commands["research-completion-check"], harness)
        )
        overlap_diff["event_sequence"] = 10
        overlap_diff["completion_sequence"] = 20
        overlap_checker["event_sequence"] = 15
        overlap_checker["completion_sequence"] = 25
        overlapping, overlap_problems = _final_completion_check_evidence(
            overlapping_actions, harness, workspace, MANIFEST_REL, writes,
        )
        if overlapping.get("final_check") is not False or not overlap_problems:
            failures.append(f"concurrent research diff completion and checker begin was accepted for {harness}")

        duplicate, duplicate_problems, duplicate_actions, _ = finish_result(harness, one_pass)
        terminal_checker = next(
            action for action in duplicate_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(commands["research-completion-check"], harness)
        )
        duplicate_action = dict(terminal_checker)
        duplicate_action["event_sequence"] = terminal_checker["event_sequence"] + 2
        duplicate_action["completion_sequence"] = terminal_checker["completion_sequence"] + 2
        duplicate_actions.append(duplicate_action)
        duplicate, duplicate_problems = _final_completion_check_evidence(
            duplicate_actions, harness, workspace, MANIFEST_REL, writes,
        )
        if duplicate.get("final_check") is not False or not duplicate_problems:
            failures.append(f"duplicated research checker lifecycle identity was accepted for {harness}")

        late_check, late_check_problems, _late_check_actions, _ = finish_result(harness, late_diff_timeline)
        if late_check.get("final_check") is not False or not late_check_problems:
            failures.append(f"successful research checker followed by an invalidating check was accepted for {harness}")


def run_selfcheck(root: Path = ROOT) -> int:
    root = root.resolve()
    failures: list[str] = []
    check_names: list[str] = []
    protected_before = _protected_evaluator_hashes(root)

    completion_failures = completion_selfcheck_problems(root)
    failures.extend(f"completion contract: {item}" for item in completion_failures)
    check_names.append("canonical-completion-contract-artifact-path-readonly-regressions")

    _check_research_workspace_public_selection(root, failures)
    check_names.append("research-workspace-tracked-ignored-public-selection")

    for relative in (
        "research-result.schema.json", "comparison-report.schema.json", "fixture.schema.json",
        "series.schema.json", "trial-record.schema.json", "verdict.schema.json",
    ):
        try:
            json.loads((HERE / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failures.append(f"typed schema JSON is invalid: {relative}")
    check_names.append("typed-schemas")

    fixture, _fixture_bytes = load_fixture(root)
    alpha = fixture["inputs"][0]
    context1 = FixtureContext(alpha, alpha["paired_seeds"][0])
    context2 = FixtureContext(alpha, alpha["paired_seeds"][0])
    pair = context1.eligible_pairs[0]
    first = list(context1.stream_for(pair))
    second = list(context2.stream_for(pair))
    if first != second:
        failures.append("fixture context streams are not deterministic for a paired seed")
    if not any(item["role"] == "reserved-heldout" for item in fixture["inputs"]):
        failures.append("reserved held-out fixture role is missing")
    check_names.append("fixture-contract-and-heldout")

    _check_accounting(failures, root)
    check_names.append("synthetic-ledger-accounting")

    try:
        _local_callback_source(HERE / "callback_worker.py")
        failures.append("callback worker source was incorrectly accepted as a subject contribution")
    except (ValueError, SyntaxError):
        pass
    with tempfile.TemporaryDirectory(prefix="research-selfcheck-code-") as temp:
        subject = Path(temp) / "challenger.py"
        subject.write_text("# plan only; no executable callback\n", encoding="utf-8")
        try:
            _local_callback_source(subject)
        except (ValueError, SyntaxError):
            pass
        else:
            failures.append("plan-only contribution passed callback source validation")
        subject.write_text(
            "from coverage_challenger import _make_challenger_template\n"
            "def challenger(ctx):\n    yield from _make_challenger_template()(ctx)\n",
            encoding="utf-8",
        )
        try:
            _local_callback_source(subject)
        except (ValueError, SyntaxError):
            pass
        else:
            failures.append("bundled campaign example import was accepted as an authored implementation")
    check_names.append("authored-callback-and-plan-negatives")

    scratch_parent = root / ".tmp"
    mkdir_chain_no_symlinks(scratch_parent)
    with tempfile.TemporaryDirectory(prefix="research-eval-selfcheck-", dir=scratch_parent) as temp:
        scratch = Path(temp)
        try:
            rubric, workspace, trial, expected_dev_report = _make_positive_fixture(root, scratch)
            manifest, manifest_digest = verify_series(rubric, MANIFEST_REL)
            if manifest_digest != trial["series_manifest_sha256"]:
                failures.append("series digest changed during the selfcheck fixture build")
            verdict = grade_contribution(
                rubric, workspace, trial, MANIFEST_REL,
                evidence_scope="synthetic-selfcheck",
            )
            if verdict.get("success") is not True or verdict.get("evidence_scope") != "synthetic-selfcheck":
                failures.append(f"valid synthetic contribution did not pass independent grading: {verdict.get('failure_reasons')}")
            independent_report = verdict.get("independent_report")
            if not isinstance(independent_report, dict) or independent_report.get("role_scope") != "development_plus_reserved_heldout":
                failures.append("independent report did not recompute development and reserved held-out inputs")
            if isinstance(independent_report, dict):
                if independent_report.get("run_count") != 6:
                    failures.append("independent grader did not execute multiple input/seed pairs")
                if independent_report.get("comparison_status") != "incomplete_comparison":
                    failures.append("a deliberately short regression callback did not retain its honest incomplete status")
                if independent_report.get("separate_genetic_campaign_target", {}).get("observed_completed_simulations") is not None:
                    failures.append("synthetic selfcheck invented an observed genetic baseline count")
            comparison_diagnostics = [
                item for item in verdict.get("diagnostics", [])
                if isinstance(item, dict) and item.get("category") == "research_comparison_outcome"
            ]
            if (
                verdict.get("success") is not True
                or not comparison_diagnostics
                or comparison_diagnostics[0].get("severity") != "info"
                or any(item.get("category") == "protocol_violation" for item in verdict.get("diagnostics", []))
            ):
                failures.append("honest incomplete/negative synthetic research was mislabeled as a protocol failure")
            check_names.append("positive-independent-grade-and-heldout")

            mutations = []
            hardcoded = json.loads(json.dumps(expected_dev_report))
            hardcoded["runs"][0]["challenger"]["completed_evaluator_calls"] = 999
            hardcoded["runs"][0]["challenger"]["target_reached"] = True
            mutations.append(("forged-count", hardcoded))
            invalid_cell = json.loads(json.dumps(expected_dev_report))
            invalid_cell["runs"][0]["challenger"]["unique_valid_cells_reached"].append("forged_rotation|forged_feature")
            invalid_cell["runs"][0]["challenger"]["unique_valid_cell_count"] += 1
            mutations.append(("invalid-cell-claim", invalid_cell))
            depth_forgery = json.loads(json.dumps(expected_dev_report))
            depth_forgery["runs"][0]["challenger"]["rejections"]["depth_over_six"] = 0
            depth_forgery["runs"][0]["challenger"]["completed_evaluator_calls"] += 1
            mutations.append(("depth-over-six-forgery", depth_forgery))
            stale = json.loads(json.dumps(expected_dev_report))
            stale["implementation_sha256"] = "0" * 64
            mutations.append(("stale-report", stale))
            if any(canonical_json_equal(candidate, expected_dev_report) for _name, candidate in mutations):
                failures.append("a hardcoded count, invalid cell/depth, or stale report matched the independent report")
            check_names.extend(name for name, _candidate in mutations)

            # One full-grade tamper case updates its self-reported hash to prove
            # that integrity hashes alone cannot make a forged report pass.
            tampered = scratch / "workspace-tampered-report"
            shutil.copytree(workspace, tampered)
            tampered_report = json.loads((tampered / REPORT_REL).read_text(encoding="utf-8"))
            tampered_report["runs"][0]["challenger"]["completed_evaluator_calls"] = 999
            (tampered / REPORT_REL).write_bytes(_json_bytes(tampered_report))
            result_data = json.loads((tampered / RESULT_REL).read_text(encoding="utf-8"))
            result_data["machine_claims"]["comparison_report_sha256"] = _digest((tampered / REPORT_REL).read_bytes())
            (tampered / RESULT_REL).write_bytes(_json_bytes(result_data))
            failed_verdict = grade_contribution(
                rubric, tampered, _trial_with_current_artifacts(tampered, trial), MANIFEST_REL,
                evidence_scope="synthetic-selfcheck",
            )
            if failed_verdict.get("success") is not False or not any(
                "not independently reproducible" in item for item in failed_verdict.get("failure_reasons", [])
            ):
                failures.append("tampered report passed after its claimed SHA-256 was updated")
            check_names.append("tampered-report-fails-after-rehash")

            missing = scratch / "workspace-missing-code"
            shutil.copytree(workspace, missing)
            (missing / CODE_REL).unlink()
            missing_verdict = grade_contribution(
                rubric, missing, _trial_with_current_artifacts(missing, trial), MANIFEST_REL,
                evidence_scope="synthetic-selfcheck",
            )
            if missing_verdict.get("success") is not False:
                failures.append("missing callback implementation passed the contribution grader")
            check_names.append("missing-code-fails")

            changed_validator = scratch / "workspace-changed-validator"
            shutil.copytree(workspace, changed_validator)
            validator = changed_validator / ".github/scripts/validation/evals.py"
            validator.write_bytes(validator.read_bytes() + b"\n# selfcheck drift\n")
            changed_verdict = grade_contribution(
                rubric, changed_validator, _trial_with_current_artifacts(changed_validator, trial), MANIFEST_REL,
                evidence_scope="synthetic-selfcheck",
            )
            if changed_verdict.get("success") is not False:
                failures.append("changed validator source passed the frozen-series grader")
            check_names.append("changed-validator-fails")

            # A named series is immutable: a second create and any frozen-file
            # change are refused, while the temporary series itself is not kept.
            try:
                write_series_once(rubric, MANIFEST_REL, manifest)
            except SeriesError:
                pass
            else:
                failures.append("series freezer overwrote an existing manifest")
            drift_targets = {
                "examples/fresh-agent-evals/research/callback-contract.md",
                ".github/scripts/smoke_first_contribution.py",
                *{
                    relative for relative in frozen_paths(rubric)
                    if Path(relative).name == ".gitignore"
                },
            }
            for relative in sorted(drift_targets):
                drift = rubric / relative
                original_drift = drift.read_bytes()
                try:
                    drift.write_bytes(original_drift + b"\nfixture drift\n")
                    try:
                        verify_series(rubric, MANIFEST_REL)
                    except SeriesError:
                        pass
                    else:
                        failures.append(f"research-series verifier accepted frozen helper drift: {relative}")
                        break
                finally:
                    drift.write_bytes(original_drift)
            check_names.append("series-no-overwrite-and-transitive-helper-ignore-drift")

            _check_native_linkage(failures, workspace, rubric)
            check_names.extend([
                "native-command-outcome-linkage",
                "native-report-write-attribution-around-read-hashes",
                "native-post-evaluation-report-file-mutation-rejected",
                "native-evaluator-unique-completed-and-phased",
                "native-absent-truncated-and-mutating-evidence-fails-closed",
            ])
            _check_codex_command_evidence(failures, workspace, rubric)
            check_names.append("codex-full-bounded-command-evidence-and-access-audit")
            _check_research_access_audit(failures, workspace, rubric)
            check_names.append("research-subject-access-audit")
            _check_completion_finish_evidence(failures, workspace, rubric)
            check_names.append("research-final-completion-command-order-outcome")
            current_closure = set(frozen_paths(root))
            required_helpers = {
                ".gitignore",
                ".github/scripts/smoke_first_contribution.py",
                "examples/fresh-agent-evals/research/research_access.py",
                "examples/fresh-agent-evals/v2/native_arguments.py",
                "examples/fresh-agent-evals/v2/diagnostics.py",
                "examples/fresh-agent-evals/v2/diagnostic.schema.json",
                "scripts/onboarding/completion.py",
            }
            applicable_ignore_rules = set()
            for relative in current_closure:
                parent = Path(relative).parent
                while True:
                    ignore_relative = (parent / ".gitignore").as_posix()
                    if (root / ignore_relative).is_file():
                        applicable_ignore_rules.add(ignore_relative)
                    if parent == Path("."):
                        break
                    parent = parent.parent
            research_contract = load_fixture_contract(root, "research", CASE_ID)
            missing_closure = (
                required_helpers | applicable_ignore_rules | set(research_contract["available_input_paths"])
            ) - current_closure
            if missing_closure:
                failures.append(
                    "research freeze omitted admitted static read/diagnostic inputs: "
                    + ", ".join(sorted(missing_closure))
                )

            nested_ignore_rubric = scratch / "rubric-nested-public-ignore"
            shutil.copytree(rubric, nested_ignore_rubric)
            nested_rule_rel = "docs/depositors/.gitignore"
            nested_rule_path = nested_ignore_rubric / nested_rule_rel
            nested_rule_path.parent.mkdir(parents=True, exist_ok=True)
            nested_rule_path.write_text("faq.md\n", encoding="utf-8")
            (nested_rule_path.parent / "faq.md").write_text("# ignored public page\n", encoding="utf-8")
            cache_rule = nested_ignore_rubric / ".ruff_cache/.gitignore"
            cache_rule.parent.mkdir(parents=True, exist_ok=True)
            cache_rule.write_text("*\n", encoding="utf-8")
            nested_series_rel = "examples/fresh-agent-evals/research/series/selfcheck-public-ignore-c.json"
            nested_manifest = build_series(nested_ignore_rubric, "selfcheck-public-ignore-c")
            nested_files = nested_manifest.get("frozen_files", {})
            if nested_rule_rel not in nested_files or ".ruff_cache/.gitignore" in nested_files:
                failures.append("research series did not inherit public nested ignore rules while excluding cache rules")
            write_series_once(nested_ignore_rubric, nested_series_rel, nested_manifest)
            nested_rule_original = nested_rule_path.read_bytes()
            try:
                nested_rule_path.write_bytes(nested_rule_original + b"\nmutated public selection\n")
                try:
                    verify_series(nested_ignore_rubric, nested_series_rel)
                except SeriesError:
                    pass
                else:
                    failures.append("research series accepted mutation of a nested public ignore rule")
            finally:
                nested_rule_path.write_bytes(nested_rule_original)
            check_names.append("research-nested-public-ignore-freeze-and-drift")

            dynamic_paths = set(research_contract["deliverables"]) | set(research_contract["scratch_paths"]) | {MANIFEST_REL}
            if dynamic_paths & current_closure:
                failures.append("dynamic research manifest/output/scratch paths were incorrectly treated as frozen route inputs")
            if Path(MANIFEST_REL).parts[:-1] != Path(series_directory("research")).parts:
                failures.append("selected research manifest selfcheck path is outside the dynamic series directory")
            check_names.append("research-access-helper-future-series-closure")
        except Exception as exc:  # noqa: BLE001 - report a short offline regression failure
            failures.append(f"selfcheck setup/execution failed: {type(exc).__name__}")

    if _protected_evaluator_hashes(root) != protected_before:
        failures.append("a protected evaluator source changed during offline synthetic selfchecks")
    check_names.append("protected-evaluator-source-bytes-preserved")

    result = {
        "suite": "paper-backed-coverage-research-v1",
        "status": "pass" if not failures else "fail",
        "synthetic_selfcheck": True,
        "native_calls": 0,
        "model_calls": 0,
        "checks": check_names,
        "failures": failures,
        "note": "Regression-only synthetic callbacks and reports are not native trials, API evidence, or performance measurements.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run_selfcheck())
