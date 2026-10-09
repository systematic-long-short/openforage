#!/usr/bin/env python3
"""Create or verify the immutable v2 series manifest; never launch a harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from manifest import (
    HISTORICAL_SERIES1_REL, SERIES_OUTPUT_DIR, ManifestError, build_manifest,
    verify_historical_series1, verify_manifest,
)  # noqa: E402
from paths import PathSafetyError, mkdir_chain_no_symlinks, safe_relative  # noqa: E402


def _write_manifest_once(path: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true", help="create a named rubric freeze at a new path")
    action.add_argument("--check", action="store_true", help="verify one explicitly selected series closure")
    action.add_argument("--archived-series1-drift-check", action="store_true",
                        help="explicitly audit the byte-preserved never-run series-1 draft")
    parser.add_argument("--series-id", help="new unique series id; required with --create")
    parser.add_argument("--output", help=f"new manifest path under {SERIES_OUTPUT_DIR}/; required with --create")
    parser.add_argument("--series-manifest", help="selected manifest path; required with --check")
    parser.add_argument("--repo", default=str(HERE.parents[2]))
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    try:
        if args.create:
            if not args.series_id or not args.output:
                raise ManifestError("--create requires both --series-id and --output")
            relative = safe_relative(args.output)
            if (
                len(relative.parts) != len(Path(SERIES_OUTPUT_DIR).parts) + 1
                or relative.parts[:-1] != Path(SERIES_OUTPUT_DIR).parts
                or relative.name != f"{args.series_id}.json"
            ):
                raise ManifestError("new manifests must be named <series-id>.json under the v2 series directory")
            parent = root / relative.parent
            mkdir_chain_no_symlinks(parent)
            payload = build_manifest(root, args.series_id)
            output = root / relative
            _write_manifest_once(output, payload)
            print(json.dumps({"created": relative.as_posix(), "series_id": args.series_id,
                              "frozen_files": len(payload["frozen_files"]),
                              "snapshot_identity": "recorded per trial"}, indent=2))
        elif args.check:
            if not args.series_manifest:
                raise ManifestError("--check requires --series-manifest; series selection is explicit")
            manifest, digest = verify_manifest(root, args.series_manifest)
            print(json.dumps({"series_id": manifest["series_id"], "manifest_path": args.series_manifest,
                              "manifest_sha256": digest, "frozen_files": len(manifest["frozen_files"]),
                              "drift": False}, indent=2))
        else:
            result = verify_historical_series1(root)
            print(json.dumps(result, indent=2, sort_keys=True))
            if not result["replayable"]:
                return 1
    except (OSError, ManifestError, PathSafetyError) as exc:
        print(f"FAIL: freeze check: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
