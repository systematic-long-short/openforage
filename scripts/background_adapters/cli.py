#!/usr/bin/env python3
"""Read-only adapter discovery/probing, offline conformance, and scaffolding."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from background_adapters import AdapterError, adapter_ids, observe_read_only_probe  # noqa: E402
from background_adapters.scaffold import ScaffoldError, create_scaffold  # noqa: E402
from background_adapters.selfcheck import run_selfcheck  # noqa: E402

LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")


def _json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    check = subparsers.add_parser("discover", help="prepare a read-only evidence checklist for an unsupported or unresolved product")
    check.add_argument("--product-label", required=True, help="caller-provided label; it is not treated as verified identity")
    check.add_argument("--primary-source", action="append", default=[], help="caller-provided official-source URL; not fetched or verified")
    check.add_argument("--version", help="caller-reported installed version; not probed or verified")

    probe = subparsers.add_parser("probe", help="read version and --help for one built-in adapter; no session launch")
    probe.add_argument("--adapter", choices=adapter_ids(), required=True)
    probe.add_argument("--binary", required=True, help="explicit local executable selection")

    scaffold = subparsers.add_parser("scaffold", help="create an inert adapter skeleton in a new caller-selected directory")
    scaffold.add_argument("--directory", required=True)

    subparsers.add_parser("selfcheck", help="run only synthetic offline adapter/conformance checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "discover":
        if not isinstance(args.product_label, str) or LABEL_RE.fullmatch(args.product_label) is None:
            _json({"ok": False, "error": "product_label_invalid"})
            return 2
        _json({
            "schema_version": 1,
            "route": "read-only-discovery",
            "status": "discovery-only",
            "caller_label_received": True,
            "caller_label_sha256": hashlib.sha256(args.product_label.encode("utf-8")).hexdigest(),
            "identity_status": "caller-supplied-unverified",
            "primary_sources": [
                {
                    "fingerprint_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    "status": "caller-supplied-unverified",
                }
                for value in args.primary_source
            ],
            "caller_reported_version_received": bool(args.version),
            "caller_reported_version_sha256": hashlib.sha256(args.version.encode("utf-8")).hexdigest()
            if args.version else None,
            "version_status": "caller-supplied-unverified" if args.version else "unknown",
            "built_in_adapter_ids": list(adapter_ids()),
            "capability_observations": "unknown",
            "next_steps": [
                "Confirm the exact vendor/product identity from an official primary source.",
                "Read that product's official CLI/event documentation and local --help without starting a session.",
                "Use the caller-requested scaffold only in a new chosen directory, then run the inert conformance checklist.",
            ],
            "commands_guessed": False,
            "session_launched": False,
            "credentials_inspected": False,
            "configuration_mutated": False,
            "installation_attempted": False,
        })
        return 0

    if args.action == "probe":
        try:
            result = observe_read_only_probe(args.adapter, args.binary)
        except AdapterError as exc:
            _json({"ok": False, "error": exc.code, "session_launched": False})
            return 1
        _json({"ok": True, **result})
        return 0 if result["help_probe"]["outcome"] == "observed_success" else 1

    if args.action == "scaffold":
        try:
            result = create_scaffold(args.directory)
        except ScaffoldError as exc:
            _json({"ok": False, "error": str(exc)})
            return 1
        _json({"ok": True, **result})
        return 0

    failures = run_selfcheck()
    if failures:
        _json({"ok": False, "claim": "synthetic-offline-adapter-selfcheck-only", "failures": failures})
        return 1
    _json({
        "ok": True,
        "claim": "synthetic-offline-adapter-selfcheck-only",
        "adapter_ids": list(adapter_ids()),
        "native_calls": 0,
        "model_calls": 0,
        "session_launched": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
