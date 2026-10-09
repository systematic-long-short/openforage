"""Shared helpers for the packet validator and its focused check modules."""

from __future__ import annotations

import datetime as _dt
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    ".tmp",
    ".venv",
    ".venv-docs",
    ".opencode",
    ".codex",
    ".claude",
    ".openforage",
    ".config",
    ".openforage-state",
    ".cache",
    ".secrets",
    ".ssh",
    "credentials",
}
TARGET_LOCAL_DIRS = SKIP_DIRS
TARGET_CREDENTIAL_NAMES = {"wallet.key", "id_rsa", "id_ed25519", "credentials", "credentials.json"}
TARGET_CREDENTIAL_SUFFIXES = {".pem", ".p12", ".jks"}
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".yml", ".yaml", ".py", ".toml", ".txt", ".cfg"}

CAMPAIGN_DIR = "examples/agent-campaign"
STATE_MACHINE_PATH = "docs/agents/references/campaign-state-machine.json"

PLACEHOLDER_RE = re.compile(r"^<.*>$", re.S)
EMBEDDED_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][^<>\n]*>")
ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PASSING_RESULT_RE = re.compile(r"^\s*(?:passed|pass)\b", re.IGNORECASE)
PASSING_OUTCOMES = {"passed", "pass"}
FAILURE_RESULT_RE = re.compile(
    r"^\s*(?:fail|failed|failure|error|blocked|not observed|unverified)\b",
    re.IGNORECASE,
)


class Report:
    """Collects human-readable validation notes and failures."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors

    def dump(self) -> int:
        for note in self.notes:
            print(f"note: {note}")
        for error in self.errors:
            print(f"FAIL: {error}")
        if self.errors:
            print(f"\n{len(self.errors)} failure(s)")
            return 1
        print("ok")
        return 0


def iter_files(root: Path, suffixes: set[str] | None = None):
    root = Path(root)
    for relative in repo_path_inventory(root):
        path = root / relative
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        yield path


def repo_path_inventory(root: Path) -> tuple[str, ...]:
    """List non-local repository entries once, without following symlinks."""
    root = Path(root)
    paths: list[str] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        directories[:] = [name for name in directories if name not in SKIP_DIRS]
        for name in directories:
            paths.append((relative_dir / name).as_posix())
        for name in filenames:
            paths.append((relative_dir / name).as_posix())
    return tuple(sorted(paths))


class GitIgnoreError(RuntimeError):
    """Git could not safely evaluate this work tree's ignore rules."""


def _own_git_index_files(root: Path) -> tuple[Path, ...]:
    """Return only this root's index files; never discover an ancestor repo."""
    marker = root / ".git"
    if marker.is_symlink():
        raise GitIgnoreError("repository ignore metadata is unsafe")
    if not marker.exists():
        return ()
    if marker.is_dir():
        git_dir = marker
    elif marker.is_file():
        try:
            text = marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise GitIgnoreError("repository ignore metadata is unreadable") from exc
        if not text.startswith("gitdir: ") or not text[8:].strip():
            raise GitIgnoreError("repository ignore metadata is malformed")
        git_dir = Path(text[8:].strip())
        if not git_dir.is_absolute():
            git_dir = root / git_dir
    else:
        raise GitIgnoreError("repository ignore metadata is malformed")
    try:
        git_dir = git_dir.resolve(strict=True)
    except OSError as exc:
        raise GitIgnoreError("repository ignore metadata is unavailable") from exc
    if not git_dir.is_dir():
        raise GitIgnoreError("repository ignore metadata is unavailable")

    index = git_dir / "index"
    if index.is_symlink():
        raise GitIgnoreError("repository ignore index is unsafe")
    files = [index] if index.is_file() else []
    try:
        shared = sorted(git_dir.glob("sharedindex.*"))
    except OSError as exc:
        raise GitIgnoreError("repository ignore index is unavailable") from exc
    for path in shared:
        if path.is_symlink():
            raise GitIgnoreError("repository ignore index is unsafe")
        if path.is_file():
            files.append(path)
    return tuple(files)


def git_ignored_paths(root: Path, relative_paths) -> set[str]:
    """Evaluate many paths against real .gitignore rules in one isolated Git call.

    The disposable Git directory contains only a copy of this work tree's index
    (so tracked ignored-name files remain public). Its config, info/exclude,
    global/system config, and repository discovery are isolated. With no local
    .git metadata, an empty index is used and ancestor repositories are ignored.
    """
    root = Path(root).resolve()
    paths = set()
    for item in relative_paths:
        value = item.as_posix() if isinstance(item, Path) else str(item)
        if not value or "\x00" in value or value.startswith("/") or "\\" in value:
            raise GitIgnoreError("ignore inventory contains an unsafe repository-relative path")
        normalized = posixpath.normpath(value)
        if normalized in {"", "."}:
            continue
        if normalized == ".." or normalized.startswith("../"):
            raise GitIgnoreError("ignore inventory contains an unsafe repository-relative path")
        paths.add(normalized)
    if not paths:
        return set()

    git = shutil.which("git")
    if git is None:
        raise GitIgnoreError("Git is required to evaluate repository ignore rules")

    try:
        index_files = _own_git_index_files(root)
        with tempfile.TemporaryDirectory(prefix="of-ignore-index-") as temporary:
            git_dir = Path(temporary) / "metadata"
            template_dir = Path(temporary) / "empty-template"
            template_dir.mkdir()
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update({
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TEMPLATE_DIR": str(template_dir),
            })
            initialized = subprocess.run(
                [git, "init", "--quiet", "--bare", f"--template={template_dir}", str(git_dir)],
                cwd=root,
                env=env,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if initialized.returncode != 0:
                raise GitIgnoreError("isolated Git ignore metadata could not be initialized")
            for source in index_files:
                shutil.copyfile(source, git_dir / source.name)

            payload = b"\0".join(path.encode("utf-8") for path in sorted(paths)) + b"\0"
            process = subprocess.run(
                [
                    git,
                    f"--git-dir={git_dir}",
                    f"--work-tree={root}",
                    "check-ignore",
                    "-z",
                    "-v",
                    "--stdin",
                ],
                cwd=root,
                env=env,
                input=payload,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if process.returncode not in {0, 1}:
                raise GitIgnoreError("isolated Git ignore evaluation failed")
            fields = process.stdout.split(b"\0")
            if fields and fields[-1] == b"":
                fields.pop()
            if len(fields) % 4:
                raise GitIgnoreError("isolated Git ignore evaluation returned malformed output")
            ignored = set()
            for offset in range(0, len(fields), 4):
                pattern = fields[offset + 2].decode("utf-8", "strict")
                path = fields[offset + 3].decode("utf-8", "strict")
                normalized = posixpath.normpath(path)
                if normalized in paths:
                    if pattern.startswith("!"):
                        ignored.discard(normalized)
                    else:
                        ignored.add(normalized)
            return ignored
    except GitIgnoreError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise GitIgnoreError("isolated Git ignore evaluation was unavailable") from exc


def target_safety(
    root: Path,
    candidate: Path,
    ignored_paths: set[str],
) -> tuple[str | None, Path | None]:
    """Check a local link target without reading it or exposing its path."""
    try:
        resolved_root = Path(root).resolve(strict=True)
        lexical = Path(os.path.abspath(candidate))
        if not lexical.is_relative_to(resolved_root):
            return "local target traverses outside the repository", None
        resolved = candidate.resolve(strict=True)
    except RuntimeError:
        return "local target contains a symlink loop", None
    except (OSError, ValueError):
        return "local target is missing or unavailable", None
    if not resolved.is_relative_to(resolved_root):
        return "local target resolves outside the repository", None

    relatives = {
        lexical.relative_to(resolved_root).as_posix(),
        resolved.relative_to(resolved_root).as_posix(),
    }
    for relative in relatives:
        parts = PurePosixPath(relative).parts
        if (
            any(part in TARGET_LOCAL_DIRS for part in parts)
            or is_credential_path(relative)
            or relative in ignored_paths
        ):
            return "local target is excluded runtime, credential, or ignored content", None
    return None, resolved


def is_credential_path(relative: str) -> bool:
    """Return whether a repository-relative path names credential material."""
    basename = PurePosixPath(relative).name.casefold()
    env_example = basename.endswith((".example", ".sample", ".template"))
    return (
        basename == ".env"
        or (basename.startswith(".env.") and not env_example)
        or basename in TARGET_CREDENTIAL_NAMES
        or PurePosixPath(basename).suffix.casefold() in TARGET_CREDENTIAL_SUFFIXES
    )


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(report: Report, path: Path):
    """Load JSON, reporting failures without raising."""
    import json

    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.fail(f"{path.name}: invalid JSON: {exc}")
        return None


def is_placeholder(value) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value.strip()))


def has_embedded_placeholder(value) -> bool:
    return isinstance(value, str) and bool(EMBEDDED_PLACEHOLDER_RE.search(value))


def is_number(value) -> bool:
    """True for int/float values; bools are not numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_integer_number(value) -> bool:
    return is_number(value) and float(value).is_integer()


def is_positive_integer(value) -> bool:
    return is_integer_number(value) and value > 0


def passing_result_problem(item) -> str | None:
    """Return a diagnostic when evidence lacks an explicit passing result.

    A typed ``outcome`` of ``passed``/``pass`` or an ``observed_result`` that
    begins with ``passed``/``pass`` counts. Merely omitting a failure word
    does not.
    """
    if not isinstance(item, dict):
        return "evidence must be an object with a passing observed_result"
    outcome = item.get("outcome")
    if isinstance(outcome, str) and outcome.strip().lower() in PASSING_OUTCOMES:
        return None
    observed = item.get("observed_result")
    if isinstance(observed, str):
        stripped = observed.strip()
        if PASSING_RESULT_RE.match(stripped):
            return None
        if FAILURE_RESULT_RE.match(stripped):
            return f"records a failure result: {observed!r}"
        return (
            "requires an explicit passing outcome (for example "
            f"'passed: ...' or outcome='passed'); found {observed!r}"
        )
    return "requires an explicit passing outcome (for example 'passed: ...' or outcome='passed')"


def is_nonnegative_number(value) -> bool:
    return is_number(value) and value >= 0


def is_iso_datetime(value) -> bool:
    if not isinstance(value, str) or not ISO_DATETIME_RE.match(value.strip()):
        return False
    text = value.strip().replace("Z", "+00:00").replace(" ", "T")
    try:
        _dt.datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def is_calendar_date(value) -> bool:
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        return False
    try:
        _dt.date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def parsed_iso(value) -> _dt.datetime | None:
    if not is_iso_datetime(value):
        return None
    text = value.strip().replace("Z", "+00:00").replace(" ", "T")
    parsed = _dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def is_real_string(value, *, allow_unknown: bool = False) -> bool:
    """Non-empty string that is neither a placeholder nor a not-recorded marker."""
    if not isinstance(value, str) or not value.strip():
        return False
    stripped = value.strip()
    if is_placeholder(stripped) or has_embedded_placeholder(stripped):
        return False
    if not allow_unknown and stripped.lower() in {"unknown", "missing", "not_recorded", "n/a"}:
        return False
    return True


def scan_placeholders(report: Report, where: str, obj, skip: frozenset[str] = frozenset()) -> None:
    """Reject placeholders, including placeholders embedded inside strings."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in skip:
                continue
            scan_placeholders(report, f"{where}.{key}", value, skip)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            scan_placeholders(report, f"{where}[{index}]", value, skip)
    elif is_placeholder(obj) or has_embedded_placeholder(obj):
        report.fail(f"{where}: unfilled placeholder {obj!r} in a non-template record")


def resolve_repo_artifact(root: Path, reference, extra_roots: tuple[Path, ...] = ()) -> Path | None:
    """Resolve a repo-relative artifact reference, rejecting escapes.

    Absolute paths, ``..`` traversal, and resolutions outside ``root`` are
    rejected. ``extra_roots`` are additional bases tried for convenience.
    """
    if not isinstance(reference, str) or not reference.strip():
        return None
    text = reference.strip()
    candidate_path = Path(text)
    if candidate_path.is_absolute() or any(part == ".." for part in candidate_path.parts):
        return None
    resolved_root = root.resolve()
    candidates = [base / candidate_path for base in (extra_roots + (root,))]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(resolved_root):
            continue
        if resolved.is_file():
            return resolved
    return None


def load_structured_json(reference, root: Path) -> tuple[dict | None, str | None]:
    """Load a referenced JSON evidence object; return (data, problem)."""
    import json

    path = resolve_repo_artifact(root, reference) if is_real_string(reference) else None
    if path is None:
        return None, "must exist inside the repository"
    if path.suffix.lower() != ".json":
        return None, "must be a structured JSON artifact, not an unstructured document"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, "must contain parseable JSON"
    if not isinstance(data, dict):
        return None, "must contain a JSON object"
    return data, None
