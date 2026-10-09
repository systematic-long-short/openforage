#!/usr/bin/env python3
"""Require two native harness observations to share one prepared input snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from manifest import ManifestError, verify_manifest  # noqa: E402
from paths import PathSafetyError, safe_relative  # noqa: E402
from schema import read_json  # noqa: E402


class ComparisonError(RuntimeError):
    """The two observations cannot be compared as the same frozen input."""


def assert_same_prepared_input(opencode: dict, codex: dict) -> None:
    for field in ("source_tree_sha256", "snapshot_sha256", "initial_file_sha256"):
        if opencode.get(field) != codex.get(field):
            raise ComparisonError(f"trials were not prepared from the same clean input snapshot ({field} differs)")


def assert_snapshot_pin(opencode: dict, codex: dict) -> None:
    if codex.get("expected_snapshot_sha256") != opencode.get("snapshot_sha256"):
        raise ComparisonError("Codex trial was not launched with the OpenCode prepared snapshot pinned")
    if opencode.get("expected_snapshot_sha256") not in {None, opencode.get("snapshot_sha256")}:
        raise ComparisonError("OpenCode trial did not satisfy its supplied snapshot pin")


def _read_trial(repo: Path, relative: str) -> tuple[dict, dict]:
    trial_rel = safe_relative(relative)
    if trial_rel.parts[:3] != (".tmp", "native-contribution-wave", "runs"):
        raise ComparisonError("trial must be under ignored v2 run evidence")
    trial_path = repo / trial_rel
    if trial_path.is_symlink() or not trial_path.is_file() or not trial_path.resolve().is_relative_to(repo):
        raise ComparisonError("trial record path is missing or unsafe")
    trial = read_json(trial_path)
    verdict_path = trial_path.parent / "verdict.json"
    if verdict_path.is_symlink() or not verdict_path.is_file():
        raise ComparisonError("original verdict is missing; regrades cannot replace it")
    verdict = read_json(verdict_path)
    if verdict.get("success") is not True or verdict.get("synthetic_regression") is True:
        raise ComparisonError("only successful fresh-native trial verdicts can be compared")
    return trial, verdict


def compare_trials(repo: Path, opencode_record: str, codex_record: str) -> dict:
    repo = repo.resolve()
    opencode, opencode_verdict = _read_trial(repo, opencode_record)
    codex, codex_verdict = _read_trial(repo, codex_record)
    if {opencode.get("harness"), codex.get("harness")} != {"opencode", "codex"}:
        raise ComparisonError("comparison requires one OpenCode and one Codex trial")
    if opencode.get("case_id") != "first-finished-contribution-primary" or codex.get("case_id") != opencode.get("case_id"):
        raise ComparisonError("comparison requires the same primary case; held-out results remain separate")
    manifest_paths = {opencode.get("series_manifest_path"), codex.get("series_manifest_path")}
    if len(manifest_paths) != 1 or not isinstance(next(iter(manifest_paths)), str):
        raise ComparisonError("trials do not name the same selected series manifest")
    manifest_path = next(iter(manifest_paths))
    manifest, digest = verify_manifest(repo, manifest_path)
    if any(record.get("series_id") != manifest.get("series_id") for record in (opencode, codex)):
        raise ComparisonError("trial series ids differ from the selected manifest")
    if any(record.get("series_manifest_sha256") != digest for record in (opencode, codex)):
        raise ComparisonError("trial manifest hashes do not match the selected frozen series")
    assert_same_prepared_input(opencode, codex)
    assert_snapshot_pin(opencode, codex)
    op_artifacts = opencode_verdict.get("artifact_refs", {})
    codex_artifacts = codex_verdict.get("artifact_refs", {})
    op_docs = {path: digest for path, digest in op_artifacts.items() if path != "trial-output/completion.json"}
    codex_docs = {path: digest for path, digest in codex_artifacts.items() if path != "trial-output/completion.json"}
    if not op_docs or set(op_docs) != set(codex_docs):
        raise ComparisonError("trials do not contain the same corrected documentation artifact path")
    return {
        "status": "same-input-snapshot",
        "series_id": manifest["series_id"],
        "series_manifest_sha256": digest,
        "case_id": opencode["case_id"],
        "source_tree_sha256": opencode["source_tree_sha256"],
        "prepared_snapshot_sha256": opencode["snapshot_sha256"],
        "changed_document_artifacts": {"opencode": op_docs, "codex": codex_docs},
        "harnesses": ["opencode", "codex"],
        "requested_arguments": {
            "opencode": {
                "model_selector": opencode.get("requested_model_selector"),
                "variant": opencode.get("requested_variant"),
            },
            "codex": {
                "model_selector": codex.get("requested_model_selector"),
                "reasoning_effort": codex.get("requested_reasoning_effort"),
            },
            "backend_served_model": "not_observed",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode-trial", required=True)
    parser.add_argument("--codex-trial", required=True)
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    args = parser.parse_args(argv)
    try:
        result = compare_trials(Path(args.repo), args.opencode_trial, args.codex_trial)
    except (OSError, ValueError, ComparisonError, ManifestError, PathSafetyError) as exc:
        print(f"FAIL: v2 comparison: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
