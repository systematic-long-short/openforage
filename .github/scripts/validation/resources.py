"""Execution-resource, lifecycle, accounting, coverage, and submission checks.

Resource claims are numeric and typed; an enforced claim needs a mechanism
and observed evidence, while an unknown requested control stays explicit with
a reason. Live work is rejected for held and terminal states, and terminal
outcome/report/relaunch flags must agree.
"""

from __future__ import annotations

from .common import is_iso_datetime, is_nonnegative_number, is_real_string
from .protocol import require_keys
from .state_machine import HOLD_STATES, TERMINAL_STATES

RESOURCE_KINDS = {
    "cpu_cores",
    "ram_bytes",
    "storage_bytes",
    "wall_time_seconds",
    "worker_count",
    "completed_simulations",
}


def check_resource_limits(report, where: str, limits, template: bool) -> None:
    if not isinstance(limits, list) or not limits:
        report.fail(f"{where}: must be a non-empty list")
        return
    seen = set()
    for index, limit in enumerate(limits):
        spot = f"{where}[{index}]"
        require_keys(
            report,
            spot,
            limit,
            {
                "resource",
                "requested",
                "enforced",
                "enforcement_mechanism",
                "observed",
                "stop_behavior",
            },
        )
        if not isinstance(limit, dict):
            continue
        resource = limit.get("resource")
        if not isinstance(resource, str):
            report.fail(f"{spot}: resource kind must be a string; found {resource!r}")
        elif resource not in RESOURCE_KINDS:
            report.fail(f"{spot}: unknown resource kind {resource!r}")
        elif resource in seen:
            report.fail(f"{spot}: duplicate resource kind {resource!r}")
        else:
            seen.add(resource)

        requested = limit.get("requested")
        enforced = limit.get("enforced")
        observed = limit.get("observed")
        if not isinstance(observed, bool):
            report.fail(f"{spot}: observed must be an explicit boolean")
        if requested is not None and not is_nonnegative_number(requested):
            report.fail(f"{spot}: requested must be a non-negative number or null")
        if requested is None and not template:
            reason = limit.get("requested_unavailable_reason")
            if not (isinstance(reason, str) and reason.strip()):
                report.fail(
                    f"{spot}: an unknown/unavailable requested control must state "
                    "requested_unavailable_reason"
                )
        if enforced is not None:
            if not is_nonnegative_number(enforced):
                report.fail(f"{spot}: enforced must be a non-negative number or null")
            if not is_real_string(limit.get("enforcement_mechanism")):
                report.fail(
                    f"{spot}: an enforced claim requires a real enforcement_mechanism"
                )
            if observed is not True:
                report.fail(
                    f"{spot}: an enforced claim requires observed=true evidence"
                )
            if not is_real_string(limit.get("stop_behavior")):
                report.fail(f"{spot}: an enforced claim requires stop_behavior")
            if is_nonnegative_number(requested) and enforced > requested:
                report.fail(f"{spot}: enforced ({enforced}) must not exceed requested ({requested})")
    missing = RESOURCE_KINDS - seen
    if missing and not template:
        report.fail(f"{where}: missing resource kinds {sorted(missing)}")


def check_resources(report, where: str, resources, terminal_state: str | None, template: bool) -> None:
    require_keys(
        report,
        where,
        resources,
        {"preflight_calibration", "limits", "missing_enforcement_is_visible", "outcome", "terminal_report"},
    )
    if not isinstance(resources, dict):
        return
    calibration = resources.get("preflight_calibration")
    require_keys(report, f"{where}.preflight_calibration", calibration, {"performed", "artifact"})
    if isinstance(calibration, dict):
        performed = calibration.get("performed")
        if not isinstance(performed, bool):
            report.fail(f"{where}.preflight_calibration.performed must be an explicit boolean")
        if performed is True and not template and not is_real_string(calibration.get("artifact")):
            report.fail(
                f"{where}.preflight_calibration: a performed calibration requires an artifact"
            )
    check_resource_limits(report, f"{where}.limits", resources.get("limits"), template)
    if resources.get("missing_enforcement_is_visible") is not True:
        report.fail(f"{where}: missing_enforcement_is_visible must be true")

    outcome = resources.get("outcome")
    terminal_report = resources.get("terminal_report")
    require_keys(
        report,
        f"{where}.terminal_report",
        terminal_report,
        {"path", "pending_outcomes_preserved", "relaunch_prevented"},
    )
    if terminal_state is not None:
        if outcome != terminal_state:
            report.fail(
                f"{where}: terminal state {terminal_state!r} requires outcome "
                f"{terminal_state!r}; found {outcome!r}"
            )
        if isinstance(terminal_report, dict):
            if terminal_report.get("relaunch_prevented") is not True:
                report.fail(f"{where}.terminal_report.relaunch_prevented must be true in a terminal state")
            if terminal_report.get("pending_outcomes_preserved") is not True:
                report.fail(
                    f"{where}.terminal_report.pending_outcomes_preserved must be true "
                    "in a terminal state"
                )
            if not template and not is_real_string(terminal_report.get("path")):
                report.fail(
                    f"{where}.terminal_report.path must name the written report in a terminal state"
                )
    elif not isinstance(outcome, str):
        if outcome is not None:
            report.fail(
                f"{where}: outcome must be a state id string or null; found {outcome!r}"
            )
    elif outcome in TERMINAL_STATES:
        report.fail(
            f"{where}: outcome {outcome!r} contradicts a non-terminal campaign state"
        )
    elif isinstance(terminal_report, dict) and terminal_report.get("relaunch_prevented") is True:
        report.fail(
            f"{where}: a non-terminal campaign must not claim relaunch_prevented"
        )


def check_lifecycle_observations(report, where: str, observations, state, template: bool) -> None:
    require_keys(
        report,
        where,
        observations,
        {"worker_running", "triggers_enabled", "observed_at", "source"},
    )
    if not isinstance(observations, dict):
        return
    worker_running = observations.get("worker_running")
    triggers_enabled = observations.get("triggers_enabled")
    if not isinstance(worker_running, bool):
        report.fail(f"{where}: worker_running must be an explicit boolean")
    if not isinstance(triggers_enabled, bool):
        report.fail(f"{where}: triggers_enabled must be an explicit boolean")
    if not template:
        if not is_iso_datetime(observations.get("observed_at")):
            report.fail(f"{where}: observed_at must be a valid ISO-8601 datetime")
        if not is_real_string(observations.get("source")):
            report.fail(f"{where}: source must name how the lifecycle was observed")
    if isinstance(state, str) and state in HOLD_STATES | TERMINAL_STATES:
        if worker_running is True:
            report.fail(
                f"{where}: contradictory live work — state {state!r} must not have a "
                "running worker"
            )
        if triggers_enabled is True:
            report.fail(
                f"{where}: contradictory live work — state {state!r} must not keep "
                "recurring triggers enabled"
            )


def check_coverage_ledger(report, where: str, coverage) -> None:
    for key in (
        "catalogue_features",
        "loaded_features",
        "valid_pairs",
        "evaluated_pairs",
        "dependency_pairs",
        "unevaluated_gaps",
        "filler_or_duplicate_graphs",
        "acceptance_weakened_for_coverage",
    ):
        if key not in coverage:
            report.fail(f"{where}: missing {key!r}")
    if not isinstance(coverage, dict):
        return
    for key in (
        "catalogue_features",
        "loaded_features",
        "valid_pairs",
        "evaluated_pairs",
        "dependency_pairs",
        "unevaluated_gaps",
    ):
        value = coverage.get(key)
        if value is not None and not is_nonnegative_number(value):
            report.fail(f"{where}.{key}: must be a non-negative number or null")
    if coverage.get("filler_or_duplicate_graphs") is not False:
        report.fail(f"{where}.filler_or_duplicate_graphs must be false")
    if coverage.get("acceptance_weakened_for_coverage") is not False:
        report.fail(f"{where}.acceptance_weakened_for_coverage must be false")


def check_submissions(report, where: str, submissions, experiment: bool) -> None:
    for key in ("submitted", "pending", "accepted", "rejected", "ambiguous"):
        if key not in submissions:
            report.fail(f"{where}: missing {key!r} (ambiguous is neither accepted nor rejected)")
            continue
        value = submissions.get(key)
        if value is not None and not is_nonnegative_number(value):
            report.fail(f"{where}.{key}: must be a non-negative number or null")
    if not isinstance(submissions, dict):
        return
    if experiment:
        if submissions.get("pending_is_not_accepted") is not True:
            report.fail(f"{where}: submissions.pending_is_not_accepted must be true")
        if submissions.get("ambiguous_is_neither_accepted_nor_rejected") is not True:
            report.fail(f"{where}: ambiguous submissions require the explicit rule flag")
    else:
        if submissions.get("ambiguous_never_blindly_resubmitted") is not True:
            report.fail(f"{where}: submissions.ambiguous_never_blindly_resubmitted must be true")


def check_checkpoints(report, where: str, checkpoints) -> None:
    require_keys(
        report,
        where,
        checkpoints,
        {"attempted", "completed", "in_flight", "failed", "cache_hits_are_not_completed",
         "final_failing_quality_is_completed"},
    )
    if not isinstance(checkpoints, dict):
        return
    for key in ("attempted", "completed", "in_flight", "failed"):
        value = checkpoints.get(key)
        if value is not None and not is_nonnegative_number(value):
            report.fail(f"{where}.{key}: must be a non-negative number or null")
    if checkpoints.get("cache_hits_are_not_completed") is not True:
        report.fail(f"{where}.cache_hits_are_not_completed must be true")
    if checkpoints.get("final_failing_quality_is_completed") is not True:
        report.fail(f"{where}.final_failing_quality_is_completed must be true")
