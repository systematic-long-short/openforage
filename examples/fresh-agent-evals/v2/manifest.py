"""Versioned rubric freeze and source-closure identity helpers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

from schema import loads_strict

HERE = Path(__file__).resolve().parent
HISTORICAL_SERIES1_REL = "examples/fresh-agent-evals/v2/series.json"
HISTORICAL_SERIES1_SHA256 = "00d615cf24e0daf6742deace4a78e92a7b460880c1642e0db2a5fbe8aac8fa73"
SERIES_OUTPUT_DIR = "examples/fresh-agent-evals/v2/series"
EXCLUDED_DIRS = {
    ".git", ".tmp", ".venv", ".venv-docs", "__pycache__", ".mypy_cache",
    ".pytest_cache", "node_modules", ".opencode",
}
EXCLUDED_FILES = {".env", "wallet.key", "id_rsa", "id_ed25519"}
ROOT_LOCAL_DIRS = {".codex", ".claude", ".openforage", ".config"}
FROZEN_ANCHORS = {
    ".gitignore",
    ".github/scripts/smoke_first_contribution.py",
    "examples/fresh-agent-evals/v2/cases.json",
    "examples/fresh-agent-evals/v2/completion.schema.json",
    "examples/fresh-agent-evals/v2/diagnostic.schema.json",
    "examples/fresh-agent-evals/v2/oracle.json",
    "examples/fresh-agent-evals/v2/README.md",
    "examples/fresh-agent-evals/README.md",
    "examples/fresh-agent-evals/v2/schema.py",
    "examples/fresh-agent-evals/v2/paths.py",
    "examples/fresh-agent-evals/v2/manifest.py",
    "examples/fresh-agent-evals/v2/snapshot.py",
    "examples/fresh-agent-evals/v2/native_events.py",
    "examples/fresh-agent-evals/v2/commands.py",
    "examples/fresh-agent-evals/v2/shell_parser.py",
    "examples/fresh-agent-evals/v2/opencode_profile.py",
    "examples/fresh-agent-evals/v2/grader.py",
    "examples/fresh-agent-evals/v2/runner.py",
    "examples/fresh-agent-evals/v2/selfcheck.py",
    "examples/fresh-agent-evals/v2/comparison.py",
    "examples/fresh-agent-evals/v2/semantic.py",
    "examples/fresh-agent-evals/v2/freeze.py",
    "examples/fresh-agent-evals/v2/__init__.py",
    "examples/agent-campaign/campaign.json",
    "examples/fresh-agent-evals/tool_outcomes.py",
    "examples/fresh-agent-evals/trace_evidence.py",
    "examples/fresh-agent-evals/discovery_checks.py",
    "examples/opencode-lifecycle/bridge_core.py",
    ".github/scripts/validate_agent_docs.py",
    "CONTRIBUTING.md",
    "docs/agents/first-contribution.md",
    "docs/agents/diagnostics.md",
    "docs/agents/quickstart.md",
    "docs/agents/first-search.md",
    "docs/agents/install-authenticate.md",
    "docs/agents/research-opportunities.md",
    "docs/agents/start-here.md",
    "docs/agents/harness-verification.md",
    "docs/agents/references/onboarding.json",
    "docs/agents/references/compatibility.json",
    "docs/agents/research-opportunities.json",
    "docs/start-here/onboard-your-agent.md",
    "src/openforage/skills/agent_runtime_integration/references/harnesses.json",
    "scripts/onboarding/__init__.py",
    "scripts/onboarding/capabilities.py",
    "scripts/onboarding/entry_fragments.py",
    "scripts/onboarding/completion.py",
    "scripts/start_here.py",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
}


class ManifestError(RuntimeError):
    """The frozen case, rubric, helper, validator, or source closure drifted."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _safe_source_file(root: Path, relative: str) -> Path:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError(f"frozen source path traverses a symlink: {relative}")
    if not current.resolve().is_relative_to(root):
        raise ManifestError(f"frozen source path escapes the checkout: {relative}")
    return current


def _included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if relative.parts and relative.parts[0] in ROOT_LOCAL_DIRS:
        return False
    if path.name in EXCLUDED_FILES or path.suffix.casefold() in {".pem", ".p12", ".jks"}:
        return False
    if relative.parts[:1] == ("trial-output",):
        return False
    return (
        relative.as_posix() != HISTORICAL_SERIES1_REL
        and relative.parts[:4] != ("examples", "fresh-agent-evals", "v2", "series")
    )


def _fixture_normalizations(root: Path) -> dict[str, bytes]:
    """Hash an exact known seed as its clean source text, without writing files."""
    try:
        oracle_path = _safe_source_file(root, "examples/fresh-agent-evals/v2/oracle.json")
    except ManifestError:
        return {}
    if not oracle_path.is_file():
        return {}
    try:
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    normalizations = {}
    for case in oracle.get("cases", {}).values() if isinstance(oracle, dict) else ():
        fixture = case.get("fixture") if isinstance(case, dict) else None
        if not isinstance(fixture, dict):
            continue
        relative = Path(str(fixture.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            continue
        try:
            path = _safe_source_file(root, relative.as_posix())
        except ManifestError:
            continue
        find, replace = fixture.get("find"), fixture.get("replace")
        if not path.is_file() or path.is_symlink() or not isinstance(find, str) or not isinstance(replace, str):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if text.count(replace) == 1:
            normalizations[relative.as_posix()] = text.replace(replace, find, 1).encode("utf-8")
    return normalizations


def source_tree_sha256(root: Path) -> str:
    """Record a per-trial public input identity; this is not the rubric freeze."""
    root = root.resolve()
    digest = hashlib.sha256()
    normalized = _fixture_normalizations(root)
    files = []
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative_path.parts):
            continue
        if relative_path.parts and relative_path.parts[0] in ROOT_LOCAL_DIRS:
            continue
        if path.is_symlink():
            raise ManifestError("source snapshot contains a symlink")
        if path.is_file() and _included(path, root):
            files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        content = normalized.get(relative, path.read_bytes())
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def frozen_paths(root: Path) -> list[str]:
    """Enumerate the exact rubric closure; new validator/helpers join the freeze."""
    paths = set(FROZEN_ANCHORS)
    for directory in (
        root / "examples/fresh-agent-evals/v2",
        root / ".github/scripts/validation",
    ):
        if directory.is_dir():
            paths.update(
                item.relative_to(root).as_posix()
                for item in directory.rglob("*.py") if item.is_file()
            )
    # Capability evidence is canonical in harnesses.json. Freeze every
    # referenced artifact from that registry instead of maintaining a
    # wave-specific list of proof filenames.
    registry_path = _safe_source_file(
        root,
        "src/openforage/skills/agent_runtime_integration/references/harnesses.json",
    )
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("canonical harness registry is unreadable for evidence closure") from exc
    if not isinstance(registry, list):
        raise ManifestError("canonical harness registry must be an array for evidence closure")
    for entry in registry:
        if not isinstance(entry, dict):
            raise ManifestError("canonical harness registry contains a non-object entry")
        records = entry.get("capability_records", [])
        if not isinstance(records, list):
            raise ManifestError("canonical capability_records must be a list for evidence closure")
        for record in records:
            if not isinstance(record, dict):
                continue
            artifact = record.get("evidence_artifact")
            if artifact is None:
                continue
            if not isinstance(artifact, str):
                raise ManifestError("canonical evidence_artifact must be a repository-relative string")
            relative = Path(artifact)
            if (
                relative.is_absolute() or ".." in relative.parts or "\\" in artifact
                or relative.as_posix() != artifact
            ):
                raise ManifestError("canonical evidence_artifact is not a safe repository-relative path")
            artifact_path = _safe_source_file(root, artifact)
            if not artifact_path.is_file():
                raise ManifestError(f"canonical evidence artifact is missing: {artifact}")
            paths.add(artifact)
    paths.update(_public_gitignore_paths(root))
    return sorted(paths)


def _public_gitignore_paths(root: Path) -> set[str]:
    """Freeze ignore rules that can affect the canonical public-copy walk.

    Reuse the public copier's directory/credential exclusions and Git ignore
    evaluator. A rule is applicable when the copy admits the rule itself or a
    public file beneath its directory. Thus nested public rules join the freeze
    while rules wholly inside ignored runtime/cache trees do not.
    """
    root = Path(root).resolve()
    common_path = _safe_source_file(root, ".github/scripts/validation/common.py")
    if not common_path.is_file():
        raise ManifestError("public ignore-rule selection dependency is missing")
    spec = importlib.util.spec_from_file_location(
        "_openforage_validation_common_for_manifest", common_path,
    )
    if spec is None or spec.loader is None:
        raise ManifestError("public ignore-rule selection dependency cannot be loaded")
    common = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(common)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise ManifestError("public ignore-rule selection dependency is unreadable") from exc

    excluded_dirs = EXCLUDED_DIRS | set(common.TARGET_LOCAL_DIRS)
    candidates: set[str] = set()
    candidate_files: dict[str, Path] = {}
    candidate_directories: set[str] = set()
    rule_files: set[str] = set()

    def walk_error(_error: OSError) -> None:
        raise ManifestError("public ignore-rule inventory is unavailable")

    for current, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        directories[:] = [
            name for name in directories
            if name not in excluded_dirs
            and not (relative_dir == Path(".") and (
                name == "trial-output" or name in ROOT_LOCAL_DIRS
            ))
        ]
        for name in directories:
            relative = (relative_dir / name).as_posix()
            candidates.add(relative)
            candidate_directories.add(relative)
        for name in filenames:
            relative = (relative_dir / name).as_posix()
            candidates.add(relative)
            candidate_files[relative] = current_path / name
            if name == ".gitignore":
                rule_files.add(relative)

    try:
        ignored = common.git_ignored_paths(root, candidates)
    except Exception as exc:
        raise ManifestError("public ignore-rule applicability could not be evaluated") from exc

    for relative in candidate_directories:
        path = root / relative
        if path.is_symlink():
            if relative in ignored:
                continue
            raise ManifestError("public copy selection contains a non-ignored symlink directory")

    selected_files = set()
    for relative, path in candidate_files.items():
        if path.is_symlink():
            if relative in ignored:
                continue
            raise ManifestError("public copy selection contains a non-ignored symlink file")
        if relative in ignored and relative != ".gitignore":
            continue
        if path.name in EXCLUDED_FILES or common.is_credential_path(relative):
            continue
        if path.is_file():
            selected_files.add(relative)

    applicable = set()
    for relative in rule_files:
        directory = Path(relative).parent
        if relative in selected_files or any(
            path != relative and Path(path).is_relative_to(directory)
            for path in selected_files
        ):
            applicable.add(relative)
    return applicable


def build_manifest(root: Path, series_id: str) -> dict:
    """Build a named rubric-only freeze; trial input identity is recorded separately."""
    root = root.resolve()
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", series_id) is None:
        raise ManifestError("series id must be a short lowercase safe identifier")
    hashes = {}
    for relative in frozen_paths(root):
        path = _safe_source_file(root, relative)
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"frozen source is missing or a symlink: {relative}")
        hashes[relative] = file_sha256(path)
    return {
        "series_id": series_id,
        "series_version": 2,
        "manifest_schema_version": 2,
        "frozen_files": hashes,
        "freeze_note": "Named rubric closure only; each native trial records its own prepared input snapshot hash and file map.",
    }


def _manifest_file(root: Path, manifest_path: str) -> tuple[Path, str]:
    relative = Path(manifest_path)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != manifest_path:
        raise ManifestError("series manifest path must be a safe repository-relative path")
    path = _safe_source_file(root, manifest_path)
    if not path.is_file():
        raise ManifestError("selected series manifest is missing")
    return path, relative.as_posix()


def _verify_frozen_files(root: Path, manifest: dict, *, exact_inventory: bool = True) -> None:
    expected_paths = frozen_paths(root)
    expected = manifest.get("frozen_files")
    if not isinstance(expected, dict):
        raise ManifestError("selected series has no frozen file map")
    if exact_inventory and set(expected) != set(expected_paths):
        raise ManifestError("frozen file inventory drifted from the reviewed grading closure")
    if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in expected.values()):
        raise ManifestError("one or more frozen source digests are malformed")
    unknown_paths = set(expected) - set(expected_paths)
    if unknown_paths:
        raise ManifestError("manifest contains paths outside the admitted grading closure")
    drift = [name for name, digest in expected.items() if digest != file_sha256(_safe_source_file(root, name))]
    if drift:
        raise ManifestError(f"frozen rubric/validator files drifted: {', '.join(sorted(drift))}")


def verify_manifest(root: Path, manifest_path: str) -> tuple[dict, str]:
    """Verify one explicitly selected v2 manifest; no checkout-wide source freeze."""
    root = root.resolve()
    path, relative = _manifest_file(root, manifest_path)
    try:
        manifest = loads_strict(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("frozen series manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ManifestError("selected series manifest must be an object")
    if manifest.get("manifest_schema_version") == 1:
        raise ManifestError("historical series-1 is pre-native and cannot be selected for a new run")
    if type(manifest.get("manifest_schema_version")) is not int or manifest["manifest_schema_version"] != 2:
        raise ManifestError("selected series manifest schema version is unsupported")
    if type(manifest.get("series_version")) is not int or manifest["series_version"] != 2:
        raise ManifestError("selected series version is unsupported")
    series_id = manifest.get("series_id")
    if not isinstance(series_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", series_id) is None:
        raise ManifestError("selected series id is malformed")
    if Path(relative).parts[:-1] != Path(SERIES_OUTPUT_DIR).parts or Path(relative).name != f"{series_id}.json":
        raise ManifestError("selected manifest path must be the named file under the v2 series directory")
    required_keys = {
        "series_id", "series_version", "manifest_schema_version", "frozen_files", "freeze_note",
    }
    if set(manifest) != required_keys or not isinstance(manifest.get("freeze_note"), str):
        raise ManifestError("manifest fields or schema version do not match the frozen format")
    _verify_frozen_files(root, manifest)
    return manifest, file_sha256(path)


def verify_historical_series1(root: Path) -> dict:
    """Explicit archive audit for the byte-preserved, never-run C series-1 draft."""
    root = root.resolve()
    path, _relative = _manifest_file(root, HISTORICAL_SERIES1_REL)
    actual_manifest_sha = file_sha256(path)
    if actual_manifest_sha != HISTORICAL_SERIES1_SHA256:
        raise ManifestError("historical series-1 manifest bytes changed")
    try:
        manifest = loads_strict(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("historical series-1 manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("series_id") != "first-finished-contribution-v2-series-1"
        or type(manifest.get("manifest_schema_version")) is not int
        or manifest.get("manifest_schema_version") != 1
    ):
        raise ManifestError("historical series-1 draft identity changed")
    expected = manifest.get("frozen_files", {})
    drift = []
    if isinstance(expected, dict):
        for name, digest in expected.items():
            try:
                source = _safe_source_file(root, name) if isinstance(name, str) else None
            except ManifestError:
                source = None
            if not isinstance(digest, str) or source is None or not source.is_file() or file_sha256(source) != digest:
                drift.append(str(name))
    else:
        drift = ["frozen_files"]
    current_input = source_tree_sha256(root)
    return {
        "series_id": manifest["series_id"],
        "role": "historical-pre-native-draft",
        "native_trials": 0,
        "manifest_sha256": actual_manifest_sha,
        "frozen_file_drift": sorted(drift),
        "source_snapshot_matches": manifest.get("source_snapshot_sha256") == current_input,
        "source_snapshot_sha256_historical": manifest.get("source_snapshot_sha256"),
        "source_snapshot_sha256_current": current_input,
        "replayable": not drift and manifest.get("source_snapshot_sha256") == current_input,
    }
