"""Exclusive path handling for v2 trial workspaces and evidence directories."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


class PathSafetyError(RuntimeError):
    """A requested path is not a safe, exclusive v2 trial destination."""


def safe_relative(value: str) -> Path:
    path = Path(value)
    if (
        not value or path.is_absolute() or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise PathSafetyError(f"unsafe workspace-relative path: {value!r}")
    return path


def _check_components(path: Path, stop: Path | None = None) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise PathSafetyError(f"symlink path component refused: {current.name}")
        if stop is not None and current == stop:
            return


def mkdir_chain_no_symlinks(path: Path) -> None:
    """Create missing parents one at a time and reject every symlink component."""
    absolute = Path(os.path.abspath(path))
    chain = list(reversed([absolute, *absolute.parents]))
    current = Path(absolute.anchor)
    for component in chain[1:]:
        current = current / component.name
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise PathSafetyError(f"directory component is not a real directory: {current.name}")
            continue
        current.mkdir()


def make_exclusive_directory(path: Path, allowed_parent: Path) -> Path:
    """Create one new directory beneath a trusted parent; never replace evidence."""
    parent = Path(os.path.abspath(allowed_parent))
    target = Path(os.path.abspath(path))
    if target.parent != parent or target == parent:
        raise PathSafetyError("trial directory must be a direct child of its allowed parent")
    mkdir_chain_no_symlinks(parent)
    if target.exists() or target.is_symlink():
        raise PathSafetyError(f"refusing existing trial directory: {target.name}")
    target.mkdir()
    return target


def ensure_clean_ancestors(workspace: Path) -> None:
    """No instruction file outside the workspace may influence a native run."""
    workspace = Path(os.path.abspath(workspace))
    for ancestor in workspace.parents:
        if (ancestor / "AGENTS.md").is_file() or (ancestor / "CLAUDE.md").is_file():
            raise PathSafetyError("an ancestor instruction file exists outside the isolated workspace")


def exclusive_json(path: Path, payload: dict) -> None:
    """Write a sanitized record once, with no symlink following or replacement."""
    if path.is_symlink() or path.exists():
        raise PathSafetyError(f"refusing to overwrite evidence: {path.name}")
    if not path.parent.is_dir():
        raise PathSafetyError("evidence parent directory is not prepared")
    _check_components(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def raw_file_exclusive(path: Path, data: bytes) -> None:
    """Preserve private raw bytes only at a new, non-symlink path."""
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise PathSafetyError(f"raw trace path is unavailable or already exists: {path.name}")
    _check_components(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
