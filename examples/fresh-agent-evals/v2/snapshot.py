"""Build complete instruction-visible source snapshots with one seeded defect."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
if str(REPO_ROOT / ".github/scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / ".github/scripts"))

from onboarding.completion import SCRATCH_REL  # noqa: E402
from validation.common import (  # noqa: E402
    GitIgnoreError,
    TARGET_LOCAL_DIRS,
    git_ignored_paths,
    is_credential_path,
)

from manifest import EXCLUDED_DIRS, EXCLUDED_FILES, ROOT_LOCAL_DIRS, source_tree_sha256
from paths import PathSafetyError, ensure_clean_ancestors, make_exclusive_directory, safe_relative
from processes import run_bounded
from schema import read_json


class SnapshotError(RuntimeError):
    """A fixture could not be prepared as a bounded, isolated source checkout."""


def file_map(root: Path, *, exclude_paths: tuple[str, ...] = ()) -> dict[str, str]:
    result = {}
    excluded = {SCRATCH_REL, *exclude_paths}
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        if path.is_symlink():
            raise SnapshotError("workspace file inventory contains a symlink")
        if ".git" in relative_parts or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def digest_map(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, value in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def copy_public_tree(source: Path, target: Path) -> dict[str, str]:
    """Copy the current public working tree, honoring its real ignore rules.

    Candidate files are discovered from the filesystem (including uncommitted
    and untracked public files), never from a Git file listing. One isolated
    ignore query filters the inventory; the returned hashes describe exactly
    the public source files copied.
    """
    source = Path(source).resolve(strict=True)
    target = Path(target)
    target_lexical = Path(os.path.abspath(target))
    target_resolved = target.resolve()
    if (
        not source.is_dir()
        or target_lexical == source
        or target_lexical.is_relative_to(source)
        or target_resolved == source
        or target_resolved.is_relative_to(source)
    ):
        raise SnapshotError("public copy target must be outside the source tree")

    records: list[tuple[Path, Path, tuple[str, ...], tuple[str, ...]]] = []
    ignore_candidates: set[str] = set()

    def on_walk_error(_error: OSError) -> None:
        raise SnapshotError("public source tree inventory is unavailable")

    for current, directories, filenames in os.walk(source, followlinks=False, onerror=on_walk_error):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        kept_directories = []
        for name in directories:
            if name in EXCLUDED_DIRS or name in TARGET_LOCAL_DIRS:
                continue
            if relative_dir == Path(".") and (
                name == "trial-output" or name in ROOT_LOCAL_DIRS
            ):
                continue
            kept_directories.append(name)
            ignore_candidates.add((relative_dir / name).as_posix())
        directories[:] = kept_directories
        kept_files = tuple(filenames)
        for name in kept_files:
            ignore_candidates.add((relative_dir / name).as_posix())
        records.append((current_path, relative_dir, tuple(kept_directories), kept_files))

    try:
        ignored = git_ignored_paths(source, ignore_candidates)
    except GitIgnoreError as exc:
        raise SnapshotError(str(exc)) from exc

    target.mkdir(parents=True, exist_ok=True)
    source_hashes: dict[str, str] = {}
    for current_path, relative_dir, directories, filenames in records:
        for name in directories:
            original = current_path / name
            relative = (relative_dir / name).as_posix()
            if original.is_symlink():
                if relative in ignored:
                    continue
                raise SnapshotError("source tree contains a non-ignored symlink directory")
        for name in filenames:
            original = current_path / name
            relative = (relative_dir / name).as_posix()
            if original.is_symlink():
                if relative in ignored:
                    continue
                raise SnapshotError("source tree contains a non-ignored symlink file")
            if relative in ignored and relative != ".gitignore":
                continue
            if (
                name in EXCLUDED_FILES
                or is_credential_path(relative)
            ):
                continue
            if not original.is_file():
                continue
            destination = target / relative_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = original.read_bytes()
            destination.write_bytes(data)
            shutil.copystat(original, destination, follow_symlinks=False)
            source_hashes[relative] = hashlib.sha256(data).hexdigest()
    return source_hashes


def initialize_public_workspace(
    source: Path,
    workspace: Path,
    selected_files: dict[str, str],
    runner_python_env: dict,
) -> dict:
    """Initialize an isolated Git workspace using only the copied public selection.

    Files already admitted by ``copy_public_tree`` can match .gitignore while
    still being legitimate tracked public content. Stage that exact selection
    literally and with ``--force``; never rebuild it with ``git add --all``.
    """
    source = Path(source).resolve(strict=True)
    supplied_workspace = Path(workspace)
    if supplied_workspace.is_symlink():
        raise SnapshotError("isolated public workspace may not be a symlink")
    try:
        workspace = supplied_workspace.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("isolated public workspace is unavailable") from exc
    if (
        not workspace.is_dir()
        or workspace == source
        or workspace.is_relative_to(source)
        or source.is_relative_to(workspace)
    ):
        raise SnapshotError("isolated public workspace must be separate from its source")

    git = shutil.which("git")
    if not git:
        raise SnapshotError("Git is required to initialize the isolated public workspace")

    paths = sorted(selected_files)
    if not paths:
        raise SnapshotError("public copy selection is empty")
    for relative in paths:
        pure = PurePosixPath(relative)
        if (
            not relative
            or "\x00" in relative
            or "\\" in relative
            or pure.is_absolute()
            or pure.as_posix() != relative
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise SnapshotError("public copy selection contains an unsafe path")
        path = workspace.joinpath(*pure.parts)
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve(strict=True).is_relative_to(workspace)
        ):
            raise SnapshotError("public copy selection no longer matches the isolated workspace")

    env = {key: value for key, value in runner_python_env.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    payload = b"\0".join(os.fsencode(path) for path in paths) + b"\0"
    processes = []

    def run_step(label: str, command: str, argv: list[str], *, input_data: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            process = subprocess.run(
                argv,
                cwd=workspace,
                env=env,
                input=input_data,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            processes.append({
                "label": label,
                "command": command,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "launch_error": type(exc).__name__,
            })
            raise SnapshotError(f"isolated public workspace Git {label} failed") from exc
        stdout = process.stdout.decode("utf-8", "replace")
        stderr = process.stderr.decode("utf-8", "replace")
        processes.append({
            "label": label,
            "command": command,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        })
        if process.returncode != 0:
            raise SnapshotError(f"isolated public workspace Git {label} failed")
        return process

    run_step("init", "git init --quiet (isolated public workspace)", [git, "init", "--quiet"])
    run_step(
        "selected-index",
        "git --literal-pathspecs add --force --pathspec-from-file=- --pathspec-file-nul",
        [
            git,
            "--literal-pathspecs",
            "add",
            "--force",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
        ],
        input_data=payload,
    )
    probe = run_step(
        "root-probe",
        "git rev-parse --show-prefix (empty at isolated workspace root)",
        [git, "rev-parse", "--show-prefix"],
    )
    if probe.stdout.strip():
        raise SnapshotError("isolated Git root does not match the public workspace")

    selection_digest = hashlib.sha256()
    for relative in paths:
        selection_digest.update(os.fsencode(relative))
        selection_digest.update(b"\0")
        selection_digest.update(bytes.fromhex(selected_files[relative]))
    return {
        "workspace_git_root_verified": True,
        "selected_file_count": len(paths),
        "selected_paths_sha256": selection_digest.hexdigest(),
        "processes": processes,
    }


def prepare_snapshot(repo: Path, case_id: str, workspace: Path, child_env: dict) -> dict:
    repo = repo.resolve()
    workspace = Path(os.path.abspath(workspace))
    if workspace == repo or workspace.is_relative_to(repo):
        raise PathSafetyError("workspace must be outside the public source checkout")
    make_exclusive_directory(workspace, workspace.parent)
    ensure_clean_ancestors(workspace)
    try:
        selected_files = copy_public_tree(repo, workspace)
    except OSError as exc:
        raise SnapshotError("public source snapshot copy failed") from exc
    required = ("README.md", "AGENTS.md", "CLAUDE.md")
    if any(not (workspace / name).is_file() for name in required):
        raise SnapshotError("the root repository route files must remain visible in the workspace")
    if any((workspace / name).is_symlink() for name in required):
        raise SnapshotError("root repository route files may not be symlinks")

    oracle = read_json(repo / "examples/fresh-agent-evals/v2/oracle.json")
    entry = oracle.get("cases", {}).get(case_id)
    if not isinstance(entry, dict):
        raise SnapshotError("selected case has no frozen fixture definition")
    fixture = entry.get("fixture", {})
    relative = safe_relative(fixture.get("path", ""))
    target = workspace / relative
    if not target.is_file() or target.is_symlink():
        raise SnapshotError("fixture target is missing or unsafe")
    original = target.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    needle = fixture.get("find")
    replacement = fixture.get("replace")
    if not isinstance(needle, str) or not isinstance(replacement, str) or text.count(needle) != 1:
        raise SnapshotError("fixture must match exactly one source fragment")
    target.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    (workspace / "trial-output").mkdir()
    git_setup = initialize_public_workspace(repo, workspace, selected_files, child_env)
    files = file_map(workspace)
    return {
        "case_id": case_id,
        "fixture_path": relative.as_posix(),
        "clean_fixture_source_sha256": original_hash,
        "seeded_fixture_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "source_tree_sha256": source_tree_sha256(repo),
        "snapshot_sha256": digest_map(files),
        "snapshot_file_count": len(files),
        "workspace_git_root_verified": git_setup["workspace_git_root_verified"],
        "workspace_public_selection_sha256": git_setup["selected_paths_sha256"],
        "root_route_files_visible": list(required),
        "ancestor_instructions_outside_workspace": False,
        "public_suite_is_instruction_isolated": True,
        "initial_files": files,
    }
