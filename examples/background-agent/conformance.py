#!/usr/bin/env python3
"""Check a retained background proof or run inert synthetic conformance cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from background_adapters.conformance import EDGE_IDS, evaluate_proof_summary, selfcheck  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA = Path(__file__).resolve().parent / "conformance.schema.json"


def _validate_shape(report: dict) -> list[str]:
    failures = []
    required = {
        "schema_version", "evidence_class", "synthetic", "product", "adapter_id",
        "product_version", "os", "profile", "overall", "scope", "request_ack_bindings",
        "external_controller_overall", "edges", "native_primitive_observations", "capability_projection",
    }
    if required - set(report):
        failures.append("conformance report lacks required top-level fields")
    if report.get("evidence_class") != "background-agent-conformance-report" or report.get("schema_version") != 1:
        failures.append("conformance report class/schema is not recognized")
    edges = report.get("edges")
    if not isinstance(edges, dict) or set(edges) != set(EDGE_IDS):
        failures.append("conformance report must include each lifecycle edge exactly once")
    elif any(not isinstance(edge, dict) or edge.get("status") not in {"passed", "failed", "unknown", "not_run"}
             for edge in edges.values()):
        failures.append("one or more lifecycle edges has an invalid status")
    projection = report.get("capability_projection")
    if not isinstance(projection, dict) or projection.get("automatic_registry_promotion") is not False:
        failures.append("conformance report must keep the canonical registry under manual review")
    if not SCHEMA.is_file():
        failures.append("conformance JSON Schema is missing")
    return failures


def _read_proof(path_value: str) -> dict:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ValueError("proof path must be a regular non-symlink file")
    proof = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(proof, dict):
        raise ValueError("proof root must be a JSON object")
    return proof


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selfcheck", action="store_true", help="run synthetic-only checks; no model or harness launched")
    group.add_argument("--proof", help="read one retained sanitized proof JSON and report each edge")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selfcheck:
        failures = selfcheck(FIXTURES)
        try:
            proof = json.loads((FIXTURES / "conformance-pass.synthetic.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proof = {}
            failures.append("synthetic conformance fact fixture is unreadable")
        report = evaluate_proof_summary(proof, synthetic=True)
        failures.extend(_validate_shape(report))
        if failures:
            print(json.dumps({"ok": False, "claim": "synthetic-conformance-selfcheck-only", "failures": failures}, sort_keys=True))
            return 1
        print(json.dumps({
            "ok": True,
            "claim": "synthetic-conformance-selfcheck-only",
            "edge_count": len(EDGE_IDS),
            "native_calls": 0,
            "model_calls": 0,
        }, sort_keys=True))
        return 0

    try:
        proof = _read_proof(args.proof)
        report = evaluate_proof_summary(proof, root=REPO_ROOT)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    failures = _validate_shape(report)
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report.get("external_controller_overall") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
