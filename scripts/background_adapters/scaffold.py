"""Caller-requested adapter scaffold writer with exclusive, inert outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path


class ScaffoldError(RuntimeError):
    """A scaffold request is unsafe, ambiguous, or collides with existing files."""


SCAFFOLD_FILES: dict[str, str] = {
    "adapter.py": '''"""Fail-loud adapter skeleton. Replace only from observed primary evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class CandidateAdapter:
    # Replace with the exact vendor/product identity after primary-source review.
    adapter_id = None
    product_identity = None
    executable_name = None
    declared_primitives = ()

    def validate_selector(self, model: str, selectors: dict[str, str | None]) -> None:
        raise NotImplementedError("selector rules require official/local evidence")

    def version_probe_argv(self, binary: str) -> list[str]:
        raise NotImplementedError("do not guess a version command")

    def help_probe_argv(self, binary: str) -> list[str]:
        raise NotImplementedError("do not guess a help command")

    def parse_version(self, *, returncode: int, stdout: str, stderr: str) -> str:
        raise NotImplementedError("record an observed product version")

    def build_argv(self, *, binary: str, workspace: Path, prompt: str,
                   exact_session_id: str | None, fresh_session_id: str | None,
                   profile: str) -> list[str]:
        raise NotImplementedError("require an evidence-backed argv contract")

    def normalize_trace(self, raw_text: str) -> dict[str, Any]:
        raise NotImplementedError("do not infer success from stdout prose")

    def extract_session_identity(self, events: list[dict[str, Any]]) -> tuple[str | None, bool]:
        raise NotImplementedError("require a documented event identity field")
''',
    "evidence.json": json.dumps({
        "schema_version": 1,
        "evidence_class": "adapter-discovery-scaffold",
        "status": "unverified",
        "product_identity": None,
        "adapter_identity": None,
        "primary_sources": [],
        "version": {"value": None, "source": None, "observed": False},
        "probe": {"id": None, "os": None, "profile": None, "date": None},
        "operations": {
            "goal_declaration": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "task_creation": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "task_durable_reread": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "memory_write": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "memory_durable_reread": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "native_scheduler_creation": {"availability": "unknown", "outcome": "unknown", "evidence": []},
            "native_scheduler_delivery": {"availability": "unknown", "outcome": "unknown", "evidence": []},
        },
        "synthetic_fixture_only": True,
        "session_launched": False,
        "credentials_inspected": False,
        "configuration_mutated": False,
    }, indent=2, sort_keys=True) + "\n",
    "fixtures/inert-events.json": json.dumps({
        "synthetic": True,
        "claim": "not-an-observation",
        "events": [],
    }, indent=2, sort_keys=True) + "\n",
    "contract-checklist.md": """# Adapter contract checklist

- [ ] Exact product identity is supported by an official primary source.
- [ ] Version and help argv are observed locally; no session or model is launched.
- [ ] Each argv token is explicit; no shell, interpolation, most-recent, or implicit resume path.
- [ ] Fresh session identity and exact recorded-session continuation are separately tested.
- [ ] Event/session fields come from the official schema; stdout prose is not evidence.
- [ ] Native goal, task create, durable reread, memory, scheduler creation, and real delivery have separate typed outcomes.
- [ ] Tool/event identity, request/ack causality, version, OS, profile, and scope are retained.
- [ ] External due delivery is never labeled a native scheduler event.
- [ ] Lifecycle edges are checked separately: parent independence, ownership acknowledgement, pause/drain/reconcile, crash recovery, terminal stop, duplicate owner, cleanup.
- [ ] Synthetic fixtures remain labeled synthetic and do not enter the canonical capability registry.
- [ ] Permission defaults and authentication are inherited; no bypass, auth mutation, install, or credential inspection.
- [ ] Version changes require a fresh proof applicability review.
""",
}


def _no_symlink_components(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ScaffoldError("scaffold_path_symlink_refused")


def _write_exclusive(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        raise ScaffoldError("scaffold_file_collision") from None
    except OSError:
        raise ScaffoldError("scaffold_file_write_failed") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def create_scaffold(directory: str | Path) -> dict:
    """Create an inert scaffold only in a caller-selected, new directory."""
    target = Path(os.path.abspath(os.path.expanduser(os.fspath(directory))))
    _no_symlink_components(target)
    if target.exists() or target.is_symlink():
        raise ScaffoldError("scaffold_directory_must_be_new")
    if not target.parent.is_dir():
        raise ScaffoldError("scaffold_parent_must_exist")
    try:
        target.mkdir(mode=0o755, parents=False, exist_ok=False)
    except OSError:
        raise ScaffoldError("scaffold_directory_create_failed") from None
    try:
        for relative, text in SCAFFOLD_FILES.items():
            output = target / relative
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            if output.parent.is_symlink():
                raise ScaffoldError("scaffold_path_symlink_refused")
            _write_exclusive(output, text)
    except Exception:
        # Do not delete a partially written caller-selected scaffold; the safe
        # failure remains visible and no existing file is ever overwritten.
        raise
    return {
        "created": True,
        "file_count": len(SCAFFOLD_FILES),
        "synthetic_fixture_only": True,
        "session_launched": False,
        "credentials_inspected": False,
        "configuration_mutated": False,
    }
