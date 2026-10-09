"""Protocol, statistics, provenance, security, and usefulness validation.

Phase awareness lives here: an early bootstrapping/research record may carry
unknown or not-yet-recorded protocol fields *with a stated reason*, while a
registered or completed claim must carry real values, real types, and
referenced evidence.
"""

from __future__ import annotations

from pathlib import Path

from .common import (
    is_calendar_date,
    is_placeholder,
    is_positive_integer,
    is_real_string,
    load_structured_json,
    passing_result_problem,
    resolve_repo_artifact,
)

DIRECTIONS = {"increase", "decrease", "two-sided"}
LOOK_KINDS = {"fixed_horizon", "sequential"}
RECORD_CLASSIFICATIONS = {"measured", "exploratory"}
AVAILABILITY_VALUES = {"available", "unavailable", "pending", "unknown"}

PREREGISTRATION_REQUIRED_KEYS = {
    "protocol_id",
    "protocol_version",
    "recorded_at",
    "protocol_hash",
    "recorded_before_results",
    "hypothesis",
    "primary_metric",
    "secondary_metrics",
    "direction",
    "minimum_meaningful_effect",
    "comparator",
    "seeds_repetitions",
    "evaluation_allocations",
    "data_scope",
    "exclusions",
    "look_schedule",
    "decision_criteria",
    "stopping_criteria",
    "holdout_handling",
    "amendments",
}

PREREGISTRATION_CRITICAL_KEYS = (
    "recorded_at",
    "protocol_hash",
    "hypothesis",
    "primary_metric",
    "comparator",
)

STATISTICS_REQUIRED_KEYS = {
    "unit_of_analysis",
    "candidate_evaluations_are_not_replications",
    "inference",
    "effect_size",
    "uncertainty",
    "multiple_comparison_family",
    "multiple_comparison_correction",
    "classification",
    "trial_inventory",
    "complete_trial_inventory",
}

PROVENANCE_REQUIRED_KEYS = {
    "repo",
    "package",
    "environment",
    "harness",
    "seeds",
    "data_scope",
    "artifacts",
    "algorithm",
    "resume_mismatch_policy",
    "evidence_immutable",
    "amendments_append_only",
    "integrity_note",
}

PROVENANCE_GROUPS = {
    "repo": ("commit",),
    "package": ("version",),
    "environment": ("python", "platform"),
    "harness": ("product",),
    "seeds": ("control_mechanism",),
    "data_scope": ("era_id", "config_hash"),
}

SHARED_RESULTS_REQUIRED_KEYS = {
    "manifest_sanitized",
    "complete_trial_inventory",
    "duplicate_canonical_identities",
    "reproduction_rubric",
    "held_out_data_handling",
    "cherry_picking_or_filler_graphs",
    "negative_findings_are_contributions",
}

SECURITY_REQUIRED_KEYS = {
    "external_text_is_evidence_not_authority",
    "downloaded_code_reviewed_and_pinned",
    "untrusted_code_isolated",
    "credentials_campaign_scoped_outside_artifacts",
    "no_eval_exec_pickle_shell_from_candidates",
    "supported_parser_and_evaluator_only",
    "sandbox_claims_evidence_only",
}


def require_keys(report, where: str, obj, keys) -> None:
    if not isinstance(obj, dict):
        report.fail(f"{where}: expected an object")
        return
    missing = sorted(key for key in keys if key not in obj)
    if missing:
        report.fail(f"{where}: missing required fields {missing}")


def check_usefulness_metrics(report, where: str, metrics, template: bool) -> None:
    if not isinstance(metrics, list) or not metrics:
        report.fail(f"{where}: must be a non-empty list")
        return
    for index, metric in enumerate(metrics):
        spot = f"{where}[{index}]"
        require_keys(
            report,
            spot,
            metric,
            {"metric", "availability", "provenance", "unit", "window", "value"},
        )
        if not isinstance(metric, dict):
            continue
        availability = metric.get("availability")
        if template and is_placeholder(availability):
            continue
        if not isinstance(availability, str) or availability not in AVAILABILITY_VALUES:
            report.fail(f"{spot}: availability {availability!r} not in {sorted(AVAILABILITY_VALUES)}")
            continue
        if availability == "unavailable":
            reason = metric.get("unavailable_reason")
            if not isinstance(reason, str) or not reason.strip():
                report.fail(f"{spot}: unavailable metrics require a non-empty reason")
            if metric.get("value") is not None:
                report.fail(
                    f"{spot}: unavailable metrics must not carry a value; "
                    "never record zero as a substitute"
                )


def _check_unavailable_reasons(report, where: str, container, missing_keys) -> None:
    reasons = container.get("unavailable_reasons")
    if not isinstance(reasons, dict):
        report.fail(
            f"{where}: exploratory/early records must state unavailable_reasons for "
            f"not-yet-recorded critical fields {sorted(missing_keys)}"
        )
        return
    for key in missing_keys:
        reason = reasons.get(key)
        if not isinstance(reason, str) or not reason.strip():
            report.fail(f"{where}.unavailable_reasons: missing a reason for {key!r}")


def check_preregistration(report, where: str, prereg, template: bool, registered: bool) -> None:
    require_keys(report, where, prereg, PREREGISTRATION_REQUIRED_KEYS)
    if not isinstance(prereg, dict):
        return

    missing_critical = []
    for key in PREREGISTRATION_CRITICAL_KEYS:
        value = prereg.get(key)
        if registered:
            if not is_real_string(value):
                report.fail(
                    f"{where}: a registered/completed claim requires a real {key!r}; "
                    f"found {value!r}"
                )
        elif not template and (
            value in (None, "", [], {})
            or is_placeholder(value)
            or (
                isinstance(value, str)
                and value.strip().lower() in {"unknown", "missing", "not_recorded", "n/a"}
            )
        ):
            missing_critical.append(key)

    if registered:
        if prereg.get("recorded_before_results") is not True:
            report.fail(f"{where}: recorded_before_results must be true for a registered claim")
    elif not template:
        if prereg.get("recorded_before_results") is not True:
            missing_critical.append("recorded_before_results")
        if missing_critical:
            _check_unavailable_reasons(report, where, prereg, missing_critical)

    direction = prereg.get("direction")
    if template and is_placeholder(direction):
        pass
    elif registered:
        if not isinstance(direction, str) or direction not in DIRECTIONS:
            report.fail(f"{where}: direction {direction!r} not in {sorted(DIRECTIONS)}")
    elif not isinstance(direction, str):
        report.fail(f"{where}: direction must be a string from {sorted(DIRECTIONS)} or an explicit unknown marker")
    elif direction not in DIRECTIONS and direction.lower() not in {
        "unknown",
        "not_recorded",
        "n/a",
    }:
        report.fail(f"{where}: direction {direction!r} not in {sorted(DIRECTIONS)}")

    schedule = prereg.get("look_schedule")
    require_keys(
        report,
        f"{where}.look_schedule",
        schedule,
        {"kind", "looks", "sequential_method", "sequential_justification"},
    )
    if isinstance(schedule, dict):
        kind = schedule.get("kind")
        if registered:
            if not isinstance(kind, str) or kind not in LOOK_KINDS:
                report.fail(f"{where}.look_schedule.kind {kind!r} not in {sorted(LOOK_KINDS)}")
        elif not is_placeholder(kind):
            if not isinstance(kind, str):
                report.fail(
                    f"{where}.look_schedule.kind must be a string from {sorted(LOOK_KINDS)} "
                    "or an explicit unknown marker"
                )
            elif kind not in LOOK_KINDS and kind.lower() not in {"unknown", "not_recorded", "n/a"}:
                report.fail(f"{where}.look_schedule.kind {kind!r} not in {sorted(LOOK_KINDS)}")
        if kind == "sequential" and not (
            schedule.get("sequential_method") and schedule.get("sequential_justification")
        ):
            report.fail(
                f"{where}.look_schedule: a sequential look schedule requires a method "
                "and justification"
            )

    seeds = prereg.get("seeds_repetitions")
    if isinstance(seeds, dict) and seeds.get("predeclared") is True:
        if not is_real_string(seeds.get("seed_set_id")):
            report.fail(f"{where}.seeds_repetitions: predeclared seeds require a real seed_set_id")


def check_statistics(report, where: str, statistics, template: bool, completed: bool) -> None:
    require_keys(report, where, statistics, STATISTICS_REQUIRED_KEYS)
    if not isinstance(statistics, dict):
        return
    if statistics.get("unit_of_analysis") != "independent_paired_search_runs":
        report.fail(
            f"{where}: unit_of_analysis must be 'independent_paired_search_runs' "
            "(candidate evaluations are not independent replications)"
        )
    if statistics.get("candidate_evaluations_are_not_replications") is not True:
        report.fail(f"{where}: candidate_evaluations_are_not_replications must be true")
    inference = statistics.get("inference")
    require_keys(report, f"{where}.inference", inference, {"method", "assumptions", "data_window"})
    classification = statistics.get("classification")
    if not isinstance(classification, str) or classification not in RECORD_CLASSIFICATIONS:
        report.fail(f"{where}: classification {classification!r} not in {sorted(RECORD_CLASSIFICATIONS)}")
    complete_inventory = statistics.get("complete_trial_inventory")
    if not isinstance(complete_inventory, bool):
        report.fail(f"{where}: complete_trial_inventory must be an explicit boolean")
    trial_inventory = statistics.get("trial_inventory")
    if not template and not is_real_string(trial_inventory):
        report.fail(f"{where}: trial_inventory must name the inventory artifact")
    if completed and complete_inventory is not True:
        report.fail(
            f"{where}: a completed registered claim requires the complete trial "
            "inventory to be present"
        )


def check_experiment_statistics(report, where: str, statistics, template: bool) -> None:
    """Validate the experiment-record statistics shape.

    The experiment record uses the same unit-of-analysis rule but carries its
    own horizon and trial-inventory reference fields instead of the campaign
    manifest's classification block.
    """
    require_keys(
        report,
        where,
        statistics,
        {
            "unit_of_analysis",
            "candidate_evaluations_are_not_replications",
            "inference",
            "horizon",
            "same_data_reruns_are_not_new_oos",
            "trial_inventory_ref",
        },
    )
    if not isinstance(statistics, dict):
        return
    if statistics.get("unit_of_analysis") != "independent_paired_search_runs":
        report.fail(f"{where}: unit_of_analysis must be 'independent_paired_search_runs'")
    if statistics.get("candidate_evaluations_are_not_replications") is not True:
        report.fail(f"{where}: candidate_evaluations_are_not_replications must be true")
    inference = statistics.get("inference")
    require_keys(report, f"{where}.inference", inference, {"method", "assumptions", "data_window"})
    horizon = statistics.get("horizon")
    if not (template and is_placeholder(horizon)):
        if not isinstance(horizon, str) or horizon not in LOOK_KINDS:
            report.fail(f"{where}: horizon {horizon!r} not in {sorted(LOOK_KINDS)}")
        elif horizon == "sequential" and not statistics.get("sequential_justification"):
            report.fail(f"{where}: sequential horizon requires a justification")
    if statistics.get("same_data_reruns_are_not_new_oos") is not True:
        report.fail(f"{where}: same_data_reruns_are_not_new_oos must be true")
    trial_ref = statistics.get("trial_inventory_ref")
    if not template and not is_real_string(trial_ref):
        report.fail(f"{where}: trial_inventory_ref must name the trial inventory")


def _check_provenance_group(report, where: str, key: str, group, template: bool, registered: bool) -> None:
    spot = f"{where}.{key}"
    if not isinstance(group, dict):
        report.fail(f"{spot}: must be an object with real fields, not {group!r}")
        return
    missing = [
        field
        for field in PROVENANCE_GROUPS[key]
        if group.get(field) in (None, "", [], {})
        or (
            not template
            and not is_real_string(group.get(field))
        )
    ]
    if template:
        return
    if registered:
        for field in missing:
            report.fail(
                f"{spot}: a registered/completed claim requires a real {field!r}"
            )
    elif missing:
        reason = group.get("unavailable_reason")
        if not isinstance(reason, str) or not reason.strip():
            report.fail(
                f"{spot}: unavailable metadata must state unavailable_reason for "
                f"{sorted(missing)}"
            )


def check_provenance(report, where: str, provenance, template: bool, registered: bool, root: Path) -> None:
    require_keys(report, where, provenance, PROVENANCE_REQUIRED_KEYS)
    if not isinstance(provenance, dict):
        return
    if provenance.get("resume_mismatch_policy") != "new_segment_and_incomplete_baseline":
        report.fail(
            f"{where}: resume_mismatch_policy must be "
            "'new_segment_and_incomplete_baseline'"
        )
    if provenance.get("evidence_immutable") is not True:
        report.fail(f"{where}: evidence_immutable must be true")
    if provenance.get("amendments_append_only") is not True:
        report.fail(f"{where}: amendments_append_only must be true")

    for key in PROVENANCE_GROUPS:
        _check_provenance_group(report, where, key, provenance.get(key), template, registered)

    artifacts = provenance.get("artifacts")
    if not isinstance(artifacts, list):
        report.fail(f"{where}.artifacts: must be a list of artifact records")
    elif not template:
        real_artifacts = [
            item
            for item in artifacts
            if isinstance(item, dict)
            and is_real_string(item.get("path"))
            and is_real_string(item.get("sha256"))
        ]
        if registered and not real_artifacts:
            report.fail(
                f"{where}.artifacts: a registered/completed claim requires at least one "
                "artifact with a real path and hash"
            )
        if not registered and not real_artifacts:
            ok = all(
                isinstance(item, dict)
                and isinstance(item.get("unavailable_reason"), str)
                and item.get("unavailable_reason").strip()
                for item in artifacts
            ) and bool(artifacts)
            if not ok:
                report.fail(
                    f"{where}.artifacts: unavailable artifacts must state unavailable_reason"
                )

    algorithm = provenance.get("algorithm")
    if not isinstance(algorithm, dict):
        report.fail(f"{where}.algorithm: must be an object")
        return
    if "identity" not in algorithm:
        report.fail(f"{where}.algorithm: missing 'identity'")
    if registered and not is_real_string(algorithm.get("identity")):
        report.fail(f"{where}.algorithm: a registered/completed claim requires a real identity")
    if not registered and not is_real_string(algorithm.get("identity")):
        reason = algorithm.get("unavailable_reason")
        if not isinstance(reason, str) or not reason.strip():
            report.fail(
                f"{where}.algorithm: unavailable algorithm metadata must state unavailable_reason"
            )
    if algorithm.get("ga_reference_implementation_present") is True:
        if not is_real_string(algorithm.get("identity")):
            report.fail(
                f"{where}.algorithm: claiming a reference implementation requires an "
                "identified implementation"
            )
        evidence_ref = algorithm.get("evidence_ref")
        if not is_real_string(evidence_ref) or resolve_repo_artifact(root, evidence_ref) is None:
            report.fail(
                f"{where}.algorithm: a present reference implementation requires an "
                "existing evidence_ref inside the repository"
            )


def check_shared_results(report, where: str, shared) -> None:
    require_keys(report, where, shared, SHARED_RESULTS_REQUIRED_KEYS)
    if not isinstance(shared, dict):
        return
    if shared.get("cherry_picking_or_filler_graphs") is not False:
        report.fail(f"{where}: cherry_picking_or_filler_graphs must be false")
    if shared.get("negative_findings_are_contributions") is not True:
        report.fail(f"{where}: negative_findings_are_contributions must be true")
    for key in ("manifest_sanitized", "complete_trial_inventory", "duplicate_canonical_identities"):
        if not isinstance(shared.get(key), bool):
            report.fail(f"{where}.{key} must be an explicit boolean")


def check_security(report, where: str, security) -> None:
    require_keys(report, where, security, SECURITY_REQUIRED_KEYS)
    if not isinstance(security, dict):
        return
    for key in sorted(SECURITY_REQUIRED_KEYS):
        if not isinstance(security.get(key), bool):
            report.fail(f"{where}.{key} must be an explicit boolean (true or false)")


def check_completion_honesty(report, where: str, honesty, template: bool, root: Path) -> None:
    """Template-only honesty flags stay false; real claims need real evidence."""
    if not isinstance(honesty, dict):
        report.fail(f"{where}: must be an object")
        return
    full = honesty.get("full_benchmark_completion_claimed")
    adapter = honesty.get("verified_harness_adapter_claimed")
    if not isinstance(full, bool) or not isinstance(adapter, bool):
        report.fail(f"{where}: completion/adapter claim flags must be explicit booleans")
        return
    if template:
        if full is not False:
            report.fail(f"{where}: a template must not claim full benchmark completion")
        if adapter is not False:
            report.fail(f"{where}: a template must not claim a verified harness adapter")
    if full is True and not template:
        evidence = honesty.get("full_benchmark_completion_evidence")
        if not isinstance(evidence, dict):
            report.fail(f"{where}: full_benchmark_completion_claimed requires evidence")
        else:
            target = evidence.get("target_completed_simulations")
            completed = evidence.get("completed_evaluations")
            identity = evidence.get("reference_id")
            if not is_positive_integer(target):
                report.fail(
                    f"{where}: completion evidence target_completed_simulations must be an "
                    "explicit positive integer (packet reference target 100000)"
                )
            if not is_positive_integer(completed):
                report.fail(
                    f"{where}: completion evidence completed_evaluations must be a positive "
                    "whole number; zero and fractional counts are not completions"
                )
            elif is_positive_integer(target) and completed < target:
                report.fail(
                    f"{where}: completed_evaluations ({completed}) does not meet the target "
                    f"({target})"
                )
            if not is_real_string(identity):
                report.fail(f"{where}: completion evidence requires a real reference_id")
            if not is_calendar_date(evidence.get("completed_at")):
                report.fail(
                    f"{where}: completion evidence requires a real calendar date completed_at"
                )
            structured, problem = load_structured_json(
                evidence.get("evidence_artifact"), root
            )
            if structured is None:
                report.fail(f"{where}: completion evidence_artifact {problem}")
            else:
                artifact_identity = next(
                    (
                        structured.get(key)
                        for key in ("reference_id", "baseline_id", "id")
                        if is_real_string(structured.get(key))
                    ),
                    None,
                )
                artifact_target = structured.get("target_completed_simulations")
                artifact_count = structured.get("observed_completed_simulations")
                if artifact_count is None:
                    artifact_count = structured.get("completed_evaluations")
                if artifact_identity is None or artifact_identity != identity:
                    report.fail(
                        f"{where}: completion evidence reference_id does not match the "
                        "referenced benchmark artifact"
                    )
                if artifact_target != target:
                    report.fail(
                        f"{where}: completion evidence target does not match the referenced "
                        "benchmark artifact"
                    )
                if artifact_count != completed:
                    report.fail(
                        f"{where}: completion evidence count does not match the referenced "
                        "benchmark artifact"
                    )
    if adapter is True and not template:
        evidence = honesty.get("harness_adapter_evidence")
        if not isinstance(evidence, dict):
            report.fail(f"{where}: verified_harness_adapter_claimed requires evidence")
        else:
            for key in ("product", "product_version", "test_id"):
                if not is_real_string(evidence.get(key)):
                    report.fail(f"{where}: adapter evidence requires a real {key!r}")
            problem = passing_result_problem(evidence)
            if problem:
                report.fail(f"{where}: adapter evidence {problem}")
            scope = evidence.get("scope")
            if not isinstance(scope, dict) or not all(
                is_real_string(scope.get(key)) for key in ("surface", "os", "lifecycle")
            ):
                report.fail(
                    f"{where}: adapter evidence scope must be an object with "
                    "surface/os/lifecycle"
                )
            if not is_calendar_date(evidence.get("tested_at")):
                report.fail(f"{where}: adapter evidence requires a real calendar date tested_at")
            artifact = evidence.get("evidence_artifact")
            if not is_real_string(artifact) or resolve_repo_artifact(root, artifact) is None:
                report.fail(
                    f"{where}: adapter evidence artifact must exist inside the repository"
                )
    if honesty.get("classification_is_not_measurement_presence") is not True:
        report.fail(
            f"{where}: honesty.classification_is_not_measurement_presence must be true "
            "(exploratory never means unmeasured)"
        )
