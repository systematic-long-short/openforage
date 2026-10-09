"""Stable per-workspace claims shared by background-agent run directories."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from background_agent_store import (
    REPO_ROOT,
    StoreError,
    _is_git_ignored,
    load_state,
    process_group_observation,
    process_identity_status,
    valid_process_identity,
    validate_existing_run,
)


def _canonical_workspace(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise StoreError("workspace_guard_path_not_absolute")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise StoreError("workspace_guard_path_unresolvable") from None
    if resolved != path or not resolved.is_dir():
        raise StoreError("workspace_guard_path_not_canonical")
    return resolved


def _guard_root(root: Path | None = None) -> Path:
    selected = root or (Path.home() / ".local" / "state" / "openforage-background-agent")
    selected = Path(os.path.abspath(selected))
    if selected.is_relative_to(REPO_ROOT) and not _is_git_ignored(selected / "workspace-owner-probe.json"):
        raise StoreError("workspace_guard_inside_checkout_must_be_ignored")
    missing: list[Path] = []
    item = selected
    while not item.exists():
        missing.append(item)
        item = item.parent
    for component in reversed((item, *item.parents)):
        if component.is_symlink():
            raise StoreError("workspace_guard_symlink_refused")
    if not item.is_dir():
        raise StoreError("workspace_guard_parent_not_directory")
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise StoreError("workspace_guard_directory_create_failed") from None
        if directory.is_symlink() or not directory.is_dir():
            raise StoreError("workspace_guard_symlink_refused")
    try:
        os.chmod(selected, 0o700)
    except OSError:
        raise StoreError("workspace_guard_permissions_unavailable") from None
    return selected


class WorkspaceGuard:
    def __init__(self, root: Path, workspace: Path, lock_fd: int):
        self.root = root
        self.workspace = workspace
        self.workspace_sha256 = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()
        stem = f"workspace-{self.workspace_sha256}"
        self.metadata_path = root / f"{stem}.json"
        self.lock_path = root / f"{stem}.lock"
        self.lock_fd = lock_fd

    def read(self) -> dict | None:
        if self.metadata_path.is_symlink():
            raise StoreError("workspace_guard_metadata_symlink_refused")
        if not self.metadata_path.exists():
            return None
        try:
            record = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise StoreError("workspace_guard_metadata_invalid") from None
        if (not isinstance(record, dict) or record.get("schema_version") != 1
                or record.get("workspace_sha256") != self.workspace_sha256
                or record.get("workspace") != str(self.workspace)):
            raise StoreError("workspace_guard_metadata_identity_mismatch")
        if record.get("status") not in {
            "starting", "ready", "paused", "recovering", "needs_reconciliation", "failed", "stopped", "released",
        }:
            raise StoreError("workspace_guard_state_invalid")
        if record.get("status") == "released" and (
                record.get("release_proof") != "terminal_state_and_all_owned_groups_proven_gone"
                or type(record.get("released_at")) not in (int, float)
                or not math.isfinite(record.get("released_at"))
                or type(record.get("fencing_token")) is not int):
            raise StoreError("workspace_guard_release_record_invalid")
        return record

    def write(self, record: dict) -> None:
        if self.metadata_path.is_symlink():
            raise StoreError("workspace_guard_metadata_symlink_refused")
        fd, temporary = tempfile.mkstemp(prefix=".workspace-owner-", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, ensure_ascii=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.metadata_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def close(self) -> None:
        os.close(self.lock_fd)


@contextmanager
def locked_workspace(workspace: str | Path, *, root: Path | None = None):
    canonical = _canonical_workspace(Path(workspace))
    directory = _guard_root(root)
    digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
    lock_path = directory / f"workspace-{digest}.lock"
    if lock_path.is_symlink():
        raise StoreError("workspace_guard_lock_symlink_refused")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(lock_path, flags, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        if fd is not None:
            os.close(fd)
        raise StoreError("workspace_guard_lock_unavailable") from None
    guard = WorkspaceGuard(directory, canonical, fd)
    try:
        yield guard
    finally:
        guard.close()


def _state_dir_of(run) -> str:
    return str(Path(run.dir).resolve(strict=True))


def _run_record(run, state: dict, workspace: Path) -> dict:
    active = state.get("active")
    return {
        "schema_version": 1,
        "workspace": str(workspace),
        "workspace_sha256": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
        "run_id": run.run_id,
        "state_dir": _state_dir_of(run),
        "status": state.get("status"),
        "fencing_token": state.get("fencing_token"),
        "controller_identity": state.get("controller_identity"),
        "active_child_identity": active.get("child_identity") if isinstance(active, dict) else None,
        "updated_at": state.get("updated_at"),
    }


def _record_matches(record: dict, run, workspace: Path) -> bool:
    return (record.get("run_id") == run.run_id
            and record.get("state_dir") == _state_dir_of(run)
            and record.get("workspace") == str(workspace))


def _identity_and_group_gone(identity: object) -> bool:
    if not valid_process_identity(identity):
        return False
    return (process_identity_status(identity) == "dead"
            and process_group_observation(identity) == "gone")


def owned_processes_gone(state: dict) -> bool:
    """Require positive absence evidence for every recorded owned process group."""
    if state.get("active") is not None:
        return False
    generation = state.get("controller_generation", 0)
    controller = state.get("controller_identity")
    if type(generation) is not int or generation < 0:
        return False
    if generation:
        if not _identity_and_group_gone(controller):
            return False
    elif controller is not None or state.get("failure_code") not in {
        "controller_spawn_failed", "controller_log_create_failed", "controller_log_collision",
    }:
        return False
    active = state.get("active")
    if active is not None:
        if not isinstance(active, dict) or not _identity_and_group_gone(active.get("child_identity")):
            return False
    turns = state.get("turns")
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn, dict):
            return False
        identity = turn.get("child_process_identity")
        if turn.get("process_started") is True and identity is None:
            return False
        if identity is not None and not _identity_and_group_gone(identity):
            return False
    return True


def ensure_workspace_available(guard: WorkspaceGuard) -> None:
    """Refuse every prior owner except a stopped run with all groups proven gone."""
    record = guard.read()
    if record and record.get("status") != "released":
        prior_path = record.get("state_dir")
        try:
            prior_run = validate_existing_run(prior_path) if isinstance(prior_path, str) else None
            prior_state = load_state(prior_run) if prior_run is not None else None
        except StoreError:
            raise StoreError("workspace_owner_reconciliation_required") from None
        if (prior_state is None or prior_state.get("status") != "stopped"
                or not owned_processes_gone(prior_state)):
            raise StoreError("workspace_owned_by_existing_run")
        if (type(record.get("fencing_token")) is not int
                or record.get("fencing_token") != prior_state.get("fencing_token")
                or record.get("controller_identity") != prior_state.get("controller_identity")):
            raise StoreError("workspace_owner_metadata_state_mismatch")
        recorded_child = record.get("active_child_identity")
        if recorded_child is not None and not _identity_and_group_gone(recorded_child):
            raise StoreError("workspace_owner_child_identity_unconfirmed")
        record["status"] = "released"
        record["released_at"] = time.time()
        record["release_proof"] = "terminal_state_and_all_owned_groups_proven_gone"
        guard.write(record)


def claim_new_workspace(guard: WorkspaceGuard, run, state: dict) -> None:
    ensure_workspace_available(guard)
    guard.write(_run_record(run, state, guard.workspace))


def require_workspace_owner(guard: WorkspaceGuard, run, state: dict) -> dict:
    record = guard.read()
    if record is None or record.get("status") == "released" or not _record_matches(record, run, guard.workspace):
        raise StoreError("workspace_owner_record_missing_or_mismatched")
    saved_fence = record.get("fencing_token")
    current_fence = state.get("fencing_token")
    if (type(saved_fence) is not int or type(current_fence) is not int
            or saved_fence != current_fence):
        raise StoreError("workspace_owner_fence_mismatch")
    if record.get("controller_identity") != state.get("controller_identity"):
        raise StoreError("workspace_owner_identity_mismatch")
    return record


def update_workspace_owner(guard: WorkspaceGuard, run, state: dict) -> None:
    record = guard.read()
    if record is None or record.get("status") == "released" or not _record_matches(record, run, guard.workspace):
        raise StoreError("workspace_owner_record_missing_or_mismatched")
    saved_fence, current_fence = record.get("fencing_token"), state.get("fencing_token")
    if (type(saved_fence) is not int or type(current_fence) is not int
            or current_fence < saved_fence):
        raise StoreError("workspace_owner_fence_regression")
    guard.write(_run_record(run, state, guard.workspace))


def adopt_workspace_for_recovery(guard: WorkspaceGuard, run, state: dict) -> None:
    """Transfer only the exact original run after caller reconciliation checks."""
    record = guard.read()
    if record is None:
        raise StoreError("workspace_owner_record_missing_recovery_refused")
    if record.get("status") == "released" or not _record_matches(record, run, guard.workspace):
        raise StoreError("workspace_owned_by_different_run")
    saved_fence, current_fence = record.get("fencing_token"), state.get("fencing_token")
    if (type(saved_fence) is not int or type(current_fence) is not int
            or current_fence < saved_fence):
        raise StoreError("workspace_owner_fence_regression")
    guard.write(_run_record(run, state, guard.workspace))


def release_stopped_workspace(guard: WorkspaceGuard, run, state: dict) -> bool:
    if state.get("status") != "stopped" or not owned_processes_gone(state):
        return False
    record = guard.read()
    if record is None or not _record_matches(record, run, guard.workspace):
        return False
    record.update({
        "status": "released",
        "released_at": time.time(),
        "release_proof": "terminal_state_and_all_owned_groups_proven_gone",
        "fencing_token": state.get("fencing_token"),
    })
    guard.write(record)
    return True
