"""Campaign and experiment record orchestration.

State/history legality lives in :mod:`state_machine`; protocol fields in
:mod:`protocol`; frozen/compatible baselines in :mod:`baselines`; resources,
lifecycle, accounting, coverage, and submissions in :mod:`resources`. This
module owns the record shape and the strict template directory check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .baselines import (
    check_benchmark_record,
    check_compatible_baseline,
    check_frozen_baseline,
)
from .common import (
    CAMPAIGN_DIR,
    STATE_MACHINE_PATH,
    is_iso_datetime,
    is_real_string,
    load_json,
    read_text,
    rel,
    scan_placeholders,
)
from .protocol import (
    check_completion_honesty,
    check_experiment_statistics,
    check_preregistration,
    check_provenance,
    check_security,
    check_shared_results,
    check_statistics,
    check_usefulness_metrics,
    require_keys,
)
from .resources import (
    check_checkpoints,
    check_coverage_ledger,
    check_lifecycle_observations,
    check_resource_limits,
    check_resources,
    check_submissions,
)
from .state_machine import (
    TERMINAL_STATES,
    check_history,
    load_state_machine,
)

TRIAL_STATUSES = {"completed", "failed", "inconclusive", "abandoned", "running"}
RESOLVED_TRIAL_STATUSES = TRIAL_STATUSES - {"running"}
RECORD_CLASSIFICATIONS = {"measured", "exploratory"}

CAMPAIGN_REQUIRED_KEYS = {
    "campaign_id",
    "owner_session_identity",
    "state_machine",
    "transitions",
    "objective",
    "frozen_baseline",
    "compatible_baseline",
    "preregistration",
    "statistics",
    "pipeline_stages",
    "budgets",
    "seed_sets",
    "config_hashes",
    "coverage_ledger",
    "usefulness_metrics",
    "era_segments",
    "config_segments",
    "submissions",
    "server_results_through",
    "checkpoints",
    "resources",
    "lifecycle_observations",
    "provenance",
    "shared_results",
    "negative_findings",
}

EXPERIMENT_REQUIRED_KEYS = {
    "experiment_id",
    "campaign_id",
    "owner_session_identity",
    "preregistration_ref",
    "custom_algorithm",
    "comparison",
    "statistics",
    "usefulness_metrics",
    "resources",
    "provenance",
    "segments",
    "submissions",
    "honesty",
}


def _claims(data) -> tuple[bool, bool]:
    """Return (registered, completed) claims for a campaign record.

    Registration is driven by the record's own claim: a measured
    classification, or a protocol that has been opened (`experiment` state).
    An exploratory comparison — including one entered from research — is not
    a registered claim and may carry not-yet-recorded protocol fields with
    stated reasons. ``completed`` covers completed *registered* claims, which
    must carry the complete trial inventory.
    """
    state_machine = data.get("state_machine")
    state = state_machine.get("state") if isinstance(state_machine, dict) else None
    statistics = data.get("statistics")
    classification = statistics.get("classification") if isinstance(statistics, dict) else None
    registered = classification == "measured" or state == "experiment"
    completed = state in {"comparison", "completed"} and classification == "measured"
    return registered, completed


def _check_pipeline_stages(report, where: str, pipeline) -> None:
    require_keys(
        report,
        where,
        pipeline,
        {"paper", "mechanism", "hypothesis", "custom_algorithm", "measured_comparison"},
    )
    if not isinstance(pipeline, dict):
        return
    algorithm = pipeline.get("custom_algorithm") or {}
    if isinstance(algorithm, dict):
        if algorithm.get("depth_cap") != 6:
            report.fail(f"{where}.custom_algorithm.depth_cap must be 6")
        if algorithm.get("self_rejects_depth_gt_6") is not True:
            report.fail(
                f"{where}.custom_algorithm.self_rejects_depth_gt_6 must be true"
            )
        if algorithm.get("filler_nodes_added") is not False:
            report.fail(f"{where}.custom_algorithm.filler_nodes_added must be false")
        if algorithm.get("acceptance_thresholds_unchanged") is not True:
            report.fail(
                f"{where}.custom_algorithm.acceptance_thresholds_unchanged must be true"
            )


def check_campaign_record(report, root: Path, record_path: Path, machine, template: bool) -> None:
    where = rel(root, record_path) if record_path.is_relative_to(root) else str(record_path)
    data = load_json(report, record_path)
    if data is None:
        return
    if not isinstance(data, dict):
        report.fail(f"{where}: top level must be an object")
        return
    require_keys(report, where, data, CAMPAIGN_REQUIRED_KEYS)

    state_machine = data.get("state_machine")
    require_keys(
        report,
        f"{where}.state_machine",
        state_machine,
        {"definition", "state", "revision", "resume_target", "ownership"},
    )
    if isinstance(state_machine, dict):
        if state_machine.get("definition") != STATE_MACHINE_PATH:
            report.fail(f"{where}.state_machine.definition must reference {STATE_MACHINE_PATH}")
        ownership = state_machine.get("ownership")
        require_keys(
            report,
            f"{where}.state_machine.ownership",
            ownership,
            {
                "owner_session_identity",
                "owner_revision",
                "fencing_token",
                "claimed_at",
                "claim_mechanism",
                "claim_verified",
            },
        )
        if isinstance(ownership, dict) and not template:
            if not is_real_string(ownership.get("owner_session_identity")):
                report.fail(f"{where}.state_machine.ownership: owner_session_identity must be real")
            if not is_iso_datetime(ownership.get("claimed_at")):
                report.fail(f"{where}.state_machine.ownership: claimed_at must be a valid ISO-8601 datetime")
            if not is_real_string(ownership.get("claim_mechanism")):
                report.fail(f"{where}.state_machine.ownership: claim_mechanism must be real")
            if not isinstance(ownership.get("claim_verified"), bool):
                report.fail(f"{where}.state_machine.ownership: claim_verified must be an explicit boolean")
            if not is_real_string(ownership.get("fencing_token")) and not (
                isinstance(ownership.get("fencing_token"), (int, float))
                and not isinstance(ownership.get("fencing_token"), bool)
            ):
                report.fail(f"{where}.state_machine.ownership: fencing_token must be real")

    check_history(report, where, data, machine, template)

    state = state_machine.get("state") if isinstance(state_machine, dict) else None
    registered, completed = _claims(data)

    check_frozen_baseline(report, f"{where}.frozen_baseline", data.get("frozen_baseline"), template)
    check_compatible_baseline(report, f"{where}.compatible_baseline", data.get("compatible_baseline"), template)
    check_preregistration(report, f"{where}.preregistration", data.get("preregistration"), template, registered)
    check_statistics(report, f"{where}.statistics", data.get("statistics"), template, completed)
    check_usefulness_metrics(report, f"{where}.usefulness_metrics", data.get("usefulness_metrics"), template)
    check_shared_results(report, f"{where}.shared_results", data.get("shared_results"))
    check_provenance(report, f"{where}.provenance", data.get("provenance"), template, registered, root)
    check_security(report, f"{where}.security", data.get("security"))

    terminal_state = state if isinstance(state, str) and state in TERMINAL_STATES else None
    _check_pipeline_stages(report, f"{where}.pipeline_stages", data.get("pipeline_stages"))
    check_coverage_ledger(report, f"{where}.coverage_ledger", data.get("coverage_ledger") or {})
    check_submissions(report, f"{where}.submissions", data.get("submissions") or {}, experiment=False)
    check_checkpoints(report, f"{where}.checkpoints", data.get("checkpoints") or {})
    check_lifecycle_observations(
        report,
        f"{where}.lifecycle_observations",
        data.get("lifecycle_observations"),
        state,
        template,
    )
    check_resources(report, f"{where}.resources", data.get("resources"), terminal_state, template)

    if not template:
        scan_placeholders(report, where, data, skip=frozenset({"_note", "notes"}))


def check_experiment_record(report, root: Path, path: Path, template: bool) -> None:
    where = rel(root, path) if path.is_relative_to(root) else str(path)
    data = load_json(report, path)
    if data is None or not isinstance(data, dict):
        return
    require_keys(report, where, data, EXPERIMENT_REQUIRED_KEYS)
    custom = data.get("custom_algorithm") or {}
    if not isinstance(custom, dict):
        report.fail(f"{where}.custom_algorithm: must be an object")
    elif custom.get("depth_cap") != 6 or custom.get("self_rejects_depth_gt_6") is not True:
        report.fail(f"{where}: custom_algorithm must cap depth at 6 and self-reject deeper")

    check_completion_honesty(report, f"{where}.honesty", data.get("honesty"), template, root)
    statistics = data.get("statistics") or {}
    if not isinstance(statistics, dict):
        report.fail(f"{where}.statistics: must be an object")
    else:
        check_experiment_statistics(report, f"{where}.statistics", statistics, template)
    check_usefulness_metrics(report, f"{where}.usefulness_metrics", data.get("usefulness_metrics"), template)

    resources = data.get("resources") or {}
    if not isinstance(resources, dict):
        report.fail(f"{where}.resources: must be an object")
    else:
        check_resource_limits(
            report,
            f"{where}.resources.requested_vs_enforced",
            resources.get("requested_vs_enforced"),
            template,
        )
        if resources.get("missing_enforcement_is_visible") is not True:
            report.fail(f"{where}.resources.missing_enforcement_is_visible must be true")
        outcome = resources.get("outcome")
        if outcome is not None and (
            not isinstance(outcome, str) or outcome not in TERMINAL_STATES
        ):
            report.fail(f"{where}.resources.outcome {outcome!r} not in {sorted(TERMINAL_STATES)} or null")

    submissions = data.get("submissions") or {}
    if not isinstance(submissions, dict):
        report.fail(f"{where}.submissions: must be an object")
    else:
        check_submissions(report, f"{where}.submissions", submissions, experiment=True)

    segments = data.get("segments")
    if not isinstance(segments, dict):
        report.fail(f"{where}.segments: must be an object")
    else:
        if segments.get("never_pool_across_era_boundaries") is not True:
            report.fail(f"{where}.segments.never_pool_across_era_boundaries must be true")
        if segments.get("resume_mismatch_opens_new_segment") is not True:
            report.fail(f"{where}.segments.resume_mismatch_opens_new_segment must be true")

    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        report.fail(f"{where}.provenance: must be an object")
    else:
        comparison = data.get("comparison")
        classification = comparison.get("classification") if isinstance(comparison, dict) else None
        check_provenance(
            report,
            f"{where}.provenance",
            provenance,
            template,
            classification == "measured",
            root,
        )

    if not template:
        scan_placeholders(report, where, data, skip=frozenset({"notes"}))


def check_trial_inventory(report, root: Path, path: Path, require_rows: bool = False) -> None:
    where = rel(root, path) if path.is_relative_to(root) else str(path)
    text = read_text(path)
    rows = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        rows += 1
        spot = f"{where}:{line_no}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            report.fail(f"{spot}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            report.fail(f"{spot}: record must be an object")
            continue
        if require_rows:
            if record.get("_template") is True:
                report.fail(f"{spot}: a template row cannot satisfy a complete trial inventory")
            scan_placeholders(report, spot, record, skip=frozenset({"notes"}))
        for key in ("trial_id", "status", "classification", "unit_of_analysis"):
            if key not in record:
                report.fail(f"{spot}: missing trial field {key!r}")
        status = record.get("status")
        if not isinstance(status, str) or status not in TRIAL_STATUSES:
            report.fail(f"{spot}: status {status!r} not in {sorted(TRIAL_STATUSES)}")
        elif require_rows and status not in RESOLVED_TRIAL_STATUSES:
            report.fail(
                f"{spot}: a complete trial inventory cannot contain running ({status!r}) trials"
            )
        classification = record.get("classification")
        if not isinstance(classification, str) or classification not in RECORD_CLASSIFICATIONS:
            report.fail(
                f"{spot}: classification {classification!r} not in "
                f"{sorted(RECORD_CLASSIFICATIONS)}"
            )
        if record.get("unit_of_analysis") != "independent_paired_search_runs":
            report.fail(f"{spot}: unit_of_analysis must be independent_paired_search_runs")
        if "depth_cap" in record and record["depth_cap"] != 6:
            report.fail(f"{spot}: depth_cap must be 6")
    if require_rows and rows == 0:
        report.fail(f"{where}: a complete trial inventory must contain real rows")


def check_coverage_records(report, root: Path, path: Path) -> None:
    where = rel(root, path) if path.is_relative_to(root) else str(path)
    text = read_text(path)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            report.fail(f"{where}:{line_no}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            report.fail(f"{where}:{line_no}: record must be an object")
            continue
        if record.get("record_type") == "feature_rotation_pair":
            for key in (
                "valid_pair",
                "evaluated",
                "dependency_of_passing_graph",
                "feature_set",
                "filler_or_transformed_duplicate",
                "attribution_provenance",
            ):
                if key not in record:
                    report.fail(f"{where}:{line_no}: pair record missing {key!r}")
            feature_set = record.get("feature_set")
            if not isinstance(feature_set, str) or feature_set not in {"loaded", "catalogue"}:
                report.fail(f"{where}:{line_no}: feature_set must be loaded or catalogue")
            if record.get("filler_or_transformed_duplicate") is not False:
                report.fail(f"{where}:{line_no}: filler_or_transformed_duplicate must be false")
            if "depth_cap" in record and record["depth_cap"] != 6:
                report.fail(f"{where}:{line_no}: depth_cap must be 6")
        elif record.get("record_type") == "era_config_summary":
            if record.get("acceptance_weakened_for_coverage") is not False:
                report.fail(f"{where}:{line_no}: acceptance_weakened_for_coverage must be false")


def check_records(report, root: Path, record_dir: str | None = None) -> None:
    """Validate campaign records (template or filled) under a directory."""
    base = (root / record_dir) if record_dir else (root / CAMPAIGN_DIR)
    if not base.is_dir():
        report.fail(f"campaign record directory missing: {record_dir or CAMPAIGN_DIR}")
        return

    machine = load_state_machine(report, root)
    if machine is None:
        return

    complete_inventory_required = False
    campaign_path = base / "campaign.json"
    if not campaign_path.is_file():
        report.fail(f"{rel(root, campaign_path) if campaign_path.is_relative_to(root) else campaign_path}: missing")
    else:
        campaign = load_json(report, campaign_path)
        template = bool(isinstance(campaign, dict) and campaign.get("_template") is True)
        if isinstance(campaign, dict):
            statistics = campaign.get("statistics")
            if isinstance(statistics, dict) and statistics.get("complete_trial_inventory") is True:
                complete_inventory_required = True
        check_campaign_record(report, root, campaign_path, machine, template)

    for name in ("experiment_record.json",):
        path = base / name
        if path.is_file():
            experiment = load_json(report, path)
            template = bool(isinstance(experiment, dict) and experiment.get("_template") is True)
            if isinstance(experiment, dict):
                honesty = experiment.get("honesty")
                if isinstance(honesty, dict) and honesty.get("full_benchmark_completion_claimed") is True:
                    complete_inventory_required = True
            check_experiment_record(report, root, path, template)

    trial_path = base / "experiments.jsonl"
    if trial_path.is_file():
        check_trial_inventory(report, root, trial_path, require_rows=complete_inventory_required)
    elif complete_inventory_required:
        report.fail(
            f"{rel(root, trial_path) if trial_path.is_relative_to(root) else trial_path}: "
            "complete trial inventory required but the file is missing"
        )

    coverage_path = base / "coverage.jsonl"
    if coverage_path.is_file():
        check_coverage_records(report, root, coverage_path)

    benchmark_path = base / "benchmark_record.json"
    if benchmark_path.is_file():
        benchmark = load_json(report, benchmark_path)
        template = bool(isinstance(benchmark, dict) and benchmark.get("_template") is True)
        check_benchmark_record(report, root, benchmark_path, template)


def check_examples(report, root: Path) -> None:
    """Strict template validation for examples/agent-campaign."""
    base = root / CAMPAIGN_DIR
    if not base.is_dir():
        report.fail(f"{CAMPAIGN_DIR}/ missing")
        return

    required_files = (
        "README.md",
        "campaign.json",
        "research.md",
        "experiments.jsonl",
        "coverage.jsonl",
        "benchmark_record.json",
        "experiment_record.json",
        "challenger_design.md",
    )
    for name in required_files:
        if not (base / name).is_file():
            report.fail(f"{CAMPAIGN_DIR}/{name} missing")

    readme = base / "README.md"
    if readme.is_file():
        text = read_text(readme).lower()
        for needle in ("template", "in-sample", "settings.yaml", "pending", "exploratory", "state"):
            if needle not in text:
                report.fail(f"{CAMPAIGN_DIR}/README.md: missing honest-label wording {needle!r}")

    campaign = load_json(report, base / "campaign.json")
    if isinstance(campaign, dict):
        if campaign.get("_template") is not True:
            report.fail("campaign.json: must be marked \"_template\": true")
    elif campaign is not None:
        report.fail("campaign.json: top level must be an object")

    experiment = load_json(report, base / "experiment_record.json")
    if isinstance(experiment, dict) and experiment.get("_template") is not True:
        report.fail("experiment_record.json: must be marked \"_template\": true")

    benchmark = load_json(report, base / "benchmark_record.json")
    if isinstance(benchmark, dict) and benchmark.get("_template") is not True:
        report.fail("benchmark_record.json: must be marked \"_template\": true")

    design = base / "challenger_design.md"
    if design.is_file():
        text = read_text(design)
        lower = text.lower()
        if "missing api" not in lower:
            report.fail("challenger_design.md: must state the missing API instead of inventing methods")
        if "verified" not in lower:
            report.fail("challenger_design.md: must state what was verified against the installed API")
        if "not runnable" not in lower:
            report.fail("challenger_design.md: pseudocode must be labeled not runnable")
        allowed_prefixes = (
            "openforage",
            "openforage.catalog.function_loader",
            "openforage.catalog.features",
            "openforage.search_templates",
            "openforage.search_analytics",
            "openforage.templates",
        )
        for match in re.finditer(
            r"from\s+(openforage[A-Za-z0-9_.]*)\s+import|import\s+(openforage[A-Za-z0-9_.]*)",
            text,
        ):
            module = match.group(1) or match.group(2)
            if not any(module == prefix or module.startswith(prefix + ".") for prefix in allowed_prefixes):
                report.fail(f"challenger_design.md: import {module!r} is outside the verified public allowlist")

    check_records(report, root, CAMPAIGN_DIR)
