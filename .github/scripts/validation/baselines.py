"""Frozen-reference and compatible-baseline availability validation.

Availability is modeled explicitly: the 100,000 figure is a target, the
observed completed count is separate, and a missing reference keeps null
id/hash/observed values plus a stated reason.
"""

from __future__ import annotations

from pathlib import Path

from .common import (
    is_calendar_date,
    is_nonnegative_number,
    is_placeholder,
    is_real_string,
    load_json,
    load_structured_json,
    rel,
)
from .protocol import require_keys

FROZEN_AVAILABILITY = {"available", "unavailable", "pending", "unknown"}
COMPATIBLE_BASELINE_STATUSES = {"found", "created", "incomplete", "missing"}


def check_frozen_reference(report, where: str, obj, template: bool, id_keys=("id",)) -> None:
    required = {
        "availability",
        "unavailable_reason",
        "target_completed_simulations",
        "observed_completed_simulations",
        "frozen",
    } | set(id_keys)
    require_keys(report, where, obj, required)
    if not isinstance(obj, dict):
        return
    target = obj.get("target_completed_simulations")
    if target != 100000:
        report.fail(
            f"{where}: target_completed_simulations must be the 100000 budget; "
            f"found {target!r}"
        )
    observed = obj.get("observed_completed_simulations")
    if observed is not None and not is_nonnegative_number(observed):
        report.fail(f"{where}: observed_completed_simulations must be numeric or null")
    availability = obj.get("availability")
    if template and is_placeholder(availability):
        return
    if not isinstance(availability, str) or availability not in FROZEN_AVAILABILITY:
        report.fail(
            f"{where}: availability {availability!r} not in {sorted(FROZEN_AVAILABILITY)}"
        )
        return
    ids = {key: obj.get(key) for key in id_keys}
    if availability == "available":
        for key, value in ids.items():
            if not is_real_string(value) and not template:
                report.fail(f"{where}: availability=available requires a real {key!r}")
        if not is_real_string(obj.get("hash")) and not template:
            report.fail(f"{where}: availability=available requires a real hash")
        if obj.get("frozen") is not True:
            report.fail(f"{where}: availability=available requires frozen=true")
        if observed is None or not is_nonnegative_number(observed):
            report.fail(
                f"{where}: availability=available requires a numeric observed completed count"
            )
    else:
        reason = obj.get("unavailable_reason")
        if not (isinstance(reason, str) and reason.strip()):
            report.fail(
                f"{where}: availability={availability} requires an unavailable_reason"
            )
        for key, value in ids.items():
            if value is not None:
                report.fail(f"{where}: {key} must be null when the reference is unavailable")
        if obj.get("hash") is not None:
            report.fail(f"{where}: hash must be null when the reference is unavailable")
        if obj.get("frozen") is not False:
            report.fail(f"{where}: frozen must be false when the reference is unavailable")
        if observed is not None:
            report.fail(
                f"{where}: observed_completed_simulations must be null when unavailable"
            )


def check_frozen_baseline(report, where: str, baseline, template: bool) -> None:
    check_frozen_reference(report, where, baseline, template, id_keys=("id",))
    if not isinstance(baseline, dict):
        return
    algorithm = baseline.get("algorithm_identity")
    if not template and not is_real_string(algorithm):
        report.fail(
            f"{where}: algorithm_identity must be a recorded identity or a "
            "stated unavailable marker; found {algorithm!r}"
        )


def check_compatible_baseline(report, where: str, baseline, template: bool) -> None:
    require_keys(
        report,
        where,
        baseline,
        {
            "status",
            "status_reason",
            "baseline_id",
            "lineage",
            "config_frozen_before_run",
            "evidence_frozen_after_run",
            "additional_paired_runs_get_distinct_ids",
            "silent_overwrite",
        },
    )
    if not isinstance(baseline, dict):
        return
    if baseline.get("silent_overwrite") is not False:
        report.fail(f"{where}: silent_overwrite must be false")
    if baseline.get("additional_paired_runs_get_distinct_ids") is not True:
        report.fail(f"{where}: additional paired baseline runs must get distinct ids")
    status = baseline.get("status")
    if template and is_placeholder(status):
        return
    if not isinstance(status, str) or status not in COMPATIBLE_BASELINE_STATUSES:
        report.fail(
            f"{where}: status {status!r} not in {sorted(COMPATIBLE_BASELINE_STATUSES)}"
        )
        return
    baseline_id = baseline.get("baseline_id")
    if status in {"found", "created"}:
        if not is_real_string(baseline_id):
            report.fail(f"{where}: status={status} requires a real baseline_id")
        if baseline.get("config_frozen_before_run") is not True:
            report.fail(f"{where}: status={status} requires config_frozen_before_run=true")
        if baseline.get("evidence_frozen_after_run") is not True:
            report.fail(f"{where}: status={status} requires evidence_frozen_after_run=true")
    elif status == "incomplete":
        if not is_real_string(baseline_id):
            report.fail(f"{where}: status=incomplete requires a real baseline_id")
        if baseline.get("config_frozen_before_run") is not True:
            report.fail(f"{where}: status=incomplete requires config_frozen_before_run=true")
        if not isinstance(baseline.get("evidence_frozen_after_run"), bool):
            report.fail(f"{where}: status=incomplete requires an explicit evidence_frozen_after_run")
        if not is_real_string(baseline.get("status_reason")):
            report.fail(f"{where}: status=incomplete requires a status_reason")
    else:
        if baseline_id is not None:
            report.fail(f"{where}: a missing compatible baseline must have a null baseline_id")
        if baseline.get("config_frozen_before_run") is not None:
            report.fail(f"{where}: a missing compatible baseline must not claim a frozen config")
        if baseline.get("evidence_frozen_after_run") is not None:
            report.fail(f"{where}: a missing compatible baseline must not claim frozen evidence")
        if not is_real_string(baseline.get("status_reason")):
            report.fail(f"{where}: a missing compatible baseline requires a status_reason")


def _check_independent_reverification(report, where: str, data, root: Path) -> None:
    """A real re-verification claim needs structured, matching evidence."""
    evidence = data.get("independent_reverification_evidence")
    if not isinstance(evidence, dict):
        report.fail(
            f"{where}: independently_reverified requires "
            "independent_reverification_evidence"
        )
        return
    if not is_real_string(evidence.get("reverifier")):
        report.fail(f"{where}: independent evidence requires a real reverifier")
    if not is_calendar_date(evidence.get("reverified_at")):
        report.fail(
            f"{where}: independent evidence requires a real calendar date reverified_at"
        )
    structured, problem = load_structured_json(evidence.get("evidence_artifact"), root)
    if structured is None:
        report.fail(f"{where}: independent re-verification evidence_artifact {problem}")
        return
    record_id = data.get("baseline_id")
    artifact_id = next(
        (
            structured.get(key)
            for key in ("baseline_id", "reference_id", "id")
            if is_real_string(structured.get(key))
        ),
        None,
    )
    record_count = data.get("observed_completed_simulations")
    artifact_count = structured.get("observed_completed_simulations")
    if artifact_count is None:
        artifact_count = structured.get("completed_evaluations")
    if artifact_id is None or artifact_id != record_id:
        report.fail(
            f"{where}: independent evidence baseline identity does not match the "
            "benchmark record"
        )
    if artifact_count != record_count:
        report.fail(
            f"{where}: independent evidence completed count does not match the "
            "benchmark record"
        )


def check_benchmark_record(report, root: Path, path: Path, template: bool) -> None:
    where = rel(root, path) if path.is_relative_to(root) else str(path)
    data = load_json(report, path)
    if data is None or not isinstance(data, dict):
        return
    check_frozen_reference(report, where, data, template, id_keys=("baseline_id",))
    if data.get("completion_claim") != "frozen_reference_only":
        report.fail(f"{where}: completion_claim must be 'frozen_reference_only'")
    if data.get("independently_reverified") is True:
        if template:
            report.fail(f"{where}: a template must not claim independent re-verification")
        else:
            _check_independent_reverification(report, where, data, root)
    algorithm = data.get("algorithm_identity")
    require_keys(
        report,
        f"{where}.algorithm_identity",
        algorithm,
        {"recorded_algorithm", "verified_installed_comparator", "ga_reference_implementation_present"},
    )
    if isinstance(algorithm, dict):
        if algorithm.get("verified_installed_comparator") != "random_weighted":
            report.fail(f"{where}.algorithm_identity: verified_installed_comparator must be random_weighted")
        if algorithm.get("ga_reference_implementation_present") is True:
            if not is_real_string(algorithm.get("recorded_algorithm")):
                report.fail(
                    f"{where}.algorithm_identity: claiming a present implementation "
                    "requires an identified recorded_algorithm"
                )
            if not is_real_string(algorithm.get("evidence_ref")):
                report.fail(
                    f"{where}.algorithm_identity: claiming a present implementation "
                    "requires evidence_ref"
                )
    creation = data.get("compatible_baseline_creation")
    require_keys(
        report,
        f"{where}.compatible_baseline_creation",
        creation,
        {"required_when_no_compatible_baseline_exists", "config_frozen_before_run",
         "evidence_frozen_after_run", "silent_overwrite"},
    )
    if isinstance(creation, dict):
        if creation.get("silent_overwrite") is not False:
            report.fail(f"{where}.compatible_baseline_creation.silent_overwrite must be false")
        if creation.get("required_when_no_compatible_baseline_exists") is not True:
            report.fail(
                f"{where}.compatible_baseline_creation."
                "required_when_no_compatible_baseline_exists must be true"
            )
    require_keys(
        report,
        f"{where}.checkpoint_reconciliation",
        data.get("checkpoint_reconciliation"),
        {"attempted", "completed", "in_flight", "failed"},
    )
