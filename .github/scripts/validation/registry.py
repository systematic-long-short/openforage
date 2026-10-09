"""Harness routing registry and evidence-gated capability validation.

The validator checks consistency and presence, never the actual truth of an
observation. A static check cannot confirm that a fixture ran; it can only
confirm that a claimed verified entry carries a real test id, a calendar date,
a typed scope, an existing in-repository evidence artifact, and a passing
observed result, and that an aggregate ``verified`` entry does not combine
unrelated versions, surfaces, or operating systems.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from onboarding.capabilities import (  # noqa: E402
    CAPABILITY_IDS,
    CapabilityProjectionError,
    project_capability_records,
)

from .common import (
    is_calendar_date,
    read_text,
    rel,
)

REGISTRY_REL = "src/openforage/skills/agent_runtime_integration/references/harnesses.json"
REGISTRY_STATUSES = {"documented-unverified", "discovery-only", "unresolved", "verified"}

REQUIRED_CAPABILITIES = {
    "distinct-session-identity",
    "separate-cli-invocation",
    "parent-independence",
    "native-goal-task-memory",
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
}

KNOWN_CAPABILITIES = CAPABILITY_IDS

REGISTRY_REQUIRED_KEYS = {
    "product",
    "identity",
    "surface",
    "status",
    "verified_on",
    "sources",
    "native_controls",
    "separate_session_evidence",
    "scheduler_evidence",
    "lifecycle_evidence",
    "capability_records",
    "route",
}


def _check_verified_consistency(report, where: str, verified_items) -> None:
    """A fully verified entry must not pool incompatible record dimensions."""
    if len(verified_items) < 2:
        return
    for field in ("product_version", "mechanism", "probe", "test_id"):
        values = {
            (type(value), value)
            for item in verified_items
            if (value := item.get(field)) is None or isinstance(value, str)
        }
        if len(values) > 1:
            report.fail(
                f"{where}: verified capabilities pool unrelated {field} values {sorted(map(str, values))}"
            )
    for key in ("surface", "os", "profile", "lifecycle"):
        values = set()
        for item in verified_items:
            scope = item.get("scope")
            if not isinstance(scope, dict):
                continue
            value = scope.get(key)
            if value is None or isinstance(value, str):
                values.add((type(value), value))
            else:
                report.fail(f"{where}: verified scope.{key} must be a string when recorded")
        if len(values) > 1:
            report.fail(
                f"{where}: verified capabilities pool unrelated scope.{key} values "
                f"{sorted(map(str, values))}"
            )


def check_registry(report, root: Path) -> None:
    registry_path = root / REGISTRY_REL
    references_dir = registry_path.parent
    if not registry_path.is_file():
        report.fail(f"adapter registry missing: {REGISTRY_REL}")
        return
    try:
        entries = json.loads(read_text(registry_path))
    except json.JSONDecodeError as exc:
        report.fail(f"{REGISTRY_REL}: invalid JSON: {exc}")
        return
    if not isinstance(entries, list) or not entries:
        report.fail(f"{REGISTRY_REL}: expected a non-empty JSON array")
        return

    products = set()
    routed: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"{REGISTRY_REL}[{index}]"
        if not isinstance(entry, dict):
            report.fail(f"{where}: entry must be an object")
            continue
        missing = REGISTRY_REQUIRED_KEYS - set(entry)
        if missing:
            report.fail(f"{where}: missing keys {sorted(missing)}")
        product = entry.get("product")
        if isinstance(product, str) and product:
            if product in products:
                report.fail(f"{where}: duplicate product {product!r}")
            products.add(product)
        status = entry.get("status")
        if not isinstance(status, str) or status not in REGISTRY_STATUSES:
            report.fail(
                f"{where}: status {status!r} not in {sorted(REGISTRY_STATUSES)}; only "
                "per-capability verified entries with evidence are accepted"
            )
        verified_on = entry.get("verified_on")
        if verified_on is not None and not is_calendar_date(verified_on):
            report.fail(f"{where}: verified_on must be null or a real calendar date")

        forbidden = {"verified_capabilities", "external_background_adapter_evidence"} & set(entry)
        if forbidden:
            report.fail(
                f"{where}: competing capability authorities remain {sorted(forbidden)}; "
                "use capability_records only"
            )
        capabilities = entry.get("capability_records")
        try:
            projected = project_capability_records(root, entry)
        except CapabilityProjectionError as exc:
            for problem in exc.problems:
                report.fail(f"{where}: {problem}")
            projected = []
        verified_items = [item for item in projected if item.get("status") == "verified"]
        for cap_index, item in enumerate(capabilities if isinstance(capabilities, list) else []):
            spot = f"{where}.capability_records[{cap_index}]"
            if not isinstance(item, dict):
                continue
            capability = item.get("capability")
            if isinstance(capability, str) and capability not in KNOWN_CAPABILITIES:
                report.fail(f"{spot}: unknown capability id {capability!r}")
        capability_ids = {
            item.get("capability")
            for item in verified_items
            if isinstance(item.get("capability"), str)
        }

        if status == "verified":
            if not is_calendar_date(verified_on):
                report.fail(f"{where}: a verified entry requires a real verified_on date")
            if not capabilities:
                report.fail(
                    f"{where}: a verified entry requires per-capability evidence entries"
                )
            missing_caps = REQUIRED_CAPABILITIES - capability_ids
            if missing_caps:
                report.fail(
                    f"{where}: a verified adapter must verify every required capability; "
                    f"missing {sorted(missing_caps)}"
                )
            _check_verified_consistency(report, where, verified_items)

        for key in ("sources", "native_controls"):
            if not isinstance(entry.get(key), list):
                report.fail(f"{where}: {key} must be a list")
        for key in (
            "identity",
            "surface",
            "separate_session_evidence",
            "scheduler_evidence",
            "lifecycle_evidence",
        ):
            if not isinstance(entry.get(key), str) or not entry.get(key).strip():
                report.fail(f"{where}: {key} must be a non-empty string")
        route = entry.get("route")
        if not isinstance(route, str) or not route.startswith("references/"):
            report.fail(f"{where}: route must be a 'references/...' path")
            continue
        target = references_dir / route[len("references/"):]
        if not target.is_file():
            report.fail(f"{where}: route target does not exist: {route}")
        else:
            routed.add(target.name)


    for page in sorted(references_dir.glob("*.md")):
        if page.name not in routed:
            report.fail(
                f"{rel(root, page)}: routing page is not referenced by any registry entry"
            )
