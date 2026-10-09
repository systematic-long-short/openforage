"""Canonical scoped capability projection for startup and harness documentation.

Registry records are returned individually. A caller may select exact record
dimensions, but this module never pools versions, profiles, probes, or
mechanisms into a broader support claim.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping

REGISTRY_REL = (
    "src/openforage/skills/agent_runtime_integration/references/harnesses.json"
)
LEGACY_AUTHORITY_KEYS = {
    "verified_capabilities",
    "external_background_adapter_evidence",
}
STATUSES = {"verified", "unverified", "unresolved"}
CAPABILITY_IDS = {
    "distinct-session-identity",
    "separate-cli-invocation",
    "parent-independence",
    "native-goal-task-memory",
    "native-goal-operation",
    "native-task-tool-operation",
    "native-memory-operation",
    "native-scheduler",
    "native-task-store",
    "shell-execution",
    "workspace-read-write",
    "scheduler-wakeup-and-consumption",
    "pause",
    "resume",
    "stop",
    "crash-recovery",
    "duplicate-start-control",
    "exact-target-control",
    "terminal-triggers-stopped",
    "openforage-worker-checkpoint",
    "openforage-worker-stop",
}
SELECTOR_KEYS = {
    "capability",
    "mechanism",
    "product_version",
    "probe",
    "test_id",
    "scope.surface",
    "scope.os",
    "scope.profile",
    "scope.lifecycle",
}
PROJECTION_BEGIN_MARKER = "<!-- BEGIN GENERATED SCOPED CAPABILITY PROJECTION -->"
PROJECTION_END_MARKER = "<!-- END GENERATED SCOPED CAPABILITY PROJECTION -->"


class CapabilityProjectionError(ValueError):
    """A capability record set is malformed or contradicts its evidence."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else problems
        super().__init__("; ".join(self.problems))


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal():
            index = int(part)
            if index >= len(current):
                raise KeyError(path)
            current = current[index]
        else:
            raise KeyError(path)
    return current


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values recursively without Python's bool/int equivalence."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _read_artifact(root: Path, reference: Any, record_id: str) -> dict[str, Any]:
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError(f"{record_id}: evidence_artifact is required")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{record_id}: evidence_artifact must be repository-relative")
    try:
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{record_id}: evidence_artifact escapes the repository") from exc
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{record_id}: cannot read JSON evidence {reference!r}: {exc}") from exc
    if not isinstance(evidence, dict):
        raise ValueError(f"{record_id}: evidence artifact must contain a JSON object")
    return evidence


def _check_assertions(record: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    record_id = str(record.get("record_id", record.get("capability", "record")))
    assertions = record.get("assertions", [])
    if not isinstance(assertions, list):
        return [f"{record_id}: assertions must be a list"]
    problems: list[str] = []
    for index, assertion in enumerate(assertions):
        where = f"{record_id}.assertions[{index}]"
        if (
            not isinstance(assertion, dict)
            or not isinstance(assertion.get("path"), str)
            or not assertion["path"].strip()
        ):
            problems.append(f"{where}: assertion requires a dotted evidence path")
            continue
        path = assertion["path"]
        try:
            observed = _get_path(evidence, path)
        except KeyError:
            problems.append(f"{where}: evidence path {path!r} is missing")
            continue
        operators = {
            key
            for key in (
                "equals",
                "equals_path",
                "contains",
                "length_at_least",
                "all_items_match",
            )
            if key in assertion
        }
        if len(operators) != 1:
            problems.append(f"{where}: assertion requires exactly one supported operator")
            continue
        operator = next(iter(operators))
        if operator == "equals":
            matches = _json_equal(observed, assertion["equals"])
        elif operator == "equals_path" and isinstance(assertion["equals_path"], str):
            try:
                matches = _json_equal(observed, _get_path(evidence, assertion["equals_path"]))
            except KeyError:
                problems.append(
                    f"{where}: comparison evidence path {assertion['equals_path']!r} is missing"
                )
                continue
        elif operator == "equals_path":
            problems.append(f"{where}: equals_path must be a dotted evidence path")
            continue
        elif operator == "contains":
            expected = assertion["contains"]
            matches = isinstance(observed, list) and any(
                _json_equal(item, expected) for item in observed
            )
        elif operator == "length_at_least":
            minimum = assertion["length_at_least"]
            matches = (
                isinstance(observed, (list, str))
                and isinstance(minimum, int)
                and not isinstance(minimum, bool)
                and minimum >= 0
                and len(observed) >= minimum
            )
        elif operator == "all_items_match" and isinstance(assertion["all_items_match"], dict):
            expected = assertion["all_items_match"]
            matches = isinstance(observed, list) and bool(observed) and all(
                isinstance(item, dict)
                and all(
                    key in item and _json_equal(item[key], value)
                    for key, value in expected.items()
                )
                for item in observed
            )
        else:
            problems.append(f"{where}: unsupported assertion operator")
            continue
        if not matches:
            problems.append(
                f"{where}: evidence at {path!r} contradicts the recorded assertion"
            )
    return problems


PROOF_POSITIVE_ASSERTIONS: dict[str, list[dict[str, Any]]] = {
    "separate-cli-invocation": [
        {"path": "parent_exited_before_child_start", "equals": True},
        {"path": "parent.exit_code", "equals": 0},
        {"path": "child.distinct_from_parent", "equals": True},
        {"path": "launcher.child_session_recorded", "equals": True},
    ],
    "distinct-session-identity": [
        {"path": "child.distinct_from_parent", "equals": True},
        {"path": "external_recurrence.child_session_distinct_from_parent", "equals": True},
    ],
    "parent-independence": [
        {"path": "parent_exited_before_child_start", "equals": True},
        {"path": "launcher.detached_controller_alive_after_launcher_exit", "equals": True},
        {"path": "launcher.returned_after_ownership_ack", "equals": True},
        {"path": "child.ownership_acknowledgement", "equals": "observed"},
    ],
    "resume": [
        {"path": "resume.exact_recorded_child_target", "equals": True},
        {"path": "resume.history_marker_match", "equals": True},
        {
            "path": "resume.same_child_session_fingerprint",
            "equals_path": "child.session_fingerprint",
        },
        {"path": "continuation.same_child_session", "equals": True},
    ],
    "scheduler-wakeup-and-consumption": [
        {
            "path": "external_recurrence.mode",
            "equals": "explicit_external_interval_delivery",
        },
        {
            "path": "external_recurrence.acknowledged_due_continuations",
            "length_at_least": 2,
        },
        {
            "path": "external_recurrence.acknowledged_due_continuations",
            "all_items_match": {
                "acknowledged": True,
                "delivery": "external_due_time",
                "native_session_match": True,
                "same_child_session": True,
            },
        },
        {"path": "external_recurrence.paused_window_prevented_new_dispatch", "equals": True},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
    ],
    "pause": [
        {"path": "active_pause.status", "equals": "paused"},
        {"path": "active_pause.process_group_status_after_pause", "equals": "gone"},
        {"path": "active_pause.application_worker_checkpoint", "equals": "unknown"},
        {"path": "external_recurrence.paused_window_prevented_new_dispatch", "equals": True},
    ],
    "stop": [
        {"path": "active_stop.terminal_status", "equals": "stopped"},
        {"path": "active_stop.process_group_status_after_stop", "equals": "gone"},
        {"path": "active_stop.application_worker_stop", "equals": "unknown"},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
    ],
    "crash-recovery": [
        {"path": "controller_crash.classification_after_kill", "equals": "stale_controller"},
        {"path": "recovery.controller_generation_advanced", "equals": True},
        {"path": "recovery.fence_advanced", "equals": True},
        {"path": "recovery.no_overlapping_child", "equals": True},
        {"path": "recovery.same_child_session", "equals": True},
    ],
    "exact-target-control": [
        {"path": "active_pause.mismatched_target_refused", "equals": True},
        {"path": "resume.exact_recorded_child_target", "equals": True},
    ],
    "terminal-triggers-stopped": [
        {"path": "external_recurrence.terminal_status", "equals": "stopped"},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
        {"path": "terminal_stop.turn_resume_recover_refused", "equals": True},
    ],
    "native-task-tool-operation": [
        {"path": "native_goal_task_memory.task.status", "equals": "observed_success"},
        {
            "path": "native_goal_task_memory.task.tools",
            "contains": {"tool": "todowrite", "status": "completed"},
        },
    ],
}

# Schema 2 is the reusable adapter/conformance proof. Its controller edges are
# checked separately, and native task evidence is narrowed to an explicit typed
# tool operation (not task creation, durable reread, or native scheduling).
PROOF_POSITIVE_ASSERTIONS_V2: dict[str, list[dict[str, Any]]] = {
    "separate-cli-invocation": [
        {"path": "parent_exited_before_child_start", "equals": True},
        {"path": "parent.exit_code", "equals": 0},
        {"path": "child.distinct_from_parent", "equals": True},
        {"path": "launcher.child_session_recorded", "equals": True},
        {"path": "conformance.edges.fresh_cli_and_distinct_session.status", "equals": "passed"},
    ],
    "distinct-session-identity": [
        {"path": "child.distinct_from_parent", "equals": True},
        {"path": "external_recurrence.child_session_distinct_from_parent", "equals": True},
        {"path": "conformance.edges.fresh_cli_and_distinct_session.status", "equals": "passed"},
    ],
    "parent-independence": [
        {"path": "parent_exited_before_child_start", "equals": True},
        {"path": "launcher.detached_controller_alive_after_launcher_exit", "equals": True},
        {"path": "launcher.returned_after_ownership_ack", "equals": True},
        {"path": "child.ownership_acknowledgement", "equals": "observed"},
        {"path": "conformance.edges.parent_independence.status", "equals": "passed"},
    ],
    "resume": [
        {"path": "resume.exact_recorded_child_target", "equals": True},
        {"path": "resume.history_marker_match", "equals": True},
        {"path": "resume.same_child_session_fingerprint", "equals_path": "child.session_fingerprint"},
        {"path": "continuation.same_child_session", "equals": True},
        {"path": "conformance.edges.exact_child_continuation.status", "equals": "passed"},
    ],
    "scheduler-wakeup-and-consumption": [
        {"path": "external_recurrence.mode", "equals": "explicit_external_interval_delivery"},
        {"path": "external_recurrence.acknowledged_due_continuations", "length_at_least": 2},
        {"path": "external_recurrence.acknowledged_due_continuations", "all_items_match": {
            "acknowledged": True, "delivery": "external_due_time", "native_session_match": True,
            "same_child_session": True,
        }},
        {"path": "external_recurrence.paused_window_prevented_new_dispatch", "equals": True},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
        {"path": "conformance.edges.external_due_delivery.status", "equals": "passed"},
        {"path": "conformance.scope.external_due_delivery_is_native_scheduler", "equals": False},
    ],
    "pause": [
        {"path": "active_pause.status", "equals": "paused"},
        {"path": "active_pause.process_group_status_after_pause", "equals": "gone"},
        {"path": "active_pause.application_worker_checkpoint", "equals": "unknown"},
        {"path": "external_recurrence.paused_window_prevented_new_dispatch", "equals": True},
        {"path": "conformance.edges.pause_drain_reconcile.status", "equals": "passed"},
    ],
    "stop": [
        {"path": "active_stop.terminal_status", "equals": "stopped"},
        {"path": "active_stop.process_group_status_after_stop", "equals": "gone"},
        {"path": "active_stop.application_worker_stop", "equals": "unknown"},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
        {"path": "conformance.edges.terminal_stop_no_relaunch.status", "equals": "passed"},
        {"path": "cleanup.isolated_workspace_removed", "equals": True},
    ],
    "crash-recovery": [
        {"path": "controller_crash.classification_after_kill", "equals": "stale_controller"},
        {"path": "recovery.controller_generation_advanced", "equals": True},
        {"path": "recovery.fence_advanced", "equals": True},
        {"path": "recovery.no_overlapping_child", "equals": True},
        {"path": "recovery.same_child_session", "equals": True},
        {"path": "conformance.edges.controller_crash_recovery.status", "equals": "passed"},
    ],
    "exact-target-control": [
        {"path": "active_pause.mismatched_target_refused", "equals": True},
        {"path": "resume.exact_recorded_child_target", "equals": True},
        {"path": "conformance.edges.exact_child_continuation.status", "equals": "passed"},
    ],
    "terminal-triggers-stopped": [
        {"path": "external_recurrence.terminal_status", "equals": "stopped"},
        {"path": "external_recurrence.stopped_window_prevented_new_dispatch", "equals": True},
        {"path": "terminal_stop.turn_resume_recover_refused", "equals": True},
        {"path": "conformance.edges.terminal_stop_no_relaunch.status", "equals": "passed"},
    ],
    "duplicate-start-control": [
        {"path": "duplicate_owner.refused", "equals": True},
        {"path": "duplicate_owner.same_canonical_workspace", "equals": True},
        {"path": "duplicate_owner.no_child_started", "equals": True},
        {"path": "duplicate_owner.original_owner_unchanged", "equals": True},
        {"path": "conformance.edges.duplicate_owner_exclusion.status", "equals": "passed"},
    ],
    "native-task-tool-operation": [
        {"path": "native_primitive_observations.operations.task_list_write.outcome", "equals": "observed_success"},
        {"path": "native_primitive_observations.operations.task_list_write.evidence", "all_items_match": {
            "tool": "todowrite", "status": "completed",
        }},
    ],
}

PROOF_CONTRACTS: dict[str, dict[str, Any]] = {
    "codex.background-agent-proof.v1": {
        "product_identity": "Codex",
        "adapter_identity": "codex",
        "evidence_class": "bounded-native-background-agent-proof",
        "schema_version": 1,
        "verified_capabilities": frozenset(PROOF_POSITIVE_ASSERTIONS)
        - {"native-task-tool-operation"},
    },
    "opencode.background-agent-proof.v1": {
        "product_identity": "OpenCode",
        "adapter_identity": "opencode",
        "evidence_class": "bounded-native-background-agent-proof",
        "schema_version": 1,
        "verified_capabilities": frozenset(PROOF_POSITIVE_ASSERTIONS),
    },
    "codex.background-agent-proof.v2": {
        "product_identity": "Codex",
        "adapter_identity": "codex",
        "evidence_class": "bounded-native-background-agent-proof",
        "schema_version": 2,
        "verified_capabilities": frozenset(PROOF_POSITIVE_ASSERTIONS_V2)
        - {"native-task-tool-operation"},
    },
    "opencode.background-agent-proof.v2": {
        "product_identity": "OpenCode",
        "adapter_identity": "opencode",
        "evidence_class": "bounded-native-background-agent-proof",
        "schema_version": 2,
        "verified_capabilities": frozenset(PROOF_POSITIVE_ASSERTIONS_V2),
    },
    "claude-code.background-agent-proof.v2": {
        "product_identity": "Claude Code",
        "adapter_identity": "claude-code",
        "evidence_class": "bounded-native-background-agent-proof",
        "schema_version": 2,
        "verified_capabilities": frozenset(PROOF_POSITIVE_ASSERTIONS_V2)
        - {"native-task-tool-operation"},
    },
}
LEGACY_EVIDENCE_CLASSES = {"actual-native-observation"}
EVIDENCE_CONTRACT_DECLARATION_FIELDS = {
    "contract_id",
    "evidence_class",
    "schema_version",
    "product_identity",
    "adapter_identity",
}

PROOF_MECHANISMS = {
    "separate-cli-invocation": "harness-cli",
    "distinct-session-identity": "harness-cli",
    "parent-independence": "external-background-controller",
    "resume": "harness-cli",
    "scheduler-wakeup-and-consumption": "external-background-controller",
    "pause": "external-background-controller",
    "stop": "external-background-controller",
    "crash-recovery": "external-background-controller",
    "exact-target-control": "external-background-controller",
    "terminal-triggers-stopped": "external-background-controller",
    "duplicate-start-control": "external-background-controller",
    "native-task-tool-operation": "native-tool",
    "native-task-store": "native-tool",
    "native-goal-operation": "native-tool",
    "native-memory-operation": "native-tool",
    "native-scheduler": "native-scheduler",
    "openforage-worker-checkpoint": "openforage-worker",
    "openforage-worker-stop": "openforage-worker",
}

PROOF_LIFECYCLES = {
    "separate-cli-invocation": {"fresh parent exited before distinct child bootstrap; launcher returned after ownership acknowledgement"},
    "distinct-session-identity": {"fresh child CLI identity distinct from the parent; later turns retain the child's exact identity"},
    "parent-independence": {"parent exited before child start; detached controller remained after launcher return and acknowledged child ownership"},
    "resume": {"two external due-time continuations and history continuation target the exact child; no native scheduler claim"},
    "scheduler-wakeup-and-consumption": {"two external due-time child continuations acknowledged; paused/stopped windows suppressed later dispatch"},
    "pause": {"active controller pause terminated the bounded turn process group; paused due window dispatched nothing; application-worker checkpoint unknown"},
    "stop": {"active controller stop reached terminal state; stopped due window dispatched nothing; application-worker stop unknown"},
    "crash-recovery": {"stale external controller was replaced with a new generation/fence without overlapping the exact child"},
    "exact-target-control": {"mismatched target refused and continuation kept exact recorded child identity"},
    "terminal-triggers-stopped": {"terminal stop remained stopped through the bounded following due window"},
    "duplicate-start-control": {"second owner of the canonical workspace was refused before child launch; original owner remained unchanged"},
    "native-task-tool-operation": {"successful todowrite events recorded in child turns; does not establish native goals or global memory"},
    "native-goal-operation": {"no goal operation recorded"},
    "native-memory-operation": {
        "no memory operation recorded; thread history is not global memory",
        "no global memory operation recorded; thread history is not global memory",
    },
    "native-task-tool-operation-unknown": {
        "no task tool operation recorded",
        "no successful typed task-list-write event recorded; task creation and durable reread remain unknown",
    },
    "native-task-store": {
        "no correlated durable task reread operation recorded; task creation is a separate outcome",
    },
    "native-scheduler": {"native scheduler unknown; due-time delivery belonged to the external controller"},
    "openforage-worker-checkpoint": {"worker not started by adapter; checkpoint state unknown"},
    "openforage-worker-stop": {"worker not started by adapter; stop state unknown"},
}


def _declared_proof_contracts(
    product: str,
    entry: dict[str, Any],
    problems: list[str],
) -> dict[str, dict[str, Any]]:
    declarations = entry.get("evidence_contracts", [])
    if not isinstance(declarations, list):
        problems.append(f"{product}: evidence_contracts must be a list")
        return {}

    declared: dict[str, dict[str, Any]] = {}
    for index, declaration in enumerate(declarations):
        where = f"{product}.evidence_contracts[{index}]"
        if not isinstance(declaration, dict):
            problems.append(f"{where}: contract declaration must be an object")
            continue
        contract_id = declaration.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            problems.append(f"{where}: contract_id must be a non-empty string")
            continue
        if contract_id in declared:
            problems.append(f"{where}: duplicate evidence contract {contract_id!r}")
            continue
        contract = PROOF_CONTRACTS.get(contract_id)
        if contract is None:
            problems.append(f"{where}: unrecognized evidence contract {contract_id!r}")
            continue

        valid = True
        missing = EVIDENCE_CONTRACT_DECLARATION_FIELDS - set(declaration)
        if missing:
            problems.append(f"{where}: missing contract fields {sorted(missing)}")
            valid = False
        expected_fields = {
            field: contract[field]
            for field in sorted(EVIDENCE_CONTRACT_DECLARATION_FIELDS - {"contract_id"})
        }
        for field, expected in expected_fields.items():
            if field in declaration and not _json_equal(declaration[field], expected):
                problems.append(
                    f"{where}: {field} does not match registered contract "
                    f"{contract_id!r}"
                )
                valid = False
        if contract["product_identity"] != product:
            problems.append(
                f"{where}: registered contract {contract_id!r} is for "
                f"{contract['product_identity']!r}, not {product!r}"
            )
            valid = False
        if valid:
            declared[contract_id] = contract
    return declared


def _is_background_proof_record(record: dict[str, Any]) -> bool:
    """Identify the versioned background-proof family from record provenance."""
    known_classes = {contract["evidence_class"] for contract in PROOF_CONTRACTS.values()}
    probe = record.get("probe")
    test_id = record.get("test_id")
    record_id = record.get("record_id")
    return bool(
        isinstance(probe, str) and probe in known_classes
        or isinstance(test_id, str) and test_id.startswith("background-proof-")
        or isinstance(record_id, str) and record_id.startswith("background-proof-")
    )


def _is_retained_legacy_observation(product: str, evidence: dict[str, Any]) -> bool:
    """Recognize only the two immutable, explicitly identified legacy sources."""
    if type(evidence.get("schema_version")) is not int or evidence.get("schema_version") != 1:
        return False
    if (
        product == "OpenCode"
        and evidence.get("product") == "OpenCode"
        and evidence.get("evidence_class") == "actual-native-observation"
        and evidence.get("run_id") == "fixture-20260922T173829Z-242424"
    ):
        return True
    return (
        product == "Codex"
        and evidence.get("provenance") == "actual-native-observation"
        and evidence.get("fixture_id") == "codex-lifecycle-marker-v1"
    )


def _resolve_proof_contract(
    product: str,
    record: dict[str, Any],
    evidence: dict[str, Any],
    declared: dict[str, dict[str, Any]],
    problems: list[str],
) -> dict[str, Any] | None:
    evidence_class = evidence.get("evidence_class")
    record_id = str(record.get("record_id", record.get("capability", "record")))
    if "record_id" not in record and _is_retained_legacy_observation(product, evidence):
        return None
    if evidence_class is None:
        if _is_background_proof_record(record) or (
            "schema_version" in evidence and "record_id" in record
        ):
            problems.append(
                f"{record_id}: versioned background proof requires explicit recognized evidence_class and schema_version"
            )
        return None
    if not isinstance(evidence_class, str) or not evidence_class.strip():
        problems.append(f"{record_id}: evidence_class must be a non-empty string")
        return None
    if evidence_class in LEGACY_EVIDENCE_CLASSES:
        problems.append(
            f"{record_id}: legacy evidence class {evidence_class!r} is accepted only for the exact retained legacy provenance"
        )
        return None

    schema_version = evidence.get("schema_version")
    recognized = [
        (contract_id, contract)
        for contract_id, contract in PROOF_CONTRACTS.items()
        if contract["evidence_class"] == evidence_class
        and _json_equal(contract["schema_version"], schema_version)
    ]
    if not recognized:
        problems.append(
            f"{record_id}: unrecognized evidence class/schema "
            f"{evidence_class!r}/{schema_version!r}"
        )
        return None

    product_contracts = [
        (contract_id, contract)
        for contract_id, contract in recognized
        if contract["product_identity"] == product
    ]
    if not product_contracts:
        supported_products = sorted(
            {contract["product_identity"] for _, contract in recognized}
        )
        problems.append(
            f"{record_id}: no registered evidence contract for product {product!r} "
            f"and class/schema {evidence_class!r}/{schema_version!r}; "
            f"supported product identities are {supported_products}"
        )
        return None

    declared_matches = [
        (contract_id, contract)
        for contract_id, contract in product_contracts
        if contract_id in declared
    ]
    if not declared_matches:
        expected_ids = sorted(contract_id for contract_id, _ in product_contracts)
        problems.append(
            f"{record_id}: {product!r} does not declare a recognized evidence "
            f"contract for class/schema {evidence_class!r}/{schema_version!r}; "
            f"declare one of {expected_ids} in harnesses.json"
        )
        return None
    if len(declared_matches) != 1:
        problems.append(
            f"{record_id}: evidence class/schema resolves to multiple "
            "product-specific contracts"
        )
        return None
    return declared_matches[0][1]


def _check_new_proof_record(
    root: Path,
    product: str,
    record: dict[str, Any],
    evidence: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    record_id = str(record.get("record_id", record.get("capability", "record")))
    problems: list[str] = []
    capability = record.get("capability")
    status = record.get("status")
    expected_harness = contract["adapter_identity"]
    expected = {
        "record_id",
        "mechanism",
        "probe",
        "scope",
        "assertions",
        "evidence_artifact",
        "test_id",
        "tested_at",
        "product_version",
    }
    missing = expected - set(record)
    if missing:
        problems.append(f"{record_id}: new proof record missing {sorted(missing)}")
    for field in ("record_id", "mechanism", "probe", "test_id", "tested_at", "product_version"):
        if not _is_real_text(record.get(field)):
            problems.append(f"{record_id}: new proof record requires a real {field}")
    if status != "verified" and not _is_real_text(record.get("note")):
        problems.append(f"{record_id}: unknown new-proof record requires a note")
    tested_at = record.get("tested_at")
    if isinstance(tested_at, str):
        try:
            if date.fromisoformat(tested_at).isoformat() != tested_at:
                raise ValueError(tested_at)
        except ValueError:
            problems.append(f"{record_id}: tested_at must be a real YYYY-MM-DD date")
    if evidence.get("passed") is not True or evidence.get("failures") != []:
        problems.append(f"{record_id}: referenced background proof did not pass cleanly")
    if evidence.get("harness") != expected_harness:
        problems.append(
            f"{record_id}: evidence harness does not match the registered adapter "
            f"identity {expected_harness!r} for {product!r}"
        )
    if not _json_equal(evidence.get("evidence_class"), contract["evidence_class"]):
        problems.append(f"{record_id}: evidence_class does not match its registered contract")
    if not _json_equal(evidence.get("schema_version"), contract["schema_version"]):
        problems.append(f"{record_id}: schema_version does not match its registered contract")
    if evidence.get("proof_id") != record.get("test_id"):
        problems.append(f"{record_id}: test_id does not match proof_id")
    if evidence.get("harness_version") != record.get("product_version"):
        problems.append(f"{record_id}: product_version does not match proof version")
    scope = record.get("scope")
    if not isinstance(scope, dict):
        problems.append(f"{record_id}: scope must be an object")
        scope = {}
    for field in ("surface", "os", "profile", "lifecycle", "evidence_scope"):
        if not _is_real_text(scope.get(field)):
            problems.append(f"{record_id}: scope.{field} must be a real string")
    surface = scope.get("surface")
    if isinstance(surface, str) and expected_harness not in surface.casefold():
        problems.append(f"{record_id}: scope.surface does not identify the proof harness")
    if scope.get("os") != evidence.get("os"):
        problems.append(f"{record_id}: scope.os does not match proof OS")
    adapter_profile = evidence.get("adapter_profile")
    if not isinstance(adapter_profile, dict):
        problems.append(f"{record_id}: proof adapter_profile must be an object")
        adapter_profile = {}
    if scope.get("profile") != adapter_profile.get("selected"):
        problems.append(f"{record_id}: scope.profile does not match proof adapter profile")
    if scope.get("evidence_scope") != evidence.get("scope"):
        problems.append(f"{record_id}: scope.evidence_scope does not match proof scope")
    if record.get("probe") != evidence.get("evidence_class"):
        problems.append(f"{record_id}: probe does not match proof evidence_class")
    expected_mechanism = PROOF_MECHANISMS.get(capability)
    if expected_mechanism is None:
        problems.append(f"{record_id}: this proof has no registered mechanism for {capability!r}")
    elif record.get("mechanism") != expected_mechanism:
        problems.append(
            f"{record_id}: mechanism {record.get('mechanism')!r} does not match "
            f"the proof contract for {capability!r} ({expected_mechanism!r})"
        )
    expected_lifecycles = PROOF_LIFECYCLES.get(capability)
    if status != "verified" and capability == "native-task-tool-operation":
        expected_lifecycles = PROOF_LIFECYCLES["native-task-tool-operation-unknown"]
    lifecycle = scope.get("lifecycle")
    if (
        expected_lifecycles is None
        or not isinstance(lifecycle, str)
        or lifecycle not in expected_lifecycles
    ):
        problems.append(f"{record_id}: scope.lifecycle is not supported by this proof contract")
    if status == "verified" and record.get("mechanism") == "native-scheduler":
        problems.append(f"{record_id}: an external-controller proof cannot establish a native scheduler")
    problems.extend(_check_assertions(record, evidence))

    if status == "verified":
        if capability not in contract["verified_capabilities"]:
            problems.append(
                f"{record_id}: contract {contract['product_identity']!r} does not "
                f"verify capability {capability!r}; it remains unknown"
            )
        else:
            positive_assertions = (
                PROOF_POSITIVE_ASSERTIONS_V2
                if contract.get("schema_version") == 2
                else PROOF_POSITIVE_ASSERTIONS
            )
            base_required = positive_assertions.get(capability)
            if base_required is None:
                problems.append(
                    f"{record_id}: no positive assertions are registered for "
                    f"capability {capability!r}"
                )
                base_required = []
            required = list(base_required)
            if capability == "stop":
                proof_id = evidence.get("proof_id")
                required.extend(
                    [
                        {"path": "cleanup.isolated_workspace_removed", "equals": True},
                        {
                            "path": f"cleanup.runs.{proof_id}.all_owned_process_groups_proven_gone",
                            "equals": True,
                        },
                        {
                            "path": f"cleanup.runs.{proof_id}-recurrence.all_owned_process_groups_proven_gone",
                            "equals": True,
                        },
                    ]
                )
            assertions = record.get("assertions")
            if not isinstance(assertions, list):
                assertions = []
            for assertion in required:
                if not any(_json_equal(assertion, candidate) for candidate in assertions):
                    problems.append(
                        f"{record_id}: verified {capability!r} lacks required proof assertion {assertion}"
                    )

    return problems


def _record_key(record: dict[str, Any]) -> tuple[str, ...]:
    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    return (
        str(record.get("capability", "")),
        str(record.get("mechanism", "not-recorded")),
        str(record.get("product_version", "not-recorded")),
        str(scope.get("surface", "not-recorded")),
        str(scope.get("os", "not-recorded")),
        str(scope.get("profile", "not-recorded")),
        str(scope.get("lifecycle", "not-recorded")),
        str(record.get("probe", "not-recorded")),
        str(record.get("test_id", "not-recorded")),
    )


def _normalise_record(product: str, record: dict[str, Any]) -> dict[str, Any]:
    capability = record["capability"]
    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    normalized_scope = dict(scope)
    normalized_scope.setdefault("profile", None)
    normalized_scope.setdefault("evidence_scope", None)
    base = {
        "product": product,
        "record_id": record.get("record_id"),
        "capability": capability,
        "status": record["status"],
        "availability": "observed" if record["status"] == "verified" else "unknown",
        "mechanism": record.get("mechanism"),
        "product_version": record.get("product_version"),
        "scope": normalized_scope,
        "probe": record.get("probe"),
        "test_id": record.get("test_id"),
        "tested_at": record.get("tested_at"),
        "evidence_artifact": record.get("evidence_artifact"),
        "observed_result": record.get("observed_result"),
        "unknown_reason": record.get("unknown_reason", record.get("note")),
        "assertions": record.get("assertions", []),
    }
    if not isinstance(base["record_id"], str) or not base["record_id"].strip():
        identity = json.dumps(
            {"product": product, "record": record},
            sort_keys=True,
            separators=(",", ":"),
        )
        base["record_id"] = f"legacy-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
    base["mechanism"] = base["mechanism"] or "not-recorded"
    return base


def _record_sort_key(record: dict[str, Any]) -> tuple[str, ...]:
    scope = record.get("scope", {})
    return (
        str(record.get("product", "")),
        str(record.get("capability", "")),
        str(record.get("mechanism", "")),
        str(record.get("product_version", "")),
        str(scope.get("surface", "")),
        str(scope.get("os", "")),
        str(scope.get("profile", "")),
        str(record.get("probe", "")),
        str(record.get("test_id", "")),
        str(record.get("record_id", "")),
    )


def _is_real_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.strip().startswith("<")
        and "<" not in value
        and ">" not in value
        and value.strip().casefold() not in {"unknown", "missing", "not_recorded", "n/a"}
    )


def project_capability_records(
    root: Path,
    entry: dict[str, Any],
    *,
    selectors: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic, unpooled records matching exact selectors.

    Selectors may use ``capability``, ``mechanism``, ``product_version``,
    ``probe``, ``test_id``, or one of ``scope.surface``, ``scope.os``,
    ``scope.profile``, and ``scope.lifecycle``. Matching is exact and
    case-sensitive. All records on the selected harness entry are validated
    before filtering so a malformed sibling cannot silently disappear.
    """
    if not isinstance(entry, dict):
        raise CapabilityProjectionError("harness entry must be an object")
    product = entry.get("product")
    if not isinstance(product, str) or not product.strip():
        raise CapabilityProjectionError("harness entry has no product name")
    legacy = LEGACY_AUTHORITY_KEYS.intersection(entry)
    if legacy:
        raise CapabilityProjectionError(
            f"{product}: competing capability authorities remain: {sorted(legacy)}; "
            "move them into capability_records"
        )
    records = entry.get("capability_records")
    if not isinstance(records, list):
        raise CapabilityProjectionError(f"{product}: capability_records must be a list")

    problems: list[str] = []
    declared_contracts = _declared_proof_contracts(product, entry, problems)
    projected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    records_by_scope: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(records):
        where = f"{product}.capability_records[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: record must be an object")
            continue
        capability = item.get("capability")
        status = item.get("status")
        if not isinstance(capability, str) or not capability.strip():
            problems.append(f"{where}: capability id is required")
            continue
        if capability not in CAPABILITY_IDS:
            problems.append(f"{where}: unknown capability id {capability!r}")
        if not isinstance(status, str) or status not in STATUSES:
            problems.append(f"{where}: status must be one of {sorted(STATUSES)}")
            continue
        record = _normalise_record(product, item)
        record_id = record["record_id"]
        if record_id in seen_ids:
            problems.append(f"{where}: duplicate record_id {record_id!r}")
        seen_ids.add(record_id)
        if item.get("mechanism") is not None and (
            not isinstance(item.get("mechanism"), str) or not item["mechanism"].strip()
        ):
            problems.append(f"{where}: mechanism must be a non-empty string when recorded")

        artifact = item.get("evidence_artifact")
        evidence: dict[str, Any] | None = None
        if status == "verified":
            for field in (
                "evidence_artifact",
                "test_id",
                "tested_at",
                "product_version",
                "scope",
                "observed_result",
            ):
                if field not in item:
                    problems.append(f"{where}: verified record is missing {field!r}")
            for field in ("evidence_artifact", "test_id", "tested_at", "product_version", "observed_result"):
                if not _is_real_text(item.get(field)):
                    problems.append(f"{where}: verified record requires a real {field}")
            for field in ("mechanism", "probe"):
                if not _is_real_text(item.get(field)):
                    problems.append(f"{where}: verified record requires a real {field}")
            tested_at = item.get("tested_at")
            if isinstance(tested_at, str):
                try:
                    if date.fromisoformat(tested_at).isoformat() != tested_at:
                        raise ValueError(tested_at)
                except ValueError:
                    problems.append(f"{where}: tested_at must be a real YYYY-MM-DD date")
            if not isinstance(item.get("scope"), dict):
                problems.append(f"{where}: verified record scope must be an object")
            else:
                for field in ("surface", "os", "lifecycle"):
                    if not _is_real_text(item["scope"].get(field)):
                        problems.append(f"{where}: verified record scope.{field} is required")
                for field in ("profile", "evidence_scope"):
                    value = item["scope"].get(field)
                    if value is not None and not _is_real_text(value):
                        problems.append(
                            f"{where}: verified record scope.{field} must be a string when recorded"
                        )
            observed = item.get("observed_result")
            outcome = item.get("outcome")
            passing = (
                isinstance(outcome, str) and outcome.strip().casefold() in {"pass", "passed"}
            ) or (
                isinstance(observed, str)
                and observed.strip().casefold().startswith(("pass:", "pass ", "passed"))
            )
            if not passing:
                problems.append(f"{where}: verified record must contain a passing observed_result")
            if isinstance(artifact, str):
                try:
                    evidence = _read_artifact(root, artifact, record_id)
                except ValueError as exc:
                    problems.append(str(exc))
            else:
                problems.append(f"{where}: verified record requires evidence_artifact")
        elif artifact is not None:
            try:
                evidence = _read_artifact(root, artifact, record_id)
            except ValueError as exc:
                problems.append(str(exc))

        if evidence is not None and isinstance(item.get("assertions", []), list):
            problems.extend(_check_assertions(item, evidence))
        elif "assertions" in item and not isinstance(item.get("assertions"), list):
            problems.append(f"{where}: assertions must be a list")

        if evidence is not None:
            proof_contract = _resolve_proof_contract(
                product,
                item,
                evidence,
                declared_contracts,
                problems,
            )
            if proof_contract is not None:
                problems.extend(
                    _check_new_proof_record(root, product, item, evidence, proof_contract)
                )
            elif status == "verified" and item.get("mechanism") in {
                "external-background-controller",
                "external-controller",
            }:
                problems.append(
                    f"{where}: verified external-controller evidence requires a "
                    "recognized product-specific evidence-class/schema contract"
                )

        records_by_scope[_record_key(item)].append(item)
        projected.append(record)

    for key, scoped_records in records_by_scope.items():
        statuses = {record.get("status") for record in scoped_records}
        if len(statuses) > 1:
            problems.append(
                "contradictory capability records share exact scope "
                f"{key!r} with statuses {sorted(statuses)}"
            )
        elif len(scoped_records) > 1:
            problems.append(
                f"duplicate capability records share exact scope {key!r}; "
                "retain one canonical record per exact test/scope"
            )

    if selectors is not None:
        if not isinstance(selectors, Mapping):
            problems.append("selectors must be a mapping of exact record dimensions")
        else:
            unknown = set(selectors) - SELECTOR_KEYS
            if unknown:
                problems.append(
                    f"unsupported capability selectors: {sorted(map(str, unknown))}"
                )
            for key, value in selectors.items():
                if not isinstance(value, str):
                    problems.append(f"selector {key!r} must be a string")
    if problems:
        raise CapabilityProjectionError(problems)

    result = sorted(projected, key=_record_sort_key)
    if selectors:
        exact = dict(selectors)
        matches: list[dict[str, Any]] = []
        for record in result:
            matched = True
            for key, value in exact.items():
                try:
                    observed = _get_path(record, key)
                except KeyError:
                    matched = False
                    break
                if observed != value:
                    matched = False
                    break
            if matched:
                matches.append(record)
        result = matches
    return result


def render_capability_projection(root: Path) -> str:
    """Render a generated Markdown summary from the canonical registry records."""
    try:
        registry = json.loads((root / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityProjectionError(f"cannot read {REGISTRY_REL}: {exc}") from exc
    if not isinstance(registry, list):
        raise CapabilityProjectionError(f"{REGISTRY_REL} must contain a JSON array")

    def display(value: Any) -> str:
        return "not-recorded" if value is None or value == "" else str(value)

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for entry in registry:
        if not isinstance(entry, dict):
            raise CapabilityProjectionError("harness registry entries must be objects")
        for record in project_capability_records(root, entry):
            scope = record.get("scope", {})
            evidence_artifact = record.get("evidence_artifact")
            group_key = (
                record["product"],
                record["availability"],
                display(record["mechanism"]),
                display(record["product_version"]),
                display(scope.get("surface")),
                display(scope.get("os")),
                display(scope.get("profile")),
                display(record["probe"]),
                display(record["test_id"]),
                display(evidence_artifact),
            )
            grouped[group_key].append(record)

    rows = [
        "| Product | State | Mechanism | Version / surface / OS / profile | Capability records (individual lifecycle) | Probe / test id | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]

    for key in sorted(grouped):
        (
            product,
            availability,
            mechanism,
            version,
            surface,
            os_name,
            profile,
            probe,
            test_id,
            artifact,
        ) = key
        caps = sorted(
            f"`{record['capability']}` — {display(record.get('scope', {}).get('lifecycle'))}"
            for record in grouped[key]
        )
        scope = " / ".join((version, surface, os_name, profile))
        if artifact == "not-recorded":
            evidence_link = "not recorded"
        else:
            evidence_link = f"[`JSON`](../../{artifact})"
        cells = (product, availability, mechanism, scope, "<br>".join(caps), f"{probe} / {test_id}", evidence_link)
        escaped = [cell.replace("|", "\\|").replace("\n", " ") for cell in cells]
        rows.append("| " + " | ".join(escaped) + " |")
    if len(rows) == 2:
        rows.append("| No scoped capability records | — | — | — | — | — | — |")
    return "\n".join(rows)


def replace_capability_projection(text: str, replacement: str, relpath: str) -> str:
    """Replace the single generated projection block without touching prose."""
    if text.count(PROJECTION_BEGIN_MARKER) != 1 or text.count(PROJECTION_END_MARKER) != 1:
        raise CapabilityProjectionError(
            f"{relpath} must contain exactly one generated capability projection marker pair"
        )
    start = text.index(PROJECTION_BEGIN_MARKER)
    finish = text.index(PROJECTION_END_MARKER) + len(PROJECTION_END_MARKER)
    if finish < start:
        raise CapabilityProjectionError(f"{relpath} capability projection markers are reversed")
    block = f"{PROJECTION_BEGIN_MARKER}\n\n{replacement}\n{PROJECTION_END_MARKER}"
    return text[:start] + block + text[finish:]
