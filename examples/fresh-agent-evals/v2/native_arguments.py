"""Bounded, typed OpenCode file-tool argument evidence for v2 trials."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "opencode-lifecycle"))

from bridge_core import redact_secret_shapes  # noqa: E402

ARGUMENT_SCHEMA_VERSION = 1
MAX_NATIVE_ARGUMENT_BYTES = 8192
MAX_NATIVE_FIELD_BYTES = 2048
MAX_READ_OFFSET = 100_000_000
MAX_READ_LIMIT = 10_000
MAX_FILTER_PATTERNS = 32
NATIVE_ARGUMENT_TOOLS = {"read", "list", "glob", "grep"}
PATH_ALIASES = {"path", "file_path", "filePath"}
REDACTED_MARKERS = ("<REDACTED>", "<HOME>", "<REPO>")
SECRET_SHAPE_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|AKIA[0-9A-Z]{16}|bearer\s+\S+)"
)


def _encoded_text(value: object, *, limit: int = MAX_NATIVE_FIELD_BYTES) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value or value != value.strip():
        return None, "malformed"
    if any(char in value for char in "\0\r\n"):
        return None, "malformed"
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None, "malformed"
    if len(encoded) > limit:
        return None, "truncated"
    if REDACTED_MARKERS[0] in value or SECRET_SHAPE_RE.search(value) or redact_secret_shapes(value) != value:
        return None, "redacted"
    return value, None


def _safe_workspace_path(value: object, workspace: Path, *, allow_root: bool) -> tuple[str | None, str | None]:
    text, problem = _encoded_text(value)
    if problem:
        return None, problem
    assert text is not None
    workspace_prefix = str(workspace.resolve())
    if text == "<WORKSPACE>" or text == workspace_prefix:
        relative = "."
    elif text.startswith("<WORKSPACE>/"):
        relative = text[len("<WORKSPACE>/"):]
    elif text.startswith(workspace_prefix + "/"):
        relative = text[len(workspace_prefix) + 1:]
    elif text.startswith(("<REPO>", "<HOME>", "/", "~")):
        return None, "outside_workspace"
    else:
        relative = text

    if not relative or "\\" in relative:
        return None, "malformed"
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "~"} for part in path.parts):
        return None, "escaping"
    if relative == ".":
        return (".", None) if allow_root else (None, "generic_root")
    if any(part in {"<REDACTED>", "<HOME>", "<REPO>"} for part in path.parts):
        return None, "redacted"
    if any(any(char in part for char in "*?[]{}$") for part in path.parts):
        return None, "dynamic_path"
    return path.as_posix(), None


def _safe_glob_pattern(value: object) -> tuple[str | None, str | None]:
    text, problem = _encoded_text(value)
    if problem:
        return None, problem
    assert text is not None
    if text.startswith(("/", "~", "<WORKSPACE>", "<REPO>", "<HOME>")) or "\\" in text:
        return None, "escaping"
    if any(part == ".." for part in PurePosixPath(text).parts):
        return None, "escaping"
    return text, None


def _path_aliases(raw: dict) -> tuple[object | None, str | None]:
    present = [key for key in PATH_ALIASES if key in raw]
    if not present:
        return None, "missing_path"
    values = [raw[key] for key in present]
    if any(not isinstance(item, str) for item in values) or len(set(values)) != 1:
        return None, "conflicting_path_aliases"
    return values[0], None


def normalize_native_arguments(
    tool: str,
    raw: object,
    workspace: Path,
) -> tuple[dict | None, str | None]:
    """Retain only a complete safe argument object for read/list/glob/grep."""
    if tool not in NATIVE_ARGUMENT_TOOLS or not isinstance(raw, dict):
        return None, "missing_or_unsupported_arguments"

    common = PATH_ALIASES
    allowed = {
        "read": common | {"offset", "limit"},
        "list": common | {"ignore"},
        "glob": common | {"pattern"},
        "grep": common | {"pattern", "include"},
    }[tool]
    if set(raw) - allowed:
        return None, "unsupported_argument_keys"

    raw_path, path_problem = _path_aliases(raw)
    if path_problem:
        return None, path_problem
    path, path_problem = _safe_workspace_path(
        raw_path, workspace, allow_root=tool in {"list", "glob"},
    )
    if path_problem:
        return None, path_problem

    result: dict = {"tool": tool, "path": path}
    if tool == "read":
        for key, minimum, maximum in (
            ("offset", 0, MAX_READ_OFFSET),
            ("limit", 1, MAX_READ_LIMIT),
        ):
            if key not in raw:
                continue
            value = raw[key]
            if type(value) is not int or not minimum <= value <= maximum:
                return None, f"invalid_{key}"
            result[key] = value
    elif tool == "glob":
        pattern, problem = _safe_glob_pattern(raw.get("pattern"))
        if problem:
            return None, "missing_pattern" if problem == "malformed" and "pattern" not in raw else problem
        result["pattern"] = pattern
    elif tool == "grep":
        pattern, problem = _encoded_text(raw.get("pattern"))
        if problem:
            return None, "missing_pattern" if problem == "malformed" and "pattern" not in raw else problem
        result["pattern"] = pattern
        if "include" in raw:
            include, problem = _safe_glob_pattern(raw["include"])
            if problem:
                return None, problem
            result["include"] = include
    elif tool == "list" and "ignore" in raw:
        ignored = raw["ignore"]
        if (
            not isinstance(ignored, list)
            or len(ignored) > MAX_FILTER_PATTERNS
        ):
            return None, "invalid_ignore_patterns"
        normalized = []
        for item in ignored:
            pattern, problem = _safe_glob_pattern(item)
            if problem:
                return None, "invalid_ignore_patterns"
            normalized.append(pattern)
        result["ignore"] = normalized

    try:
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None, "malformed"
    if len(encoded) > MAX_NATIVE_ARGUMENT_BYTES:
        return None, "truncated"
    return result, None


def _path_has_symlink(path_text: str, workspace: Path) -> bool:
    if workspace.is_symlink():
        return True
    current = workspace
    if path_text != ".":
        for part in PurePosixPath(path_text).parts:
            current = current / part
            if current.is_symlink():
                return True
    try:
        return not current.resolve().is_relative_to(workspace.resolve())
    except OSError:
        return True


def _glob_prefix_has_symlink(pattern: str, workspace: Path) -> bool:
    literal_parts = []
    for part in PurePosixPath(pattern).parts:
        if any(char in part for char in "*?[]{}"):
            break
        literal_parts.append(part)
    if not literal_parts:
        return False
    return _path_has_symlink(PurePosixPath(*literal_parts).as_posix(), workspace)


def validate_native_arguments(action: dict, workspace: Path) -> tuple[dict | None, str | None]:
    """Revalidate normalized structured arguments and native call evidence."""
    if action.get("native_arguments_status") != "observed":
        return None, str(action.get("native_arguments_problem") or "arguments_unknown")
    if type(action.get("native_arguments_schema_version")) is not int or action["native_arguments_schema_version"] != ARGUMENT_SCHEMA_VERSION:
        return None, "unsupported_argument_schema"
    arguments = action.get("native_arguments")
    if not isinstance(arguments, dict) or arguments.get("tool") != action.get("tool"):
        return None, "malformed_arguments"
    tool = action.get("tool")
    if tool not in NATIVE_ARGUMENT_TOOLS:
        return None, "unsupported_tool_type"
    path = arguments.get("path")
    safe_path, problem = _safe_workspace_path(path, workspace, allow_root=tool in {"list", "glob"})
    if problem or safe_path != path:
        return None, problem or "malformed_path"
    if _path_has_symlink(path, workspace):
        return None, "symlink_path"

    expected_keys = {
        "read": {"tool", "path", "offset", "limit"},
        "list": {"tool", "path", "ignore"},
        "glob": {"tool", "path", "pattern"},
        "grep": {"tool", "path", "pattern", "include"},
    }[tool]
    if set(arguments) - expected_keys or not {"tool", "path"} <= set(arguments):
        return None, "malformed_arguments"
    if tool == "read":
        for key, minimum, maximum in (("offset", 0, MAX_READ_OFFSET), ("limit", 1, MAX_READ_LIMIT)):
            if key in arguments and (type(arguments[key]) is not int or not minimum <= arguments[key] <= maximum):
                return None, f"invalid_{key}"
    if tool == "glob":
        pattern, problem = _safe_glob_pattern(arguments.get("pattern"))
        if problem or pattern != arguments.get("pattern"):
            return None, problem or "malformed_pattern"
        if _glob_prefix_has_symlink(pattern, workspace):
            return None, "symlink_pattern_prefix"
    if tool == "grep":
        pattern, problem = _encoded_text(arguments.get("pattern"))
        if problem or pattern != arguments.get("pattern"):
            return None, problem or "malformed_pattern"
        if "include" in arguments:
            include, problem = _safe_glob_pattern(arguments["include"])
            if problem or include != arguments["include"]:
                return None, problem or "malformed_include"
    if tool == "list" and "ignore" in arguments:
        ignore = arguments["ignore"]
        if not isinstance(ignore, list) or len(ignore) > MAX_FILTER_PATTERNS:
            return None, "invalid_ignore_patterns"
        for item in ignore:
            pattern, problem = _safe_glob_pattern(item)
            if problem or pattern != item:
                return None, "invalid_ignore_patterns"

    try:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None, "malformed_arguments"
    if len(encoded) > MAX_NATIVE_ARGUMENT_BYTES:
        return None, "truncated"
    if (
        action.get("native_arguments_bytes") != len(encoded)
        or action.get("native_arguments_sha256") != hashlib.sha256(encoded).hexdigest()
    ):
        return None, "argument_digest_mismatch"
    identity = action.get("native_tool_call_id_sha256")
    if (
        not isinstance(identity, str)
        or not re.fullmatch(r"[0-9a-f]{64}", identity)
        or action.get("native_arguments_identity_sha256") != identity
        or action.get("correlation_conflict") is True
    ):
        return None, "missing_or_conflicting_native_identity"
    outcome = action.get("outcome")
    completion_status = action.get("native_arguments_completion_status")
    if completion_status not in {"completed", "error", "denied", "cancelled", "timeout", "skipped"}:
        return None, "missing_explicit_completion"
    if not isinstance(outcome, dict) or completion_status != outcome.get("native_tool_status"):
        return None, "completion_status_mismatch"
    event_sequence = action.get("event_sequence")
    argument_sequence = action.get("native_arguments_event_sequence")
    completion_sequence = action.get("completion_sequence")
    if (
        type(event_sequence) is not int
        or type(argument_sequence) is not int
        or type(completion_sequence) is not int
        or argument_sequence < event_sequence
        or completion_sequence < argument_sequence
    ):
        return None, "missing_or_conflicting_completion"
    return arguments, None


def native_read_output_frame(arguments: dict, output_evidence: dict) -> dict | None:
    """Validate one framed page from a bound native file-read response.

    This consumes only normalized request/output facts. It does not inspect
    the target's final type, size, or contents. Full-source credit additionally
    requires an EOF footer or a validated contiguous page chain.
    """
    if arguments.get("tool") != "read":
        return None
    relative = arguments.get("path")
    if not isinstance(relative, str) or relative == ".":
        return None
    if (
        not isinstance(output_evidence, dict)
        or output_evidence.get("native_output_result_type") != "file_content"
        or output_evidence.get("native_output_content_status") != "observed"
        or output_evidence.get("native_output_file_envelope_status") != "observed"
        or output_evidence.get("native_output_file_path_sha256")
        != hashlib.sha256(relative.encode("utf-8")).hexdigest()
    ):
        return None
    content_bytes = output_evidence.get("native_output_content_bytes")
    first_line = output_evidence.get("native_output_file_first_line")
    last_line = output_evidence.get("native_output_file_last_line")
    line_count = output_evidence.get("native_output_file_line_count")
    footer = output_evidence.get("native_output_file_footer")
    total_lines = output_evidence.get("native_output_file_total_lines")
    next_offset = output_evidence.get("native_output_file_next_offset")
    complete = output_evidence.get("native_output_file_complete")
    if (
        type(content_bytes) is not int or content_bytes <= 0
        or type(first_line) is not int or first_line < 1
        or type(last_line) is not int or last_line < first_line
        or type(line_count) is not int or line_count != last_line - first_line + 1
        or type(complete) is not bool
    ):
        return None
    offset = arguments.get("offset", 1)
    if type(offset) is not int or not 0 <= offset <= MAX_READ_OFFSET:
        return None
    requested_first = 1 if offset == 0 else offset
    limit = arguments.get("limit")
    if limit is not None and (
        type(limit) is not int or not 1 <= limit <= MAX_READ_LIMIT
        or line_count > limit or last_line > requested_first + limit - 1
    ):
        return None
    if first_line != requested_first:
        return None
    if footer == "eof":
        if (
            complete is not True
            or type(total_lines) is not int
            or total_lines != last_line
            or next_offset is not None
        ):
            return None
    elif footer == "capped":
        if (
            complete is not False
            or total_lines is not None
            or type(next_offset) is not int
            or next_offset != last_line + 1
        ):
            return None
    else:
        return None
    return {
        "first_line": first_line,
        "last_line": last_line,
        "line_count": line_count,
        "content_bytes": content_bytes,
        "footer": footer,
        "total_lines": total_lines,
        "next_offset": next_offset,
        "request_offset": requested_first,
    }


def native_read_covers_source(arguments: dict, output_evidence: dict) -> bool:
    """Return whether one bound native read reached a truthful EOF from line 1."""
    frame = native_read_output_frame(arguments, output_evidence)
    return (
        frame is not None
        and frame["footer"] == "eof"
        and frame["first_line"] == 1
        and frame["content_bytes"] > 0
    )


def native_argument_path(action: dict, workspace: Path) -> str | None:
    arguments, _problem = validate_native_arguments(action, workspace)
    path = arguments.get("path") if isinstance(arguments, dict) else None
    return path if isinstance(path, str) else None
