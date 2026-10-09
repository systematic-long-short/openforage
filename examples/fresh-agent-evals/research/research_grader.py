"""Independent artifact, execution, and native-evidence grader."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS_ROOT = HERE.parent
REPO_ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
if str(EVALS_ROOT) not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT))
if str(EVALS_ROOT / "v2") not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT / "v2"))

from v2.commands import (  # noqa: E402
    command_semantic_key,
)
from v2.diagnostics import compose_research_diagnostics  # noqa: E402
from v2.evidence import (  # noqa: E402
    action_sequence,
    command_sha256_matches,
    entry_route_evidence,
    explicit_exit_code,
    final_completion_sequence_evidence,
    result_sequence,
)
from v2.grader import (  # noqa: E402
    _forbidden_action_problems,
    _outside_workspace_problems,
    _path_write_sequences,
)
from v2.native_events import normalize_codex, normalize_opencode  # noqa: E402
from v2.paths import PathSafetyError, safe_relative  # noqa: E402
from v2.schema import loads_strict  # noqa: E402
from v2.snapshot import digest_map, file_map  # noqa: E402
from onboarding.completion import (  # noqa: E402
    fixture_deliverable,
    load_fixture_contract,
    render_finish_commands,
)

from research_access import research_action_access_problems  # noqa: E402
from series import SeriesError, verify_series  # noqa: E402
from synthetic_evaluator import (  # noqa: E402
    EvaluationError,
    canonical_json_equal,
    evaluate_suite,
    load_fixture,
)
from typed_schemas import validate_research_result, validate_trial_record  # noqa: E402

CASE_ID = "paper-backed-coverage-challenger"
RESEARCH_CONTRACT = load_fixture_contract(REPO_ROOT, "research", CASE_ID)
CODE_REL = fixture_deliverable(REPO_ROOT, "research", CASE_ID, "challenger.py")
REPORT_REL = fixture_deliverable(REPO_ROOT, "research", CASE_ID, "comparison-report.json")
RESULT_REL = fixture_deliverable(REPO_ROOT, "research", CASE_ID, "research-result.json")
SOURCE_PROVENANCE_REL = next(
    path for path in RESEARCH_CONTRACT["available_input_paths"]
    if Path(path).name == "source-provenance.json"
)
SOURCE_PROVENANCE_FIELDS = {"schema_version", "source_excerpt_path", "title", "authors", "year", "identifier", "url", "maintainer_paraphrase"}
ALLOWED_CHANGED = set(RESEARCH_CONTRACT["deliverables"])
RAW_BASE = (".tmp", "fresh-agent-research", "runs")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class GradeError(RuntimeError):
    """The contribution, selected series, or native evidence failed closed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_json(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(
        right, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _safe_root_file(root: Path, relative: str) -> Path:
    try:
        safe = safe_relative(relative)
    except PathSafetyError as exc:
        raise GradeError("artifact path is not safe and workspace-relative") from exc
    path = root
    for part in safe.parts:
        path = path / part
        if path.is_symlink():
            raise GradeError("artifact path traverses a symlink")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise GradeError("artifact is missing or escapes its workspace")
    return path


def _scan_no_symlinks(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise GradeError("trial workspace must be a real directory")
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *filenames):
            candidate = current_path / name
            if candidate.is_symlink():
                raise GradeError("trial workspace contains a symlink")
            if not candidate.resolve().is_relative_to(root.resolve()):
                raise GradeError("trial workspace path escapes its root")


def _load_json(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    try:
        payload = loads_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GradeError("structured artifact is invalid JSON or has duplicate keys") from exc
    if not isinstance(payload, dict):
        raise GradeError("structured artifact root must be a JSON object")
    return payload, raw


def _source_provenance(root: Path) -> tuple[dict, str]:
    path = _safe_root_file(root, SOURCE_PROVENANCE_REL)
    data, raw = _load_json(path)
    if set(data) != SOURCE_PROVENANCE_FIELDS or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise GradeError("frozen source provenance has an unsupported typed shape")
    if (
        data.get("title") != "Illuminating search spaces by mapping elites"
        or data.get("authors") != ["Jean-Baptiste Mouret", "Jeff Clune"]
        or data.get("year") != 2015
        or data.get("identifier") != "arXiv:1504.04909"
        or data.get("url") != "https://arxiv.org/abs/1504.04909"
    ):
        raise GradeError("frozen source provenance no longer matches the attributed excerpt")
    return data, _sha256(raw)


def _changed_paths(initial: dict, current: dict) -> list[str]:
    return sorted(
        relative for relative in set(initial) | set(current)
        if initial.get(relative) != current.get(relative)
    )


def _read_raw_events(root: Path, trial: dict) -> bytes:
    relative = trial.get("raw_events_path")
    if not isinstance(relative, str):
        raise GradeError("native trial raw-event reference is missing")
    try:
        safe = safe_relative(relative)
    except PathSafetyError as exc:
        raise GradeError("raw-event path is unsafe") from exc
    if safe.parts[: len(RAW_BASE)] != RAW_BASE:
        raise GradeError("raw-event path is outside ignored research-run scratch")
    path = root / safe
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise GradeError("raw-event path is missing, unsafe, or a symlink")
    raw = path.read_bytes()
    if _sha256(raw) != trial.get("raw_events_sha256") or len(raw) != trial.get("raw_events_bytes"):
        raise GradeError("raw-event bytes or digest do not match the native trial record")
    return raw


def _verify_raw_stderr(root: Path, trial: dict) -> None:
    relative = trial.get("raw_stderr_path")
    if not isinstance(relative, str):
        raise GradeError("native trial raw-stderr reference is missing")
    try:
        safe = safe_relative(relative)
    except PathSafetyError as exc:
        raise GradeError("raw-stderr path is unsafe") from exc
    if safe.parts[: len(RAW_BASE)] != RAW_BASE:
        raise GradeError("raw-stderr path is outside ignored research-run scratch")
    path = root / safe
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise GradeError("raw-stderr path is missing, unsafe, or a symlink")
    raw = path.read_bytes()
    if _sha256(raw) != trial.get("raw_stderr_sha256") or len(raw) != trial.get("raw_stderr_bytes"):
        raise GradeError("raw-stderr bytes or digest do not match the native trial record")


def _recompute_native_actions(root: Path, workspace: Path, trial: dict) -> dict:
    raw = _read_raw_events(root, trial)
    _verify_raw_stderr(root, trial)
    harness = trial.get("harness")
    if harness == "opencode":
        normalized = normalize_opencode(raw, workspace, root)
    elif harness == "codex":
        normalized = normalize_codex(raw, workspace, root)
    else:
        raise GradeError("native evidence does not use an admitted harness")
    if not _same_json(normalized.get("actions"), trial.get("native_actions")):
        raise GradeError("normalized native actions do not match retained raw events")
    if normalized.get("session_id_sha256") != trial.get("native_session_id_sha256"):
        raise GradeError("native lifecycle session identity does not match retained events")
    # Keep incomplete traces available to the focused access audit so they are
    # reported as unknown instead of disappearing behind a generic grade error.
    return normalized


def _native_identity(action: dict, harness: str) -> str | None:
    if harness == "opencode":
        value = action.get("native_tool_call_id_sha256")
    else:
        value = action.get("native_item_id_sha256")
    return value if isinstance(value, str) and SHA_RE.fullmatch(value) else None


def _completed_native_status(action: dict, harness: str) -> bool:
    if harness == "opencode":
        outcome = action.get("outcome")
        return isinstance(outcome, dict) and outcome.get("native_tool_status") == "completed"
    return action.get("native_status") == "completed" and "item.completed" in action.get("lifecycle_events", [])


def _research_action_problems(
    actions: list,
    harness: str,
    workspace: Path,
    root: Path,
    *,
    trace: dict | None = None,
    series_manifest_path: str | None = None,
    causal_actions: dict[str, object] | None = None,
) -> list[str]:
    if not isinstance(actions, list):
        return ["native subject access audit is unknown: native action list is unsupported"]
    problems = _forbidden_action_problems(
        actions, harness, causal_actions=causal_actions,
    )
    problems.extend(_outside_workspace_problems(
        actions, harness, workspace, root, causal_actions=causal_actions,
    ))
    for action_index, action in enumerate(actions):
        if isinstance(action, dict) and action.get("tool") in {
            "question", "python", "skill", "webfetch", "websearch", "network", "external_directory",
        }:
            message = f"disallowed fixture tool observed: {action['tool']}"
            reason = f"{message} (action_index={action_index})" if causal_actions is not None else message
            problems.append(reason)
            if causal_actions is not None:
                causal_actions[reason] = action
    if not isinstance(series_manifest_path, str):
        problems.append("native subject access audit is unknown: selected series identity is missing")
    else:
        problems.extend(research_action_access_problems(
            actions,
            harness,
            workspace,
            series_manifest_path,
            _expected_eval_command(series_manifest_path),
            trace,
            causal_actions=causal_actions,
        ))
    return sorted(set(problems))


def _expected_eval_command(series_path: str) -> str:
    contract = load_fixture_contract(REPO_ROOT, "research", CASE_ID)
    return next(
        item["command"] for item in render_finish_commands(contract, "research", series_path)
        if item["check_id"] == "research-development-evaluation"
    )


def _research_report_write_sequences(
    actions: list, harness: str, workspace: Path, evaluator_completion_sequence: int,
) -> list[int]:
    """Attribute report writes only to the completed evaluator or native file mutations.

    Shell commands are audited separately: bounded reads/hashes and no-ops are
    not writes, while unsupported or mutating shell operations remain audit
    failures/unknown rather than being inferred away here.
    """
    file_mutations = [
        action for action in actions
        if isinstance(action, dict) and action.get("tool") in {"edit", "write", "apply_patch", "file_change"}
    ]
    mutation_sequences = _path_write_sequences(file_mutations, REPORT_REL, harness, workspace)
    return sorted({evaluator_completion_sequence, *mutation_sequences})


def _native_command_evidence(
    actions: list,
    harness: str,
    workspace: Path,
    series_path: str,
    *,
    causal_actions: dict[str, object] | None = None,
) -> tuple[dict, list[str]]:
    problems = []

    def problem(message: str, action: dict | None = None) -> None:
        problems.append(message)
        if isinstance(action, dict) and causal_actions is not None:
            causal_actions[message] = action

    declared_key = command_semantic_key(_expected_eval_command(series_path), harness)
    if declared_key is None:
        return {}, ["declared development-evaluation command is outside the frozen parser grammar"]
    matches = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        command = action.get("command")
        if not isinstance(command, str) or action.get("command_truncated") is not False:
            continue
        key = command_semantic_key(command, harness)
        if key == declared_key:
            matches.append(action)
    if len(matches) != 1:
        problem("one uniquely identified native development-evaluation command with explicit exit 0 is required")
        return {}, problems

    action = matches[0]
    shell_exit = explicit_exit_code(action, harness)
    if (
        not command_sha256_matches(action)
        or shell_exit != 0
        or not _completed_native_status(action, harness)
    ):
        if shell_exit in {126, 127}:
            problem(
                f"runtime unavailable: native development-evaluation shell launch exited {shell_exit}",
                action,
            )
        else:
            problem("one uniquely identified native development-evaluation command with explicit exit 0 is required", action)
        return {}, problems
    identity = _native_identity(action, harness)
    sequence = action_sequence(action)
    completed_sequence = result_sequence(action)
    if identity is None or type(sequence) is not int or type(completed_sequence) is not int or completed_sequence < sequence:
        problem("development-evaluation command is missing a unique native lifecycle identity", action)
        return {}, problems
    matching_identities = [item for item in actions if isinstance(item, dict) and _native_identity(item, harness) == identity]
    if len(matching_identities) != 1:
        problem("development-evaluation native lifecycle identity is not unique")
        return {}, problems

    native_file_actions = [
        action for action in actions
        if isinstance(action, dict) and action.get("tool") in {"edit", "write", "apply_patch", "file_change"}
    ]
    write_sequences = {
        CODE_REL: _path_write_sequences(native_file_actions, CODE_REL, harness, workspace),
        REPORT_REL: _research_report_write_sequences(actions, harness, workspace, completed_sequence),
        RESULT_REL: _path_write_sequences(native_file_actions, RESULT_REL, harness, workspace),
    }
    if not write_sequences[CODE_REL] or not write_sequences[REPORT_REL] or not write_sequences[RESULT_REL]:
        problem("native trace must show completed writes for the callback, report, and research artifact")
        return {}, problems
    if completed_sequence not in write_sequences[REPORT_REL]:
        problem("the exact development-evaluation command is not correlated to its report write", action)
    if max(write_sequences[CODE_REL]) >= completed_sequence:
        problem("development-evaluation must occur after the final callback implementation write", action)
    if min(write_sequences[RESULT_REL]) <= completed_sequence:
        problem("typed research artifact must be written after the completed development evaluation", action)
    if any(sequence > completed_sequence for sequence in write_sequences[REPORT_REL]):
        problem("comparison report was written again after the correlated evaluator command", action)
    return {
        "command_identity": "declared-development-evaluator-v1",
        "command_sha256": action.get("command_sha256"),
        "command_bytes": action.get("command_bytes"),
        "native_lifecycle_identity_sha256": identity,
        "event_sequence": sequence,
        "completion_sequence": completed_sequence,
        "explicit_native_exit_code": 0,
        "native_status": "completed",
        "outcome_basis": "B-v2 parsed command key, full digest, explicit exit, and unique lifecycle identity",
        "write_sequences": write_sequences,
    }, problems


def _final_completion_check_evidence(
    actions: list,
    harness: str,
    workspace: Path,
    series_path: str,
    write_sequences: dict | None,
) -> tuple[dict, list[str]]:
    """Select terminal native diff/check attempts after all deliverable writes."""
    contract = load_fixture_contract(REPO_ROOT, "research", CASE_ID)
    finish = {
        item["check_id"]: item["command"]
        for item in render_finish_commands(contract, "research", series_path)
    }
    checker_command = finish["research-completion-check"]
    diff_command = finish["research-git-diff-check"]
    evidence, problems = final_completion_sequence_evidence(
        actions,
        harness,
        workspace,
        checker_command=checker_command,
        diff_command=diff_command,
        artifact_write_sequences=write_sequences if isinstance(write_sequences, dict) else {},
        required_artifact_paths=(CODE_REL, REPORT_REL, RESULT_REL),
        scope="research",
    )
    return evidence, problems


def _expected_machine_claims(
    *, series_path: str, series_id: str, series_digest: str,
    implementation_digest: str, report_digest: str, source_excerpt_digest: str,
) -> dict:
    return {
        "evaluation_scope": "synthetic_fixture_only",
        "comparison_report_path": REPORT_REL,
        "comparison_report_sha256": report_digest,
        "implementation_path": CODE_REL,
        "implementation_sha256": implementation_digest,
        "series_id": series_id,
        "series_manifest_path": series_path,
        "series_manifest_sha256": series_digest,
        "reference_identity": "synthetic_uniform_pair_selector_v1",
        "synthetic_uniform_is_genetic": False,
        "synthetic_quality_scope": "fixture_only",
        "synthetic_comparison_interpretation": "descriptive_only",
        "live_performance": {"status": "unknown", "value": None},
        "out_of_sample_return_correlation": {"status": "unknown", "value": None},
        "server_acceptance": {"status": "unknown", "value": None},
        "genetic_reference_target": {
            "algorithm": "genetic",
            "target_completed_simulations": 100_000,
            "observed_completed_simulations": None,
            "status": "unmeasured_target",
        },
    }


def grade_contribution(
    root: Path,
    workspace: Path,
    trial: dict,
    series_manifest_path: str,
    *,
    evidence_scope: str = "fresh-native-trial",
) -> dict:
    """Verify frozen sources, run the callback independently, and grade evidence."""
    root = root.resolve()
    workspace = Path(os.path.abspath(workspace))
    failures: list[str] = []
    report: dict | None = None
    command_evidence: dict | None = None
    causal_actions: dict[str, object] = {}
    artifact_hashes: dict[str, str] = {}
    route_evidence: dict | None = None
    native_actions: list = []
    series_id = trial.get("series_id") if isinstance(trial, dict) else None
    series_digest = trial.get("series_manifest_sha256") if isinstance(trial, dict) else None
    if not isinstance(series_id, str):
        series_id = "unknown-series"
    if not isinstance(series_digest, str) or SHA_RE.fullmatch(series_digest) is None:
        series_digest = "0" * 64

    try:
        if evidence_scope not in {"fresh-native-trial", "synthetic-selfcheck"}:
            raise GradeError("unsupported evidence scope")
        if evidence_scope == "fresh-native-trial" and workspace.resolve().is_relative_to(root):
            raise GradeError("native subject workspace must be outside the source checkout")
        _scan_no_symlinks(workspace)

        manifest, verified_digest = verify_series(root, series_manifest_path)
        if manifest.get("series_id") != trial.get("series_id"):
            raise GradeError("trial series identity does not match the selected frozen manifest")
        if trial.get("series_manifest_path") != series_manifest_path:
            raise GradeError("trial series-manifest path does not match the explicitly selected manifest")
        if verified_digest != trial.get("series_manifest_sha256"):
            raise GradeError("trial series-manifest digest does not match the selected manifest")
        workspace_manifest, workspace_manifest_digest = verify_series(workspace, series_manifest_path)
        if workspace_manifest_digest != verified_digest or workspace_manifest != manifest:
            raise GradeError("prepared workspace series manifest differs from the trusted source manifest")

        if evidence_scope == "fresh-native-trial":
            trial_problems = validate_trial_record(trial)
            failures.extend(trial_problems)
            if trial_problems:
                raise GradeError("native trial record does not match its typed schema")
        elif not isinstance(trial, dict):
            raise GradeError("synthetic selfcheck identity record is malformed")
        if trial.get("case_id") != CASE_ID:
            failures.append("trial case id does not match the research contribution task")
        if evidence_scope == "fresh-native-trial":
            if trial.get("native_exit_code") != 0 or trial.get("native_timed_out") is not False or trial.get("process_group_remaining") is not False:
                failures.append("native subject did not finish successfully with its owned process group cleaned up")
            if trial.get("native_session_id_sha256") is None or trial.get("native_trace_complete") is not True:
                failures.append("native session identity or complete event trace is unavailable")

        initial = trial.get("initial_file_sha256")
        if not isinstance(initial, dict) or any(
            not isinstance(path, str) or not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None
            for path, digest in initial.items()
        ):
            raise GradeError("initial snapshot file map is malformed")
        if digest_map(initial) != trial.get("snapshot_sha256"):
            failures.append("initial file map does not reproduce the prepared snapshot digest")
        frozen = manifest["frozen_files"]
        for relative, digest in frozen.items():
            if initial.get(relative) != digest:
                failures.append(f"prepared snapshot does not match frozen source: {relative}")
        current = file_map(workspace)
        changed = _changed_paths(initial, current)
        disallowed = sorted(set(changed) - ALLOWED_CHANGED)
        if disallowed:
            failures.append("subject changed files outside the three declared deliverables")
        for relative in changed:
            if relative in ALLOWED_CHANGED and relative not in current:
                failures.append(f"required deliverable was deleted: {relative}")
        for relative, digest in frozen.items():
            if current.get(relative) != digest:
                failures.append(f"frozen rubric, fixture, or validator changed in the subject workspace: {relative}")

        code_path = _safe_root_file(workspace, CODE_REL)
        report_path = _safe_root_file(workspace, REPORT_REL)
        result_path = _safe_root_file(workspace, RESULT_REL)
        code_bytes = code_path.read_bytes()
        report_data, report_bytes = _load_json(report_path)
        result_data, result_bytes = _load_json(result_path)
        artifact_hashes = {
            CODE_REL: _sha256(code_bytes),
            REPORT_REL: _sha256(report_bytes),
            RESULT_REL: _sha256(result_bytes),
        }
        if trial.get("artifact_sha256") != {CODE_REL: artifact_hashes[CODE_REL], REPORT_REL: artifact_hashes[REPORT_REL], RESULT_REL: artifact_hashes[RESULT_REL]}:
            failures.append("native process-exit artifact hashes do not match the final deliverables")

        result_problems = validate_research_result(result_data)
        failures.extend(result_problems)
        source_record, _source_record_digest = _source_provenance(workspace)
        source_claim = result_data.get("source") if isinstance(result_data, dict) else None
        if isinstance(source_claim, dict):
            expected_source_fields = {
                "source_excerpt_path": source_record["source_excerpt_path"],
                "title": source_record["title"],
                "authors": source_record["authors"],
                "year": source_record["year"],
                "identifier": source_record["identifier"],
                "url": source_record["url"],
            }
            for key, expected in expected_source_fields.items():
                if source_claim.get(key) != expected:
                    failures.append(f"research source provenance does not match the archived citation: {key}")
            excerpt_path = _safe_root_file(workspace, source_record["source_excerpt_path"])
            excerpt_digest = _sha256(excerpt_path.read_bytes())
            if source_claim.get("source_excerpt_sha256") != excerpt_digest:
                failures.append("source excerpt hash claim does not match the attributed public excerpt")
        else:
            excerpt_digest = "0" * 64

        workspace_manifest_file = _safe_root_file(workspace, series_manifest_path)
        if _sha256(workspace_manifest_file.read_bytes()) != verified_digest:
            failures.append("workspace series manifest bytes changed")

        implementation_digest = _sha256(code_bytes)
        report_digest = _sha256(report_bytes)
        claims = result_data.get("machine_claims") if isinstance(result_data, dict) else None
        expected_claims = _expected_machine_claims(
            series_path=series_manifest_path,
            series_id=manifest["series_id"],
            series_digest=verified_digest,
            implementation_digest=implementation_digest,
            report_digest=report_digest,
            source_excerpt_digest=excerpt_digest,
        )
        if not _same_json(claims, expected_claims):
            failures.append("structured machine claims or source/artifact hashes do not match independently observed facts")

        fixture, fixture_bytes = load_fixture(root)
        workspace_fixture, workspace_fixture_bytes = load_fixture(workspace)
        if fixture_bytes != workspace_fixture_bytes or fixture != workspace_fixture:
            failures.append("prepared fixture data differs from the frozen source fixture")
        result_claims = result_data.get("machine_claims")
        if not isinstance(result_claims, dict) or result_claims.get("series_manifest_path") != series_manifest_path:
            failures.append("research artifact references a different named series manifest")

        native = _recompute_native_actions(root, workspace, trial) if evidence_scope == "fresh-native-trial" else {
            "actions": trial.get("native_actions", []),
            "trace_complete": True,
        }
        native_actions = native.get("actions", []) if isinstance(native.get("actions"), list) else []
        if evidence_scope == "fresh-native-trial":
            failures.extend(_research_action_problems(
                native.get("actions", []), trial["harness"], workspace, root,
                trace=native, series_manifest_path=series_manifest_path,
                causal_actions=causal_actions,
            ))
            route_evidence = entry_route_evidence(
                native.get("actions", []), trial["harness"], workspace,
                task="research",
                task_route_paths=(
                    "examples/fresh-agent-evals/research/README.md",
                    "examples/fresh-agent-evals/research/task.md",
                ),
            )
            if route_evidence.get("status") != "pass":
                missing_route = route_evidence.get("missing_evidence", [])
                failures.append(
                    "entrypoint-first research route evidence is incomplete: "
                    + "; ".join(item for item in missing_route if isinstance(item, str))
                )
            command_evidence, command_problems = _native_command_evidence(
                native.get("actions", []), trial["harness"], workspace, series_manifest_path,
                causal_actions=causal_actions,
            )
            failures.extend(command_problems)
            completion_check, completion_problems = _final_completion_check_evidence(
                native.get("actions", []),
                trial["harness"],
                workspace,
                series_manifest_path,
                command_evidence.get("write_sequences") if isinstance(command_evidence, dict) else None,
            )
            failures.extend(completion_problems)
            if not isinstance(command_evidence, dict):
                command_evidence = {}
            command_evidence["completion_check"] = completion_check
        else:
            route_evidence = {
                "contract_version": "entrypoint-first-v1",
                "task": "research",
                "status": "not_applicable",
                "reason": "synthetic-selfcheck does not represent a native subject route",
            }
            command_evidence = {
                "completion_check": {
                    "status": "not_applicable",
                    "reason": "synthetic selfcheck has no native final completion-command trace",
                }
            }

        dev_report, independent_report = evaluate_suite(
            root,
            workspace,
            CODE_REL,
            series_id=manifest["series_id"],
            series_manifest_sha256=verified_digest,
            source_excerpt_sha256=excerpt_digest,
            include_heldout=True,
        )
        if not canonical_json_equal(report_data, dev_report):
            failures.append("development comparison report is stale, tampered, or not independently reproducible")
        if independent_report is None:
            failures.append("independent report did not include the reserved held-out fixture")
        else:
            report = independent_report
            development_runs = [item for item in independent_report["runs"] if item["role"] == "development"]
            if not development_runs or not any(item["challenger"]["callback_yields_captured"] > 0 for item in development_runs):
                failures.append("the locally authored callback did not yield a development fixture payload")
            if not any(item["challenger"]["completed_evaluator_calls"] > 0 for item in development_runs):
                failures.append("no callback payload was independently recognized and completed on a development input")
            heldout_runs = [item for item in independent_report["runs"] if item["role"] == "reserved-heldout"]
            if not heldout_runs:
                failures.append("reserved held-out callback executions are missing")

        _scan_no_symlinks(workspace)
        final_source_manifest, final_source_digest = verify_series(root, series_manifest_path)
        if final_source_digest != verified_digest or final_source_manifest != manifest:
            failures.append("trusted source closure changed during independent callback execution")
        final_frozen, final_frozen_digest = verify_series(workspace, series_manifest_path)
        if final_frozen_digest != verified_digest or final_frozen != manifest:
            failures.append("a frozen dependency changed during independent callback execution")
        if _sha256(code_path.read_bytes()) != implementation_digest:
            failures.append("callback implementation changed during independent recomputation")
        if _sha256(report_path.read_bytes()) != report_digest or _sha256(result_path.read_bytes()) != artifact_hashes[RESULT_REL]:
            failures.append("research artifacts changed during independent recomputation")
    except (OSError, ValueError, SeriesError, EvaluationError, GradeError, PathSafetyError) as exc:
        failures.append(f"independent grading failed closed: {type(exc).__name__}")

    failures = list(dict.fromkeys(failures))
    success = not failures
    diagnostics = compose_research_diagnostics(
        failures,
        native_actions,
        trial.get("harness", "unknown") if isinstance(trial, dict) else "unknown",
        report,
        artifact_paths=(CODE_REL, REPORT_REL, RESULT_REL, SOURCE_PROVENANCE_REL),
        causal_actions=causal_actions,
        repo=root,
        workspace=workspace,
    )
    return {
        "verdict_schema_version": 1,
        "series_id": series_id,
        "series_manifest_sha256": series_digest,
        "case_id": CASE_ID,
        "evidence_scope": evidence_scope,
        "success": success,
        "status": "pass" if success else "fail",
        "failure_reasons": failures,
        "diagnostics": diagnostics,
        "entry_route": route_evidence,
        "artifact_hashes": artifact_hashes,
        "native_command_evidence": command_evidence,
        "independent_report": report,
        "limitations": [
            "The eligible pair denominator and every quality bit are known only for the synthetic fixture.",
            "The synthetic uniform reference is not OpenForage genetic and does not measure live performance.",
            "Live performance, out-of-sample return correlation, and server acceptance remain unknown.",
            "The public suite is instruction-isolated and bounded, not a perfect blind evaluation or OS security boundary.",
        ],
    }
