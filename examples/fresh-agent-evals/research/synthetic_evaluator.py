"""Trusted deterministic evaluator for the public synthetic research fixture.

All results emitted here are fixture-only. The uniform comparator is a small
synthetic reference and is never labeled as OpenForage genetic/GA behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
EVALS_ROOT = HERE.parent
if str(EVALS_ROOT / "v2") not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT / "v2"))
if str(REPO_ROOT / "examples/agent-campaign") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "examples/agent-campaign"))

from contract import FixtureContext, canonical_fixture_payload  # noqa: E402
from paths import PathSafetyError, safe_relative  # noqa: E402
from processes import run_bounded  # noqa: E402
from schema import loads_strict  # noqa: E402
from stratified_policy import IDENTITY_MAPPING, Ledger, Proposal  # noqa: E402


RESEARCH_REL = Path("examples/fresh-agent-evals/research")
FIXTURE_REL = RESEARCH_REL / "fixture.json"
WORKER_REL = RESEARCH_REL / "callback_worker.py"
IMPLEMENTATION_REL = "trial-output/challenger.py"
TARGET_COMPLETION = 4
MAX_REFERENCE_DRAWS = 128
CALLBACK_TIMEOUT_SECONDS = 15.0
WORKER_OUTPUT_LIMIT = 2_000_000
REFERENCE_ID = "synthetic_uniform_pair_selector_v1"
GENETIC_TARGET = {
    "algorithm": "genetic",
    "target_completed_simulations": 100_000,
    "observed_completed_simulations": None,
    "status": "unmeasured_target",
}


class EvaluationError(RuntimeError):
    """The frozen fixture or a bounded callback execution is invalid."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular(root: Path, relative: str | Path) -> bytes:
    try:
        safe = safe_relative(str(relative).replace(os.sep, "/"))
    except PathSafetyError as exc:
        raise EvaluationError("fixture path is not a safe relative path") from exc
    current = root
    for part in safe.parts:
        current = current / part
        if current.is_symlink():
            raise EvaluationError("fixture path contains a symlink")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise EvaluationError("fixture file is missing or escapes its root")
    return current.read_bytes()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise EvaluationError("fixture contains a non-canonical JSON value") from exc


def validate_fixture(fixture: object) -> dict:
    if not isinstance(fixture, dict) or type(fixture.get("fixture_schema_version")) is not int:
        raise EvaluationError("fixture root or schema version is invalid")
    if fixture.get("fixture_schema_version") != 1:
        raise EvaluationError("fixture schema version is unsupported")
    if fixture.get("evaluation_origin") != "synthetic_fixture_only":
        raise EvaluationError("fixture origin must remain explicitly synthetic")
    if type(fixture.get("target_completed_evaluations_per_arm")) is not int or fixture["target_completed_evaluations_per_arm"] != TARGET_COMPLETION:
        raise EvaluationError("fixture completed-call target is unsupported")
    if type(fixture.get("max_callback_yields_per_run")) is not int or fixture["max_callback_yields_per_run"] != 64:
        raise EvaluationError("fixture callback yield bound is invalid")
    reference = fixture.get("uniform_reference")
    if (
        not isinstance(reference, dict)
        or reference.get("identity") != REFERENCE_ID
        or reference.get("is_openforage_genetic") is not False
        or reference.get("is_library_measurement") is not False
    ):
        raise EvaluationError("uniform reference identity or synthetic label is invalid")
    inputs = fixture.get("inputs")
    if not isinstance(inputs, list):
        raise EvaluationError("fixture inputs must be a list")
    expected_roles = {
        "development-alpha": "development",
        "development-beta": "development",
        "reserved-heldout-gamma": "reserved-heldout",
    }
    if {item.get("input_id"): item.get("role") for item in inputs if isinstance(item, dict)} != expected_roles:
        raise EvaluationError("fixture inputs must contain two development cases and the reserved held-out case")

    ids = set()
    for item in inputs:
        if not isinstance(item, dict):
            raise EvaluationError("fixture input must be an object")
        input_id = item.get("input_id")
        if not isinstance(input_id, str) or input_id in ids:
            raise EvaluationError("fixture input ids must be unique strings")
        ids.add(input_id)
        if not isinstance(item.get("era_id"), str) or not isinstance(item.get("config_id"), str):
            raise EvaluationError("fixture input must carry explicit synthetic era/config scope")
        seeds = item.get("paired_seeds")
        if (
            not isinstance(seeds, list) or len(seeds) != 2
            or any(type(seed) is not int or seed < 0 for seed in seeds)
            or len(set(seeds)) != len(seeds)
        ):
            raise EvaluationError("each fixture input must declare two distinct integer paired seeds")
        pairs = item.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            raise EvaluationError("fixture input must contain explicit feature×rotation pairs")
        pair_map = {}
        for pair in pairs:
            if not isinstance(pair, dict):
                raise EvaluationError("fixture pair must be an object")
            pair_id = pair.get("pair_id")
            if (
                not isinstance(pair_id, str) or pair_id in pair_map
                or pair_id != f"{pair.get('rotation')}|{pair.get('feature')}"
                or not isinstance(pair.get("rotation"), str) or not pair["rotation"]
                or not isinstance(pair.get("feature"), str) or not pair["feature"]
                or type(pair.get("loaded")) is not bool
                or type(pair.get("eligible")) is not bool
            ):
                raise EvaluationError("fixture pair identity or eligibility types are invalid")
            if pair["eligible"] is True and pair["loaded"] is not True:
                raise EvaluationError("an unloaded synthetic pair cannot be eligible")
            if pair["loaded"] and pair["eligible"]:
                if pair.get("exclusion_reason") is not None:
                    raise EvaluationError("eligible loaded fixture pairs cannot carry an exclusion reason")
            elif not isinstance(pair.get("exclusion_reason"), str) or not pair["exclusion_reason"]:
                raise EvaluationError("excluded or unloaded pairs need an explicit reason")
            pair_map[pair_id] = pair
        if not any(pair["loaded"] is True and pair["eligible"] is True for pair in pairs):
            raise EvaluationError("each fixture input needs a known eligible denominator")
        streams = item.get("candidate_streams")
        if not isinstance(streams, dict) or set(streams) != set(pair_map):
            raise EvaluationError("each fixture pair needs one explicit candidate stream")
        for pair_id, records in streams.items():
            if not isinstance(records, list) or not records:
                raise EvaluationError("fixture candidate streams must be non-empty lists")
            for record in records:
                if not isinstance(record, dict) or set(record) != {"payload", "depth", "synthetic_quality_pass"}:
                    raise EvaluationError("fixture candidate record does not match its typed shape")
                if not isinstance(record["payload"], dict):
                    raise EvaluationError("fixture graph payload must be an object")
                if record["depth"] is not None and type(record["depth"]) is not int:
                    raise EvaluationError("fixture depth must be an integer or null")
                if record["depth"] is not None and record["depth"] < 1:
                    raise EvaluationError("fixture depth must be positive or null")
                quality = record["synthetic_quality_pass"]
                if quality is not None and type(quality) is not bool:
                    raise EvaluationError("fixture quality label must be a boolean or null")
                if quality is None and record["depth"] is not None and (
                    type(record["depth"]) is not int or record["depth"] < 1
                ):
                    raise EvaluationError("unregistered fixture descriptors may only carry a positive integer depth")

    return fixture


def load_fixture(root: Path) -> tuple[dict, bytes]:
    raw = _read_regular(root, FIXTURE_REL)
    try:
        data = loads_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvaluationError("fixture JSON is invalid or has duplicate keys") from exc
    return validate_fixture(data), raw


def _candidate_index(fixture_input: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    unregistered: set[str] = set()
    pair_map = {item["pair_id"]: item for item in fixture_input["pairs"]}
    for pair_id, records in fixture_input["candidate_streams"].items():
        pair = pair_map[pair_id]
        for record in records:
            if record["synthetic_quality_pass"] is None:
                canonical = canonical_fixture_payload(record["payload"])
                if canonical is not None:
                    unregistered.add(canonical[0])
                continue
            canonical = canonical_fixture_payload(record["payload"])
            if canonical is None:
                raise EvaluationError("registered fixture graph does not have a supported canonical identity")
            identity, _encoded = canonical
            descriptor = {
                "pair_id": pair_id,
                "rotation": pair["rotation"],
                "feature": pair["feature"],
                "loaded": pair["loaded"],
                "eligible": pair["eligible"],
                "depth": record["depth"],
                "synthetic_quality_pass": record["synthetic_quality_pass"],
            }
            previous = result.get(identity)
            if previous is not None and previous != descriptor:
                raise EvaluationError("fixture graph identity maps to conflicting trusted descriptors")
            result[identity] = descriptor
    if unregistered & set(result):
        raise EvaluationError("unregistered fixture payload identity collides with a registered graph")
    return result


def _safe_subject(workspace: Path, implementation: str) -> Path:
    try:
        relative = safe_relative(implementation)
    except PathSafetyError as exc:
        raise EvaluationError("implementation path is unsafe") from exc
    target = workspace
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise EvaluationError("implementation path contains a symlink")
    if not target.is_file() or not target.resolve().is_relative_to(workspace.resolve()):
        raise EvaluationError("implementation is missing or escapes the trial workspace")
    return target


def _worker_environment(workspace: Path) -> dict[str, str]:
    return {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workspace),
        "LC_ALL": "C.UTF-8",
    }


def execute_callback(
    trusted_root: Path,
    workspace: Path,
    implementation: str,
    fixture_input: dict,
    seed: int,
    *,
    timeout: float = CALLBACK_TIMEOUT_SECONDS,
) -> dict:
    subject = _safe_subject(workspace, implementation)
    subject_before = _sha256(subject.read_bytes())
    worker = trusted_root / WORKER_REL
    fixture_path = trusted_root / FIXTURE_REL
    for relative, required in ((WORKER_REL, worker), (FIXTURE_REL, fixture_path)):
        current = trusted_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise EvaluationError("trusted worker or fixture path contains a symlink")
        if not required.is_file() or not required.resolve().is_relative_to(trusted_root.resolve()):
            raise EvaluationError("trusted worker or fixture path is missing, unsafe, or a symlink")
    argv = [
        sys.executable, str(worker), "--subject", str(subject), "--fixture", str(fixture_path),
        "--input-id", fixture_input["input_id"], "--seed", str(seed),
    ]
    try:
        process = run_bounded(argv, cwd=workspace, env=_worker_environment(workspace), timeout=timeout)
    except OSError as exc:
        raise EvaluationError(f"callback worker could not start: {type(exc).__name__}") from exc
    if process.timed_out or process.process_group_remaining or process.returncode != 0:
        raise EvaluationError("callback worker timed out, left an owned process running, or exited unsuccessfully")
    if len(process.stdout) > WORKER_OUTPUT_LIMIT:
        raise EvaluationError("callback worker output exceeded its fixed transport bound")
    try:
        worker_result = loads_strict(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvaluationError("callback worker returned malformed or duplicate-key JSON") from exc
    if (
        not isinstance(worker_result, dict)
        or worker_result.get("worker_schema_version") != 1
        or worker_result.get("execution_status") != "completed"
        or worker_result.get("subject_sha256") != subject_before
        or not isinstance(worker_result.get("payloads"), list)
    ):
        error_type = worker_result.get("error_type") if isinstance(worker_result, dict) else None
        safe_error = error_type if isinstance(error_type, str) else "invalid_worker_result"
        raise EvaluationError(f"callback execution did not complete: {safe_error}")
    if _sha256(subject.read_bytes()) != subject_before:
        raise EvaluationError("callback implementation changed during the independently bounded run")
    for key in ("captured_yield_count", "yield_count_lower_bound"):
        if type(worker_result.get(key)) is not int or worker_result[key] < 0:
            raise EvaluationError("callback worker yield counts are malformed")
    if worker_result["captured_yield_count"] != len(worker_result["payloads"]):
        raise EvaluationError("callback worker payload count does not match its structured count")
    return worker_result


class _RunAccumulator:
    def __init__(self, fixture_input: dict, target: int):
        self.fixture_input = fixture_input
        self.target = target
        self.index = _candidate_index(fixture_input)
        self.pairs = {item["pair_id"]: item for item in fixture_input["pairs"]}
        self.eligible_pairs = sorted(
            pair_id for pair_id, pair in self.pairs.items()
            if pair["loaded"] is True and pair["eligible"] is True
        )
        self.excluded_pairs = sorted(
            pair_id for pair_id, pair in self.pairs.items()
            if pair["loaded"] is True and pair["eligible"] is not True
        )
        self.unloaded_pairs = sorted(
            pair_id for pair_id, pair in self.pairs.items() if pair["loaded"] is not True
        )
        self.ledger = Ledger()
        self.identities_seen: set[str] = set()
        self.covered_cells: set[str] = set()
        self.rejections = {
            "duplicate_identity": 0,
            "depth_over_six": 0,
            "excluded_pair": 0,
            "unloaded_pair": 0,
            "unknown_identity": 0,
            "invalid_payload": 0,
        }
        self.attempts_processed = 0
        self.synthetic_quality_passes = 0

    def add_transport(self, transport: dict) -> None:
        self.attempts_processed += 1
        if not isinstance(transport, dict) or transport.get("transport_status") != "ok" or set(transport) != {"transport_status", "payload"}:
            self.rejections["invalid_payload"] += 1
            self.ledger.reject("invalid-payload")
            return
        canonical = canonical_fixture_payload(transport["payload"])
        if canonical is None:
            self.rejections["invalid_payload"] += 1
            self.ledger.reject("invalid-payload")
            return
        identity, _encoded = canonical
        descriptor = self.index.get(identity)
        if descriptor is None:
            self.rejections["unknown_identity"] += 1
            self.ledger.reject("unsupported-identity")
            return
        if identity in self.identities_seen:
            self.rejections["duplicate_identity"] += 1
            self.ledger.reject("duplicate")
            return
        self.identities_seen.add(identity)
        if descriptor["loaded"] is not True:
            self.rejections["unloaded_pair"] += 1
            self.ledger.reject("unavailable")
            return
        if descriptor["eligible"] is not True:
            self.rejections["excluded_pair"] += 1
            self.ledger.reject("unavailable")
            return
        depth = descriptor["depth"]
        if type(depth) is not int or depth < 1:
            self.rejections["invalid_payload"] += 1
            self.ledger.reject("invalid-payload")
            return
        if depth > 6:
            self.rejections["depth_over_six"] += 1
            self.ledger.reject("depth")
            return
        proposal = Proposal(
            construction_key=identity,
            cell=descriptor["pair_id"],
            descriptor=f"depth{depth}",
            payload=transport["payload"],
            identity_kind=IDENTITY_MAPPING,
        )
        self.ledger.accept(proposal)
        self.covered_cells.add(descriptor["pair_id"])
        quality = descriptor["synthetic_quality_pass"]
        if type(quality) is not bool:
            self.rejections["invalid_payload"] += 1
            self.ledger.reject("invalid-payload")
            return
        self.ledger.evaluations += 1
        self.ledger.passed += int(quality)
        self.synthetic_quality_passes += int(quality)

    def snapshot(self, *, callback_result: dict | None = None, reference: dict | None = None) -> dict:
        completed = self.ledger.evaluations
        denominator = len(self.eligible_pairs)
        if callback_result is not None:
            captured = callback_result["captured_yield_count"]
            stream_exhausted = callback_result["yield_stream_exhausted"] is True
            yield_cap = callback_result["yield_limit_reached"] is True
            yield_lower_bound = callback_result["yield_count_lower_bound"]
            yield_count_status = "lower_bound" if yield_cap else "exact"
        else:
            captured = self.attempts_processed
            stream_exhausted = bool(reference and reference.get("all_streams_exhausted"))
            yield_cap = False
            yield_lower_bound = captured
            yield_count_status = "exact"
        return {
            "target_completed_evaluations": self.target,
            "completed_evaluator_calls": completed,
            "target_reached": completed >= self.target,
            "eligible_denominator": denominator,
            "eligible_denominator_scope": "known_only_for_this_synthetic_fixture",
            "eligible_pair_ids": self.eligible_pairs,
            "excluded_pair_ids": self.excluded_pairs,
            "unloaded_pair_ids": self.unloaded_pairs,
            "unique_valid_cells_reached": sorted(self.covered_cells),
            "unique_valid_cell_count": len(self.covered_cells),
            "synthetic_coverage_fraction": round(len(self.covered_cells) / denominator, 6) if denominator else None,
            "coverage_scope": "synthetic_construction_coverage_only_not_oos_return_correlation",
            "synthetic_unique_proposals_accepted": self.ledger.proposals,
            "proposal_acceptance_scope": "synthetic_fixture_policy_only_not_server_acceptance",
            "evaluator_attempts_processed": self.attempts_processed,
            "callback_yields_captured": captured,
            "callback_yield_count_status": yield_count_status,
            "callback_yield_count_lower_bound": yield_lower_bound,
            "yield_limit_reached": yield_cap,
            "stream_exhausted": stream_exhausted,
            "not_evaluated_after_target": max(0, captured - self.attempts_processed),
            "synthetic_quality_label": "synthetic_fixture_quality_pass_bit",
            "synthetic_quality_passes": self.synthetic_quality_passes,
            "synthetic_quality_rate": round(self.synthetic_quality_passes / completed, 6) if completed else None,
            "rejections": dict(self.rejections),
            "shared_ledger": {
                "accounting_module": "examples/agent-campaign/stratified_policy.py",
                "accepted_proposals": self.ledger.proposals,
                "completed_evaluator_calls": self.ledger.evaluations,
                "duplicate_rejections": self.ledger.duplicates_rejected,
                "depth_rejections": self.ledger.depth_rejected,
                "unavailable_rejections": self.ledger.unavailable_rejected,
                "other_rejections": self.ledger.other_rejected,
            },
            "reference_generator": reference,
        }


def _callback_run(
    trusted_root: Path,
    workspace: Path,
    implementation: str,
    fixture_input: dict,
    seed: int,
    target: int,
) -> dict:
    worker_result = execute_callback(trusted_root, workspace, implementation, fixture_input, seed)
    accumulator = _RunAccumulator(fixture_input, target)
    for output in worker_result["payloads"]:
        if accumulator.ledger.evaluations >= target:
            break
        accumulator.add_transport(output)
    result = accumulator.snapshot(callback_result=worker_result)
    result.update({
        "input_id": fixture_input["input_id"],
        "role": fixture_input["role"],
        "seed": seed,
        "arm": "challenger",
        "scope": {
            "era_id": fixture_input["era_id"],
            "config_id": fixture_input["config_id"],
            "scope_origin": "synthetic_fixture_only",
        },
    })
    return result


def _uniform_run(fixture_input: dict, seed: int, target: int) -> dict:
    context = FixtureContext(fixture_input, seed)
    accumulator = _RunAccumulator(fixture_input, target)
    active = list(context.eligible_pairs)
    streams = {pair_id: context.stream_for(pair_id) for pair_id in context.eligible_pairs}
    rng = random.Random(seed)
    draws = 0
    exhaustion_draws = 0
    while accumulator.ledger.evaluations < target and active and draws < MAX_REFERENCE_DRAWS:
        pair_id = rng.choice(active)
        try:
            payload = next(streams[pair_id])
        except StopIteration:
            active.remove(pair_id)
            exhaustion_draws += 1
            continue
        draws += 1
        accumulator.add_transport({"transport_status": "ok", "payload": payload})
    all_exhausted = not active
    result = accumulator.snapshot(reference={
        "identity": REFERENCE_ID,
        "selection": "uniform choice among remaining eligible loaded pair ids per draw",
        "max_draws": MAX_REFERENCE_DRAWS,
        "draws": draws,
        "stream_exhaustion_draws": exhaustion_draws,
        "all_streams_exhausted": all_exhausted,
        "is_openforage_genetic": False,
    })
    result.update({
        "input_id": fixture_input["input_id"],
        "role": fixture_input["role"],
        "seed": seed,
        "arm": "synthetic_uniform_reference",
        "scope": {
            "era_id": fixture_input["era_id"],
            "config_id": fixture_input["config_id"],
            "scope_origin": "synthetic_fixture_only",
        },
    })
    return result


def evaluate_input_seed(
    trusted_root: Path,
    workspace: Path,
    implementation: str,
    fixture_input: dict,
    seed: int,
    target: int = TARGET_COMPLETION,
) -> dict:
    challenger = _callback_run(trusted_root, workspace, implementation, fixture_input, seed, target)
    reference = _uniform_run(fixture_input, seed, target)
    equal = challenger["completed_evaluator_calls"] == reference["completed_evaluator_calls"]
    complete_pair = (
        challenger["target_reached"] is True
        and reference["target_reached"] is True
        and equal
    )
    paired = {
        "same_input_and_seed": True,
        "equal_completed_evaluator_calls": equal,
        "comparison_status": "complete_equal_calls" if complete_pair else "incomplete_comparison",
        "synthetic_quality_pass_difference": (
            challenger["synthetic_quality_passes"] - reference["synthetic_quality_passes"]
            if complete_pair else None
        ),
        "unique_valid_cell_difference": (
            challenger["unique_valid_cell_count"] - reference["unique_valid_cell_count"]
            if complete_pair else None
        ),
    }
    return {
        "input_id": fixture_input["input_id"],
        "role": fixture_input["role"],
        "seed": seed,
        "scope": {
            "era_id": fixture_input["era_id"],
            "config_id": fixture_input["config_id"],
            "scope_origin": "synthetic_fixture_only",
        },
        "challenger": challenger,
        "reference": reference,
        "paired_comparison": paired,
    }


def _report_from_runs(
    fixture: dict,
    fixture_bytes: bytes,
    runs: list[dict],
    *,
    role_scope: str,
    implementation_sha256: str,
    series_id: str,
    series_manifest_sha256: str,
    source_excerpt_sha256: str,
) -> dict:
    target = fixture["target_completed_evaluations_per_arm"]
    all_complete = bool(runs) and all(
        item["paired_comparison"]["comparison_status"] == "complete_equal_calls"
        for item in runs
    )
    return {
        "schema_version": 1,
        "report_type": "synthetic_research_comparison",
        "evidence_scope": "synthetic_fixture_only",
        "role_scope": role_scope,
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": _sha256(fixture_bytes),
        "series_id": series_id,
        "series_manifest_sha256": series_manifest_sha256,
        "implementation_sha256": implementation_sha256,
        "source_excerpt_sha256": source_excerpt_sha256,
        "reference": {
            "identity": REFERENCE_ID,
            "kind": "synthetic uniform selector over fixture eligible pairs",
            "is_openforage_genetic": False,
            "is_library_measurement": False,
        },
        "target_completed_evaluations_per_arm": target,
        "eligible_denominator_note": "known only for each explicit synthetic fixture input",
        "construction_coverage_note": "unique valid feature×rotation cells reached; not out-of-sample return correlation",
        "proposal_acceptance_note": "local fixture proposal accounting is not server acceptance",
        "run_count": len(runs),
        "paired_run_count": len({(item["input_id"], item["seed"]) for item in runs}),
        "comparison_status": "complete_equal_calls" if all_complete else "incomplete_comparison",
        "runs": runs,
        "live_measurements": {
            "live_performance": {"status": "unknown", "value": None},
            "out_of_sample_return_correlation": {"status": "unknown", "value": None},
            "server_acceptance": {"status": "unknown", "value": None},
        },
        "separate_genetic_campaign_target": dict(GENETIC_TARGET),
        "interpretation": "descriptive synthetic fixture comparison only; no superiority or live-performance inference",
    }


def evaluate_suite(
    trusted_root: Path,
    workspace: Path,
    implementation: str,
    *,
    series_id: str,
    series_manifest_sha256: str,
    source_excerpt_sha256: str,
    include_heldout: bool,
) -> tuple[dict, dict | None]:
    fixture, fixture_bytes = load_fixture(trusted_root)
    subject = _safe_subject(workspace, implementation)
    implementation_sha256 = _sha256(subject.read_bytes())
    selected_inputs = [
        item for item in fixture["inputs"]
        if item["role"] == "development" or include_heldout and item["role"] == "reserved-heldout"
    ]
    runs = []
    for fixture_input in selected_inputs:
        for seed in fixture_input["paired_seeds"]:
            runs.append(evaluate_input_seed(
                trusted_root, workspace, implementation, fixture_input, seed,
                fixture["target_completed_evaluations_per_arm"],
            ))
    development_runs = [item for item in runs if item["role"] == "development"]
    development_report = _report_from_runs(
        fixture, fixture_bytes, development_runs,
        role_scope="development_inputs_only",
        implementation_sha256=implementation_sha256,
        series_id=series_id,
        series_manifest_sha256=series_manifest_sha256,
        source_excerpt_sha256=source_excerpt_sha256,
    )
    if not include_heldout:
        return development_report, None
    full_report = _report_from_runs(
        fixture, fixture_bytes, runs,
        role_scope="development_plus_reserved_heldout",
        implementation_sha256=implementation_sha256,
        series_id=series_id,
        series_manifest_sha256=series_manifest_sha256,
        source_excerpt_sha256=source_excerpt_sha256,
    )
    return development_report, full_report


def read_structured(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    try:
        value = loads_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvaluationError("JSON artifact is invalid or contains duplicate keys") from exc
    if not isinstance(value, dict):
        raise EvaluationError("JSON artifact root must be an object")
    return value, raw


def canonical_json_equal(left: object, right: object) -> bool:
    try:
        return _canonical(left) == _canonical(right)
    except EvaluationError:
        return False
