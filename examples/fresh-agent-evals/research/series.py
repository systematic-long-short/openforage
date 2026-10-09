"""Immutable named-series closure for the paper-backed research fixture."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

EVALS_ROOT_REL = Path("examples/fresh-agent-evals")
RESEARCH_REL = EVALS_ROOT_REL / "research"
SERIES_DIR_REL = RESEARCH_REL / "series"
SERIES_SCHEMA_VERSION = 1
SERIES_VERSION = 1
SERIES_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
EXCLUDED_PARTS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules", ".tmp", ".venv", ".venv-docs"}
HERE = Path(__file__).resolve().parent
V2_DIR = HERE.parent / "v2"
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

from manifest import frozen_paths as v2_frozen_paths  # noqa: E402

HELPER_CLOSURE = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "docs/agents/first-contribution.md",
    "docs/agents/research-campaign.md",
    "docs/agents/research-opportunities.json",
    "docs/agents/background-agent.md",
    "docs/agents/harness-verification.md",
    "docs/agents/references/compatibility.json",
    "docs/agents/research-security.md",
    "examples/agent-campaign/campaign.json",
    "examples/agent-campaign/challenger_design.md",
    "examples/agent-campaign/coverage_challenger.py",
    "examples/agent-campaign/stratified_policy.py",
    "examples/fresh-agent-evals/README.md",
    "examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/primary-source-excerpt.md",
    "examples/fresh-agent-evals/tool_outcomes.py",
    "examples/fresh-agent-evals/trace_evidence.py",
    "examples/fresh-agent-evals/v2/commands.py",
    "examples/fresh-agent-evals/v2/evidence.py",
    "examples/fresh-agent-evals/v2/grader.py",
    "examples/fresh-agent-evals/v2/manifest.py",
    "examples/fresh-agent-evals/v2/native_events.py",
    "examples/fresh-agent-evals/v2/opencode_profile.py",
    "examples/fresh-agent-evals/v2/paths.py",
    "examples/fresh-agent-evals/v2/processes.py",
    "examples/fresh-agent-evals/v2/runner.py",
    "examples/fresh-agent-evals/v2/README.md",
    "examples/fresh-agent-evals/v2/schema.py",
    "examples/fresh-agent-evals/v2/semantic.py",
    "examples/fresh-agent-evals/v2/shell_parser.py",
    "examples/fresh-agent-evals/v2/snapshot.py",
    "examples/opencode-lifecycle/bridge_core.py",
    "src/openforage/skills/agent_runtime_integration/references/claude-code.md",
    "src/openforage/skills/agent_runtime_integration/references/unsupported.md",
    "src/openforage/skills/search_research/SKILL.md",
    "examples/background-agent/README.md",
    "examples/background-agent/adapter-interface.md",
    "examples/background-agent/conformance.py",
    "examples/background-agent/conformance.schema.json",
    "examples/background-agent/proof.py",
    "examples/background-agent/proof.schema.json",
    "examples/background-agent/fixtures/README.md",
    "examples/background-agent/fixtures/claude-code-stream.synthetic.jsonl",
    "examples/background-agent/fixtures/conformance-pass.synthetic.json",
    "examples/background-agent/fixtures/proof-v2-envelope.synthetic.json",
    "scripts/background_agent.py",
    "scripts/background_agent_adapters.py",
    "scripts/background_agent_ownership.py",
    "scripts/background_agent_service.py",
    "scripts/background_agent_selfcheck.py",
    "scripts/background_agent_store.py",
    "scripts/background_adapters/__init__.py",
    "scripts/background_adapters/claude_code.py",
    "scripts/background_adapters/cli.py",
    "scripts/background_adapters/codex.py",
    "scripts/background_adapters/conformance.py",
    "scripts/background_adapters/contract.py",
    "scripts/background_adapters/native_evidence.py",
    "scripts/background_adapters/opencode.py",
    "scripts/background_adapters/scaffold.py",
    "scripts/background_adapters/selfcheck.py",
    ".github/scripts/validate_agent_docs.py",
}


class SeriesError(RuntimeError):
    """A named research series is missing, unsafe, or has drifted."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        not relative or path.is_absolute() or "\\" in relative
        or ".." in path.parts or path.as_posix() != relative
    ):
        raise SeriesError("series path is not a safe repository-relative path")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise SeriesError("series source path traverses a symlink")
    if not target.resolve().is_relative_to(root.resolve()):
        raise SeriesError("series source path escapes its root")
    return target


def frozen_paths(root: Path) -> list[str]:
    """Return the exact public rubric, evaluator, fixture, and helper closure."""
    root = root.resolve()
    research_dir = _safe_path(root, RESEARCH_REL.as_posix())
    if not research_dir.is_dir():
        raise SeriesError("research evaluation directory is missing")
    # The imported v2 runner transitively depends on the full frozen v2 source
    # closure, including onboarding/validator modules and their route inputs.
    paths = set(HELPER_CLOSURE) | set(v2_frozen_paths(root))
    for path in research_dir.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if relative.parts[: len(SERIES_DIR_REL.parts)] == SERIES_DIR_REL.parts:
            continue
        if path.is_symlink():
            raise SeriesError("research evaluation source closure contains a symlink")
        if path.is_file() and path.suffix.casefold() in {".py", ".json", ".md"}:
            paths.add(relative.as_posix())
    validation_dir = root / ".github/scripts/validation"
    if validation_dir.is_dir():
        for path in validation_dir.rglob("*.py"):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise SeriesError("validator helper closure contains a symlink")
            paths.add(relative.as_posix())
    return sorted(paths)


def build_series(root: Path, series_id: str) -> dict:
    root = root.resolve()
    if SERIES_ID_RE.fullmatch(series_id) is None:
        raise SeriesError("series id must be a short lowercase safe identifier")
    hashes = {}
    for relative in frozen_paths(root):
        path = _safe_path(root, relative)
        if not path.is_file():
            raise SeriesError(f"frozen source is missing: {relative}")
        hashes[relative] = _file_sha256(path)
    return {
        "series_id": series_id,
        "series_version": SERIES_VERSION,
        "manifest_schema_version": SERIES_SCHEMA_VERSION,
        "frozen_files": hashes,
        "freeze_note": "Named rubric/evaluator/helper closure only; each native trial records its own prepared public-source snapshot hash.",
    }


def _series_file(root: Path, manifest_path: str) -> Path:
    path = _safe_path(root, manifest_path)
    relative = Path(manifest_path)
    if relative.parts[:-1] != SERIES_DIR_REL.parts or relative.suffix != ".json":
        raise SeriesError("series manifest must be a named JSON file under the research series directory")
    if not path.is_file():
        raise SeriesError("selected series manifest is missing")
    return path


def verify_series(root: Path, manifest_path: str) -> tuple[dict, str]:
    """Verify one explicitly selected series and every frozen source digest."""
    root = root.resolve()
    manifest_file = _series_file(root, manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SeriesError("selected series manifest is unreadable or has duplicate keys") from exc
    if not isinstance(manifest, dict):
        raise SeriesError("selected series manifest root must be an object")
    if set(manifest) != {"series_id", "series_version", "manifest_schema_version", "frozen_files", "freeze_note"}:
        raise SeriesError("selected series manifest fields do not match the supported schema")
    series_id = manifest.get("series_id")
    if not isinstance(series_id, str) or SERIES_ID_RE.fullmatch(series_id) is None or Path(manifest_path).name != f"{series_id}.json":
        raise SeriesError("series id and selected manifest path do not match")
    if type(manifest.get("series_version")) is not int or manifest["series_version"] != SERIES_VERSION:
        raise SeriesError("selected series version is unsupported")
    if type(manifest.get("manifest_schema_version")) is not int or manifest["manifest_schema_version"] != SERIES_SCHEMA_VERSION:
        raise SeriesError("selected manifest schema version is unsupported")
    if not isinstance(manifest.get("freeze_note"), str):
        raise SeriesError("selected series freeze note must be a string")
    expected_paths = frozen_paths(root)
    frozen = manifest.get("frozen_files")
    if not isinstance(frozen, dict) or set(frozen) != set(expected_paths):
        raise SeriesError("frozen source inventory has drifted")
    for relative, digest in frozen.items():
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise SeriesError("frozen source digest is malformed")
        path = _safe_path(root, relative)
        if not path.is_file() or _file_sha256(path) != digest:
            raise SeriesError(f"frozen source digest drifted: {relative}")
    return manifest, _file_sha256(manifest_file)


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def write_series_once(root: Path, output_relative: str, payload: dict) -> Path:
    """Create a series file without following symlinks or replacing evidence."""
    root = root.resolve()
    output = _safe_path(root, output_relative)
    if Path(output_relative).parts[:-1] != SERIES_DIR_REL.parts:
        raise SeriesError("new series files must be direct children of the research series directory")
    if output.name != f"{payload.get('series_id')}.json":
        raise SeriesError("series output filename must match its series id")
    current = root
    for part in Path(output_relative).parts[:-1]:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise SeriesError("series output directory contains an unsafe path component")
        else:
            current.mkdir()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, 0o644)
    except FileExistsError as exc:
        raise SeriesError("refusing to overwrite an existing series manifest") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return output
