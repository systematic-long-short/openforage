"""Reusable, edge-by-edge checks for the bounded background-agent proof.

This consumes facts already measured by ``examples/background-agent/proof.py``.
It does not start a supervisor or create a second proof route. Its selfcheck
fixtures are synthetic and cannot be promoted into the public capability
projection.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from onboarding.capabilities import project_capability_records  # noqa: E402

EDGE_IDS = (
    "fresh_cli_and_distinct_session",
    "exact_child_continuation",
    "real_ownership_acknowledgement",
    "parent_independence",
    "pause_drain_reconcile",
    "controller_crash_recovery",
    "terminal_stop_no_relaunch",
    "duplicate_owner_exclusion",
    "owned_process_cleanup",
    "external_due_delivery",
    "native_scheduler_delivery",
)

EDGE_CAPABILITIES = {
    "fresh_cli_and_distinct_session": ("separate-cli-invocation", "distinct-session-identity"),
    "exact_child_continuation": ("resume", "exact-target-control"),
    "real_ownership_acknowledgement": ("parent-independence",),
    "parent_independence": ("parent-independence",),
    "pause_drain_reconcile": ("pause", "resume"),
    "controller_crash_recovery": ("crash-recovery",),
    "terminal_stop_no_relaunch": ("stop", "terminal-triggers-stopped"),
    "duplicate_owner_exclusion": ("duplicate-start-control",),
    "owned_process_cleanup": ("stop",),
    "external_due_delivery": ("scheduler-wakeup-and-consumption",),
    "native_scheduler_delivery": ("native-scheduler",),
}

NATIVE_OPERATION_IDS = (
    "goal_declaration",
    "task_creation",
    "task_list_write",
    "task_durable_reread",
    "task_state_update",
    "memory_write",
    "memory_durable_reread",
    "native_scheduler_creation",
    "native_scheduler_delivery",
)
NATIVE_OUTCOMES = {
    "observed_success", "observed_failure", "observed_in_progress",
    "observed_mixed", "unavailable", "unknown",
}


@dataclass(frozen=True)
class Check:
    label: str
    path: str
    expected: Any


@dataclass(frozen=True)
class Edge:
    edge_id: str
    mechanism: str
    checks: tuple[Check, ...]
    scope_owner: str
    external_checks: tuple[Check, ...] = ()
    native_checks: tuple[Check, ...] = ()


EDGE_SPECS = (
    Edge("fresh_cli_and_distinct_session", "harness-cli + external-controller", (
        Check("parent CLI exited successfully", "parent.exit_code", 0),
        Check("parent exited before child bootstrap", "parent_exited_before_child_start", True),
        Check("child session differs from parent", "child.distinct_from_parent", True),
        Check("launcher recorded child session", "launcher.child_session_recorded", True),
    ), "harness-cli + external-controller",
       external_checks=(
           Check("parent exited before child bootstrap", "parent_exited_before_child_start", True),
           Check("launcher recorded child session", "launcher.child_session_recorded", True),
       ),
       native_checks=(
           Check("parent CLI exited successfully", "parent.exit_code", 0),
           Check("child session differs from parent", "child.distinct_from_parent", True),
       )),
    Edge("exact_child_continuation", "harness-cli exact-ID resume + external-controller request", (
        Check("continuation reused child session", "continuation.same_child_session", True),
        Check("resume targeted recorded child", "resume.exact_recorded_child_target", True),
        Check("mismatched target was refused", "active_pause.mismatched_target_refused", True),
        Check("resume session fingerprint matches child", "resume.same_child_session_fingerprint", "@child.session_fingerprint"),
    ), "harness-cli + external-controller",
       external_checks=(
           Check("resume targeted recorded child", "resume.exact_recorded_child_target", True),
           Check("mismatched target was refused", "active_pause.mismatched_target_refused", True),
       ),
       native_checks=(
           Check("continuation reused child session", "continuation.same_child_session", True),
           Check("history marker came from the child session", "resume.history_marker_match", True),
           Check("resume returned the same child identity", "resume.same_child_session_fingerprint", "@child.session_fingerprint"),
       )),
    Edge("real_ownership_acknowledgement", "native final result validated by external-controller", (
        Check("ownership acknowledgement observed", "child.ownership_acknowledgement", "observed"),
        Check("ownership binding matches request/session/task/fence", "child.ownership_binding_matches", True),
        Check("bootstrap request has a validated acknowledgement", "request_acknowledgements.bootstrap.ack_binding_matches", True),
    ), "harness-cli + external-controller",
       external_checks=(
           Check("ownership fields bind to request/session/task/fence", "child.ownership_binding_matches", True),
           Check("controller accepted the bound acknowledgement", "request_acknowledgements.bootstrap.ack_binding_matches", True),
       ),
       native_checks=(
           Check("native ownership result event observed", "request_acknowledgements.bootstrap.native_result_event_observed", True),
           Check("child supplied the structured ownership acknowledgement", "child.ownership_acknowledgement", "observed"),
       )),
    Edge("parent_independence", "detached external-controller + separate child CLI", (
        Check("parent exited before child bootstrap", "parent_exited_before_child_start", True),
        Check("controller remained after launcher exit", "launcher.detached_controller_alive_after_launcher_exit", True),
        Check("launcher returned after ownership acknowledgement", "launcher.returned_after_ownership_ack", True),
    ), "external-controller"),
    Edge("pause_drain_reconcile", "external-controller process-group pause and exact-child reconciliation", (
        Check("controller entered paused state", "active_pause.status", "paused"),
        Check("interrupted turn remains uncertain", "active_pause.turn_outcome", "uncertain"),
        Check("owned child process group is gone", "active_pause.process_group_status_after_pause", "gone"),
        Check("exact child reconciled after pause", "resume.exact_recorded_child_target", True),
        Check("paused external due window dispatched nothing", "external_recurrence.paused_window_prevented_new_dispatch", True),
    ), "external-controller"),
    Edge("controller_crash_recovery", "external-controller identity/fence recovery", (
        Check("stale controller was identified", "controller_crash.classification_after_kill", "stale_controller"),
        Check("controller generation advanced", "recovery.controller_generation_advanced", True),
        Check("fencing token advanced", "recovery.fence_advanced", True),
        Check("recovery kept the exact child", "recovery.same_child_session", True),
        Check("no overlapping child process", "recovery.no_overlapping_child", True),
    ), "external-controller"),
    Edge("terminal_stop_no_relaunch", "external-controller terminal stop", (
        Check("active stop is terminal", "active_stop.terminal_status", "stopped"),
        Check("owned process group is gone", "active_stop.process_group_status_after_stop", "gone"),
        Check("turn/resume/recover are refused", "terminal_stop.turn_resume_recover_refused", True),
        Check("stopped external due window dispatched nothing", "external_recurrence.stopped_window_prevented_new_dispatch", True),
    ), "external-controller"),
    Edge("duplicate_owner_exclusion", "external-controller canonical-workspace owner guard", (
        Check("second owner was refused", "duplicate_owner.refused", True),
        Check("refusal addressed the same workspace", "duplicate_owner.same_canonical_workspace", True),
        Check("no second child was started", "duplicate_owner.no_child_started", True),
        Check("original owner remains intact", "duplicate_owner.original_owner_unchanged", True),
    ), "external-controller"),
    Edge("owned_process_cleanup", "external-controller identity-checked cleanup", (
        Check("isolated workspace was removed", "cleanup.isolated_workspace_removed", True),
        Check("main run owned groups are gone", "cleanup.runs.@proof_id.all_owned_process_groups_proven_gone", True),
        Check("recurrence run owned groups are gone", "cleanup.runs.@proof_id-recurrence.all_owned_process_groups_proven_gone", True),
    ), "external-controller"),
    Edge("external_due_delivery", "external-controller due-time request and exact-child ack", (
        Check("delivery owner is explicitly external", "external_recurrence.mode", "explicit_external_interval_delivery"),
        Check("at least two due continuations were acknowledged", "external_recurrence.acknowledged_due_continuations", {"minimum_length": 2}),
        Check("all due continuations used the same exact child", "external_recurrence.acknowledged_due_continuations", {"all_items_match": {"acknowledged": True, "delivery": "external_due_time", "native_session_match": True, "same_child_session": True}}),
    ), "external-controller"),
    Edge("native_scheduler_delivery", "native product scheduler delivery only", (
        Check("real native delivery evidence exists", "native_primitive_observations.operations.native_scheduler_delivery.outcome", "observed_success"),
    ), "native-product"),
)


def _get_path(value: Any, path: str, proof: dict[str, Any]) -> Any:
    path = path.replace("@proof_id-recurrence", f"{proof.get('proof_id')}-recurrence")
    path = path.replace("@proof_id", str(proof.get("proof_id", "")))
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _same_json_type_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_type_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_type_equal(a, b) for a, b in zip(left, right))
    return left == right


def _check(value: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and "minimum_length" in expected:
        return isinstance(value, list) and len(value) >= expected["minimum_length"]
    if isinstance(expected, dict) and "all_items_match" in expected:
        facts = expected["all_items_match"]
        return isinstance(value, list) and bool(value) and all(
            isinstance(item, dict)
            and all(key in item and _same_json_type_equal(item[key], actual) for key, actual in facts.items())
            for item in value
        )
    return _same_json_type_equal(value, expected)


def _edge_report(edge: Edge, proof: dict[str, Any]) -> dict[str, Any]:
    def evaluate(checks: tuple[Check, ...]) -> tuple[str, list[dict[str, Any]]]:
        assertions: list[dict[str, Any]] = []
        for check in checks:
            expected = check.expected
            if isinstance(expected, str) and expected.startswith("@"):
                try:
                    expected = _get_path(proof, expected[1:], proof)
                except KeyError:
                    expected = None
            try:
                observed = _get_path(proof, check.path, proof)
            except KeyError:
                assertions.append({
                    "label": check.label,
                    "path": check.path,
                    "expected": expected,
                    "observed": None,
                    "status": "not_run",
                })
                continue
            passed = _check(observed, expected)
            unresolved = observed in {None, "unknown", "not_observed", "unavailable"} if not isinstance(observed, (dict, list)) else False
            assertions.append({
                "label": check.label,
                "path": check.path,
                "expected": expected,
                "observed": observed,
                "status": "passed" if passed else "unknown" if unresolved else "failed",
            })
        statuses = {item["status"] for item in assertions}
        if not assertions or statuses == {"not_applicable"}:
            return "not_applicable", assertions
        if statuses == {"passed"}:
            return "passed", assertions
        if "failed" in statuses:
            return "failed", assertions
        if statuses == {"not_run"}:
            return "not_run", assertions
        return "unknown", assertions

    overall, assertions = evaluate(edge.checks)
    if edge.scope_owner == "native-product":
        external_status, external_assertions = "not_applicable", []
        native_status, native_assertions = evaluate(edge.native_checks or edge.checks)
    elif edge.scope_owner == "harness-cli + external-controller":
        external_status, external_assertions = evaluate(edge.external_checks)
        native_status, native_assertions = evaluate(edge.native_checks)
    else:
        external_status, external_assertions = evaluate(edge.external_checks or edge.checks)
        native_status, native_assertions = "not_applicable", []
    return {
        "status": overall,
        "mechanism": edge.mechanism,
        "scope_owner": edge.scope_owner,
        "external_controller_status": external_status,
        "native_product_status": native_status,
        "assertions": assertions,
        "external_controller_assertions": external_assertions,
        "native_product_assertions": native_assertions,
    }


def _review_draft(edges: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare typed pointers for manual review; never add registry records."""
    rows = []
    for edge_id, capabilities in EDGE_CAPABILITIES.items():
        edge = edges[edge_id]
        for capability in capabilities:
            rows.append({
                "capability": capability,
                "evidence_path": f"edges.{edge_id}",
                "evidence_outcome": edge["status"],
                "review_state": "not_submitted",
                "automatic_registry_promotion": False,
            })
    return rows


def evaluate_proof_summary(
    proof: dict[str, Any],
    *,
    root: Path | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Evaluate independently reported edge facts and attach canonical projection context."""
    edge_rows = {edge.edge_id: _edge_report(edge, proof) for edge in EDGE_SPECS}
    statuses = {row["status"] for row in edge_rows.values()}
    if statuses == {"passed"}:
        overall = "passed"
    elif "failed" in statuses:
        overall = "failed"
    elif statuses == {"not_run"}:
        overall = "not_run"
    else:
        overall = "partial"

    external_statuses = [
        edge["external_controller_status"]
        for edge in edge_rows.values()
        if edge["external_controller_status"] != "not_applicable"
    ]
    if external_statuses and all(status == "passed" for status in external_statuses):
        external_overall = "passed"
    elif "failed" in external_statuses:
        external_overall = "failed"
    elif external_statuses and all(status == "not_run" for status in external_statuses):
        external_overall = "not_run"
    else:
        external_overall = "partial"

    product = proof.get("product")
    if not isinstance(product, str):
        product = proof.get("harness") if isinstance(proof.get("harness"), str) else None
    projected: list[dict[str, Any]] = []
    projection_error = None
    if root is not None and isinstance(product, str):
        try:
            registry_path = root / "src/openforage/skills/agent_runtime_integration/references/harnesses.json"
            entries = json.loads(registry_path.read_text(encoding="utf-8"))
            entry = next((item for item in entries if isinstance(item, dict) and item.get("product") == product), None)
            if entry is not None:
                projected = project_capability_records(root, entry)
            else:
                projection_error = "product is not present in the canonical harness registry"
        except (OSError, ValueError, StopIteration) as exc:
            projection_error = type(exc).__name__
        except Exception as exc:  # controlled projection error, not proof evidence
            projection_error = type(exc).__name__

    primitive_observations = proof.get("native_primitive_observations")
    native_scheduler = None
    if isinstance(primitive_observations, dict):
        operations = primitive_observations.get("operations")
        if isinstance(operations, dict) and isinstance(operations.get("native_scheduler_delivery"), dict):
            native_scheduler = operations["native_scheduler_delivery"].get("outcome")
    return {
        "schema_version": 1,
        "evidence_class": "background-agent-conformance-report",
        "synthetic": bool(synthetic),
        "product": product,
        "adapter_id": proof.get("adapter_id", proof.get("harness")),
        "product_version": proof.get("harness_version"),
        "os": proof.get("os"),
        "profile": (proof.get("adapter_profile") or {}).get("selected")
        if isinstance(proof.get("adapter_profile"), dict) else proof.get("adapter_profile"),
        "overall": overall,
        "external_controller_overall": external_overall,
        "scope": {
            "external_controller": "bounded external-controller process/request lifecycle only",
            "native_product": "CLI identity/session/ack and explicit operation events only; native pause/stop/scheduler are separate evidence",
            "native_scheduler_delivery_outcome": native_scheduler or "unknown",
            "external_due_delivery_is_native_scheduler": False,
        },
        "request_ack_bindings": (
            list(proof.get("request_acknowledgements", {}).values())
            if isinstance(proof.get("request_acknowledgements"), dict)
            else proof.get("request_acknowledgements", [])
        ),
        "edges": edge_rows,
        "native_primitive_observations": primitive_observations or {},
        "capability_projection": {
            "authority": "scripts/onboarding/capabilities.py:project_capability_records",
            "registry": "src/openforage/skills/agent_runtime_integration/references/harnesses.json",
            "current_records": projected,
            "projection_error": projection_error,
            "review_draft": _review_draft(edge_rows),
            "automatic_registry_promotion": False,
        },
    }


def validate_proof_v2(proof: dict[str, Any]) -> list[str]:
    """Validate the stable, sanitized v2 proof envelope without dependencies."""
    errors: list[str] = []
    if not isinstance(proof, dict):
        return ["proof root must be an object"]
    if type(proof.get("schema_version")) is not int or proof.get("schema_version") != 2:
        errors.append("proof schema_version must be integer 2")
    if proof.get("evidence_class") not in {
        "bounded-native-background-agent-proof",
        "failed-bounded-native-background-agent-proof",
    }:
        errors.append("proof evidence_class is not recognized")
    for key in ("proof_id", "harness", "product", "adapter_id", "harness_version", "os", "scope"):
        if not isinstance(proof.get(key), str) or not proof[key].strip():
            errors.append(f"proof {key} must be a non-empty string")
    if type(proof.get("passed")) is not bool:
        errors.append("proof passed must be a boolean")
    if not isinstance(proof.get("failures"), list):
        errors.append("proof failures must be a list")
    elif proof.get("passed") is True and proof["failures"]:
        errors.append("passing proof cannot contain failures")
    profile = proof.get("adapter_profile")
    if not isinstance(profile, dict):
        errors.append("proof adapter_profile must explicitly select isolated")
        profile = {}
    elif profile.get("selected") != "isolated":
        errors.append("proof adapter_profile must explicitly select isolated")
    digests = proof.get("source_file_digests")
    if not isinstance(digests, dict) or not digests:
        errors.append("proof source_file_digests must be a non-empty object")
    elif any(
        not isinstance(name, str)
        or not isinstance(row, dict)
        or not isinstance(row.get("sha256"), str)
        or len(row["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in row["sha256"])
        or type(row.get("bytes")) is not int
        or row["bytes"] < 0
        for name, row in digests.items()
    ):
        errors.append("proof source-file digest entries are malformed")
    acknowledgements = proof.get("request_acknowledgements")
    if not isinstance(acknowledgements, dict):
        errors.append("proof request_acknowledgements must be an object")
    else:
        for key, item in acknowledgements.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                errors.append("proof request acknowledgement entries are malformed")
                break
            if type(item.get("ack_binding_matches")) is not bool:
                errors.append(f"proof request acknowledgement {key!r} lacks typed binding outcome")
                break
            if not isinstance(item.get("native_result_event_observed"), bool):
                errors.append(f"proof request acknowledgement {key!r} lacks typed result-event outcome")
                break
            if (
                item.get("adapter_id") != proof.get("adapter_id")
                or item.get("product_version") != proof.get("harness_version")
                or item.get("os") != proof.get("os")
                or item.get("profile") != profile.get("selected")
            ):
                errors.append(f"proof request acknowledgement {key!r} has mismatched adapter/version/OS/profile scope")
                break
    native = proof.get("native_primitive_observations")
    operations = native.get("operations") if isinstance(native, dict) else None
    if not isinstance(operations, dict) or set(NATIVE_OPERATION_IDS) - set(operations):
        errors.append("proof must carry every typed native operation outcome")
    else:
        for operation in NATIVE_OPERATION_IDS:
            row = operations.get(operation)
            if (
                not isinstance(row, dict)
                or row.get("outcome") not in NATIVE_OUTCOMES
                or not isinstance(row.get("availability"), str)
                or not isinstance(row.get("evidence"), list)
                or not isinstance(row.get("scope"), dict)
            ):
                errors.append(f"proof native operation {operation!r} is malformed")
                break
            for event in row["evidence"]:
                event_scope = event.get("scope") if isinstance(event, dict) else None
                if (
                    not isinstance(event, dict)
                    or not isinstance(event.get("event_ref"), str)
                    or not event["event_ref"]
                    or not isinstance(event.get("event_type"), str)
                    or not event["event_type"]
                    or not isinstance(event_scope, dict)
                    or event_scope.get("adapter_id") != proof.get("adapter_id")
                    or event_scope.get("product_version") != proof.get("harness_version")
                    or event_scope.get("os") != proof.get("os")
                    or event_scope.get("profile") != profile.get("selected")
                    or not isinstance(event_scope.get("request_id"), str)
                    or not isinstance(event_scope.get("turn_id"), str)
                    or type(event_scope.get("fence")) is not int
                    or not isinstance(event_scope.get("session_fingerprint"), str)
                    or not event_scope["session_fingerprint"]
                ):
                    errors.append(f"proof native operation {operation!r} has unscoped tool/event evidence")
                    break
                if operation != "native_scheduler_delivery" and not isinstance(event.get("tool"), str):
                    errors.append(f"proof native operation {operation!r} lacks an explicit tool identity")
                    break
            if errors and errors[-1].startswith(f"proof native operation {operation!r}"):
                break
    conformance = proof.get("conformance")
    if not isinstance(conformance, dict) or conformance.get("evidence_class") != "background-agent-conformance-report":
        errors.append("proof conformance report is missing or unrecognized")
    else:
        edges = conformance.get("edges")
        if not isinstance(edges, dict) or set(edges) != set(EDGE_IDS):
            errors.append("proof conformance report does not include each edge separately")
    if proof.get("native_scheduler") != "unknown; the controller supports manual delivery or one explicit external interval owner":
        errors.append("external due delivery must leave native scheduler status unknown")
    return errors


def selfcheck(fixtures_dir: Path) -> list[str]:
    """Run only inert synthetic records; never launches a native process/model."""
    failures: list[str] = []
    fixture_path = fixtures_dir / "conformance-pass.synthetic.json"
    try:
        proof = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["synthetic conformance fixture is unreadable"]
    report = evaluate_proof_summary(proof, synthetic=True)
    if report.get("synthetic") is not True:
        failures.append("synthetic conformance input lost its synthetic label")
    if report.get("external_controller_overall") != "passed":
        failures.append("synthetic external-controller edges did not report a passing scoped result")
    if report.get("edges", {}).keys() != set(EDGE_IDS):
        failures.append("conformance report does not report every lifecycle edge separately")
    for edge_id in EDGE_IDS:
        expected = "unknown" if edge_id == "native_scheduler_delivery" else "passed"
        if report["edges"][edge_id]["status"] != expected:
            failures.append(f"synthetic edge {edge_id} was not classified as {expected}")
        if "external_controller_status" not in report["edges"][edge_id] or "native_product_status" not in report["edges"][edge_id]:
            failures.append(f"synthetic edge {edge_id} lost external/native scope")
    for edge_id in (
        "fresh_cli_and_distinct_session",
        "exact_child_continuation",
        "real_ownership_acknowledgement",
    ):
        edge = report["edges"][edge_id]
        if edge["external_controller_status"] != "passed" or edge["native_product_status"] != "passed":
            failures.append(f"synthetic combined-scope edge {edge_id} did not report each scope separately")
    if report["scope"]["external_due_delivery_is_native_scheduler"] is not False:
        failures.append("external due delivery was promoted to native scheduling")
    if report["capability_projection"]["automatic_registry_promotion"] is not False:
        failures.append("conformance output enabled automatic registry promotion")

    # A plan/metadata entry is not a tool event and cannot create native success.
    no_native = dict(proof)
    no_native["native_primitive_observations"] = {
        "operations": {"native_scheduler_delivery": {"outcome": "unknown", "evidence": []}}
    }
    no_native["external_recurrence"] = {
        "mode": "explicit_external_interval_delivery",
        "acknowledged_due_continuations": [{
            "acknowledged": True,
            "delivery": "external_due_time",
            "native_session_match": True,
            "same_child_session": True,
        } for _ in range(2)],
    }
    no_native_report = evaluate_proof_summary(no_native, synthetic=True)
    if no_native_report["edges"]["native_scheduler_delivery"]["status"] != "unknown":
        failures.append("external due-time rows satisfied the native scheduler edge")

    missing = {"synthetic": True, "harness": "claude-code"}
    missing_report = evaluate_proof_summary(missing, synthetic=True)
    if any(item["status"] == "passed" for item in missing_report["edges"].values()):
        failures.append("missing conformance observations were upgraded to pass")
    return failures
