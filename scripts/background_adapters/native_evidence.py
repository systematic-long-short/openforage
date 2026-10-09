"""Typed native-operation evidence derived only from normalized tool events."""

from __future__ import annotations

import hashlib
from typing import Any

from .contract import PrimitiveDeclaration

OPERATION_IDS = (
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

LEGACY_GROUPS = {
    "goal": ("goal_declaration",),
    "task": ("task_creation", "task_state_update", "task_list_write", "task_durable_reread"),
    "memory": ("memory_write", "memory_durable_reread"),
}

OUTCOMES = {
    "observed_success",
    "observed_failure",
    "observed_in_progress",
    "observed_mixed",
    "unavailable",
    "unknown",
}


def session_fingerprint(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _declaration_map(declarations: tuple[PrimitiveDeclaration, ...]) -> dict[str, PrimitiveDeclaration]:
    return {item.operation: item for item in declarations}


def _outcome(events: list[dict[str, Any]], *, declaration: PrimitiveDeclaration | None,
             operation: str) -> tuple[str, str | None]:
    if declaration is not None and declaration.availability == "unavailable":
        return "unavailable", "The adapter explicitly declares this operation unavailable."
    if not events:
        return "unknown", "No matching operation event with an explicit outcome was observed."

    statuses = {event.get("status") for event in events if isinstance(event.get("status"), str)}
    successes = statuses.intersection({"completed", "success", "succeeded"})
    failures = statuses.intersection({"failed", "error", "denied", "rejected", "cancelled", "canceled"})
    running = statuses.intersection({"running", "in_progress", "pending"})

    # A tool invocation is not enough to prove that a durable task was read
    # back. Require explicit, matching task identity on both sides of a native
    # result event; planning metadata and final prose are not evidence.
    if operation == "task_durable_reread":
        matched_readback = any(
            event.get("status") in {"completed", "success", "succeeded"}
            and event.get("durable_readback") is True
            and isinstance(event.get("resource_id"), str)
            and event.get("resource_id") == event.get("result_resource_id")
            for event in events
        )
        if not matched_readback:
            return "unknown", "No completed native readback was correlated to the created task identity."

    # A native schedule is only delivered when the adapter exposes a concrete
    # delivery event with a delivery identity. External controller due rows do
    # not enter this adapter-native event list.
    if operation == "native_scheduler_delivery":
        delivered = any(
            event.get("native_delivery") is True
            and isinstance(event.get("delivery_id"), str)
            and event.get("status") in {"completed", "success", "succeeded"}
            for event in events
        )
        if not delivered:
            return "unknown", "No real native scheduler delivery event with an identity was observed."

    if successes and failures:
        return "observed_mixed", None
    if successes:
        return "observed_success", None
    if failures:
        return "observed_failure", None
    if running:
        return "observed_in_progress", None
    return "unknown", "Matching tool identity was present without an explicit terminal outcome."


def native_primitive_observations(
    adapter_id: str,
    events: list[dict[str, Any]] | None,
    *,
    declarations: tuple[PrimitiveDeclaration, ...],
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return separate operation outcomes, evidence identities, and scope.

    ``declared_primitives`` contributes only availability metadata. Only an
    event normalized from a product trace can provide an observed outcome.
    """
    normalized_scope = dict(scope or {})
    normalized_scope.setdefault("adapter_id", adapter_id)
    normalized_scope.setdefault("mechanism", "native-tool-event")
    declaration_by_operation = _declaration_map(declarations)
    native_events = [event for event in (events or []) if isinstance(event, dict)]
    operations: dict[str, dict[str, Any]] = {}

    for operation in OPERATION_IDS:
        declaration = declaration_by_operation.get(operation)
        names = set(declaration.tool_names if declaration else ())
        matched = [
            event for event in native_events
            if (event.get("operation") == operation
                or (isinstance(event.get("tool"), str) and event["tool"] in names))
        ]
        outcome, reason = _outcome(matched, declaration=declaration, operation=operation)
        evidence = []
        for event in matched:
            event_scope = event.get("scope") if isinstance(event.get("scope"), dict) else {}
            combined_scope = {**normalized_scope, **event_scope}
            for key, value in normalized_scope.items():
                if combined_scope.get(key) is None:
                    combined_scope[key] = value
            row = {
                "tool": event.get("tool") if isinstance(event.get("tool"), str) else None,
                "event_type": event.get("event_type") if isinstance(event.get("event_type"), str) else None,
                "event_ref": event.get("event_ref") if isinstance(event.get("event_ref"), str) else None,
                "status": event.get("status") if isinstance(event.get("status"), str) else None,
                "scope": combined_scope,
            }
            if isinstance(event.get("operation"), str):
                row["operation"] = event["operation"]
            if event.get("native_delivery") is True:
                row["native_delivery"] = True
                if isinstance(event.get("delivery_id"), str):
                    row["delivery_id"] = event["delivery_id"]
            if event.get("durable_readback") is True:
                row["durable_readback"] = True
                row["resource_id"] = event.get("resource_id")
                row["result_resource_id"] = event.get("result_resource_id")
            evidence.append(row)
        operations[operation] = {
            "availability": declaration.availability if declaration else "unknown",
            "outcome": outcome,
            "tool_names_declared": list(declaration.tool_names) if declaration else [],
            "evidence": evidence,
            "scope": normalized_scope,
            "reason": reason,
            "declaration_basis": declaration.basis if declaration else "No adapter declaration exists.",
        }
    return {
        "schema_version": 1,
        "evidence_class": "typed-native-operation-observations",
        "operations": operations,
        "external_delivery_is_native_scheduler_evidence": False,
    }


def aggregate_native_primitive_observations(
    observations: list[dict[str, Any]] | None,
    *,
    declarations: tuple[PrimitiveDeclaration, ...],
    adapter_id: str,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge same-run turns without pooling product/version/profile scopes."""
    all_events: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    for observation in observations or []:
        if not isinstance(observation, dict) or not isinstance(observation.get("operations"), dict):
            continue
        for operation in OPERATION_IDS:
            row = observation["operations"].get(operation)
            if not isinstance(row, dict):
                continue
            row_scope = row.get("scope")
            if isinstance(row_scope, dict):
                scopes.append(row_scope)
            evidence = row.get("evidence")
            if isinstance(evidence, list):
                all_events.extend(
                    event for event in evidence
                    if isinstance(event, dict) and isinstance(event.get("scope"), dict)
                )
    # If the caller did not supply one fixed run scope, retain an explicit
    # unknown marker rather than pooling heterogeneous observations.
    selected_scope = dict(scope or {})
    distinct_scopes = {
        tuple(sorted((key, repr(value)) for key, value in item.items()))
        for item in scopes
    }
    if len(distinct_scopes) == 1 and not selected_scope:
        selected_scope = dict(scopes[0])
    elif len(distinct_scopes) > 1:
        selected_scope = {"scope_consistency": "mixed_or_unknown"}
    return native_primitive_observations(
        adapter_id,
        all_events,
        declarations=declarations,
        scope=selected_scope,
    )


def legacy_group_projection(observations: dict[str, Any]) -> dict[str, Any]:
    """Compatibility-shaped goal/task/memory summary from typed operations."""
    operations = observations.get("operations", {}) if isinstance(observations, dict) else {}
    result: dict[str, Any] = {}
    for group, ids in LEGACY_GROUPS.items():
        rows = [operations.get(operation) for operation in ids if isinstance(operations.get(operation), dict)]
        evidence = [
            item
            for row in rows
            for item in row.get("evidence", [])
            if isinstance(item, dict)
        ]
        outcomes = {row.get("outcome") for row in rows}
        if "observed_success" in outcomes:
            status = "observed_success"
        elif "observed_failure" in outcomes:
            status = "observed_failure"
        elif "observed_mixed" in outcomes:
            status = "mixed"
        elif "observed_in_progress" in outcomes:
            status = "observed_in_progress"
        else:
            status = "unknown"
        result[group] = {"status": status, "tools": evidence}
    result["operations"] = operations
    result["external_delivery_is_native_scheduler_evidence"] = False
    return result
