"""Allowlisted OpenCode/Codex event normalization for v2 trial evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import datetime as dt
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "opencode-lifecycle"))

from bridge_core import redact_secret_shapes  # noqa: E402
from native_arguments import (  # noqa: E402
    ARGUMENT_SCHEMA_VERSION,
    NATIVE_ARGUMENT_TOOLS,
    normalize_native_arguments,
)
from tool_outcomes import OUTPUT_HASH_BYTE_LIMIT, normalize_tool_actions  # noqa: E402
from commands import filename_discovery_scope, normalize_command  # noqa: E402
from shell_parser import MAX_COMMAND_BYTES, parse_shell_command  # noqa: E402

NORMALIZED_COMMAND_LIMIT = 2048
COMMAND_PREVIEW_LIMIT = 300
ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/<>])/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\(?:[^\\\s\"']+\\?)+")
ALLOWED_WRAPPER_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:bin|usr/bin)/(?:bash|sh)(?=\s+-lc(?:\s|$))")
PATCH_FILE_HEADER = re.compile(r"(?m)^\*\*\* (?:Add|Update|Delete) File: ([^\r\n]+)$")
STATUS_VALUES = {"completed", "failed", "error", "denied", "cancelled", "timeout", "running", "pending", "unknown"}
CODEX_TODO_LIST_LIFECYCLE = {"item.started", "item.updated", "item.completed"}
SECRET_SHAPE_RE = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16})")
OUTPUT_NAME_RE = re.compile(r"[A-Za-z0-9_. -]{1,255}/?\Z")
LS_LONG_ROW_RE = re.compile(
    r"^(?P<mode>[-bcdlps][rwxStTs-]{9}[+@.]?)\s+"
    r"(?P<links>\d+)\s+\S+\s+\S+\s+"
    r"(?P<size>\d+(?:,\s*\d+)?(?:\.\d+)?[KMGTPE]?)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+(?:\d{2}:\d{2}|\d{4})\s+\S.*$"
)
LS_TOTAL_RE = re.compile(r"total\s+\d+\Z")
OPENCODE_TERMINAL_STATUSES = {
    "completed", "error", "failed", "denied", "permission_denied", "rejected",
    "blocked", "cancelled", "canceled", "timeout", "timed_out", "skipped",
}


def _event_epoch(event: dict) -> float | None:
    raw = next((event[key] for key in ("timestamp", "time", "created", "at", "ts") if key in event), None)
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e15:
            return value / 1e6
        if value > 1e12:
            return value / 1e3
        return value
    if isinstance(raw, str):
        try:
            return dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def parse_jsonl(raw: bytes) -> tuple[list[dict], bool, int]:
    """Parse native JSONL while exposing whether any nonempty line was lost."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
        decode_complete = False
    else:
        decode_complete = True
    events: list[dict] = []
    invalid = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            invalid += 1
            continue
        if isinstance(payload, dict):
            events.append(payload)
        elif isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
        else:
            invalid += 1
    return events, decode_complete and invalid == 0, invalid


def _safe_text(value: str, workspace: Path, repo: Path) -> str:
    text = redact_secret_shapes(value)
    text = SECRET_SHAPE_RE.sub("<REDACTED>", text)
    for original, replacement in (
        (str(workspace.resolve()), "<WORKSPACE>"),
        (str(repo.resolve()), "<REPO>"),
        (str(Path.home().resolve()), "<HOME>"),
    ):
        text = text.replace(original, replacement)
    return text


def _session_id(events: list[dict], harness: str) -> str | None:
    keys = {"codex": ("thread_id", "threadID"), "opencode": ("sessionID", "session_id", "sessionId")}[harness]
    for event in events:
        candidates = [event]
        if isinstance(event.get("item"), dict):
            candidates.append(event["item"])
        for value in candidates:
            for key in keys:
                if isinstance(value.get(key), str) and value[key]:
                    return value[key]
    return None


def _opencode_input_groups(events: list[dict]) -> dict[str, dict]:
    """Index native tool inputs by call identity without copying them to output."""
    groups: dict[str, dict] = {}
    for sequence, event in enumerate(events):
        part = event.get("part") if isinstance(event, dict) else None
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        raw_tool = part.get("tool")
        if not isinstance(raw_tool, str):
            continue
        raw_id = part.get("callID")
        key = (
            hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
            if isinstance(raw_id, str) and raw_id
            else f"event:{sequence}"
        )
        group = groups.setdefault(key, {
            "tools": set(), "inputs": [], "outputs": [], "terminal_statuses": [],
            "invalid_input": False,
        })
        group["tools"].add(raw_tool.casefold())
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = state.get("status")
        if isinstance(status, str) and status.casefold() in OPENCODE_TERMINAL_STATUSES:
            group["terminal_statuses"].append((sequence, status.casefold()))
        if "output" in state:
            group["outputs"].append((sequence, status.casefold() if isinstance(status, str) else None, state["output"]))
        if "input" not in state:
            continue
        value = state.get("input")
        if not isinstance(value, dict):
            group["invalid_input"] = True
        else:
            group["inputs"].append((sequence, value))
    return groups


def _same_input_values(inputs: list[tuple[int, dict]]) -> bool:
    if not inputs:
        return False
    try:
        canonical = json.dumps(inputs[0][1], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return all(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == canonical
            for _sequence, value in inputs[1:]
        )
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return False


def _filename_entry(value: object) -> bool:
    return (
        isinstance(value, str)
        and value not in {".", ".."}
        and OUTPUT_NAME_RE.fullmatch(value) is not None
    )


def _output_lists_names(value: object, relative_path: str | None = None) -> bool:
    """Recognize only explicit entry framing; prose is never access evidence."""
    if isinstance(value, dict):
        if not _structured_directory_output(value, relative_path):
            return False
        entries = value.get("entries")
        return (
            isinstance(entries, list)
            and len(entries) <= 512
            and all(
                _filename_entry(item)
                if isinstance(item, str)
                else _filename_entry(item.get("name")) and set(item) == {"name"}
                if isinstance(item, dict)
                else False
                for item in entries
            )
        )
    if not isinstance(value, str):
        return False
    if len(value.encode("utf-8", "replace")) > 65_536:
        return False
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if relative_path and lines:
        headers = {
            relative_path,
            relative_path.rstrip("/") + "/",
            relative_path.rstrip("/") + ":",
            Path(relative_path).name,
            Path(relative_path).name + "/",
            Path(relative_path).name + ":",
        }
        if lines[0] in headers:
            lines = lines[1:]
            if not lines:
                return True
    entries = []
    for line in lines:
        line = re.sub(r"^(?:├──|└──|├─|└─|[-*•])\s*", "", line).strip()
        if not _filename_entry(line):
            return False
        entries.append(line)
    return bool(entries) and len(entries) <= 512


def _structured_directory_output(value: object, relative_path: str | None) -> bool:
    if not isinstance(value, dict):
        return False
    output_kind = value.get("type")
    if not isinstance(output_kind, str) or output_kind not in {"directory", "directory_listing"}:
        return False
    keys = set(value)
    if keys not in ({"type", "entries"}, {"type", "path", "entries"}):
        return False
    if "path" in value and (
        not isinstance(relative_path, str)
        or not isinstance(value.get("path"), str)
        or value["path"] != relative_path
    ):
        return False
    entries = value.get("entries")
    return isinstance(entries, list) and len(entries) <= 512


def _native_output_path_matches(
    output_path: object, relative_path: str | None, workspace: Path | None,
) -> bool:
    if not isinstance(output_path, str) or not isinstance(relative_path, str) or workspace is None:
        return False
    normalized, problem = normalize_native_arguments(
        "read", {"path": output_path}, workspace,
    )
    return problem is None and isinstance(normalized, dict) and normalized.get("path") == relative_path


def _native_directory_frame(
    value: object, relative_path: str | None, workspace: Path | None,
) -> dict | None:
    """Parse OpenCode's bounded path/type/entries directory envelope as names only."""
    if not isinstance(value, str):
        return None
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if encoded_size > 65_536:
        return None
    lines = value.splitlines()
    if len(lines) < 4 or lines[1] != "<type>directory</type>" or lines[2] != "<entries>" or lines[-1] != "</entries>":
        return None
    path_match = re.fullmatch(r"<path>([^<>\r\n]+)</path>", lines[0])
    if path_match is None or not _native_output_path_matches(path_match.group(1), relative_path, workspace):
        return None
    raw_entries = lines[3:-1]
    if raw_entries == ["", "", "(0 entries)"]:
        entries = []
    else:
        entries = []
        for raw in raw_entries:
            item = re.sub(r"^(?:├──|└──|├─|└─|[-*•])\s*", "", raw).strip()
            if not _filename_entry(item):
                return None
            entries.append(item)
    if len(entries) > 512:
        return None
    return {"entries": entries, "path_sha256": hashlib.sha256(relative_path.encode("utf-8")).hexdigest()}


def _text_directory_output(value: object, relative_path: str | None) -> bool:
    if not isinstance(value, str) or len(value.encode("utf-8", "replace")) > 65_536:
        return False
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return False
    headers = set()
    if isinstance(relative_path, str):
        headers.update({
            relative_path,
            relative_path.rstrip("/") + "/",
            relative_path.rstrip("/") + ":",
            Path(relative_path).name,
            Path(relative_path).name + "/",
            Path(relative_path).name + ":",
        })
    header_framed = lines[0] in headers
    entries = lines[1:] if header_framed else lines
    list_framed = bool(entries) and all(
        re.match(r"^(?:├──|└──|├─|└─|[-*•])\s+", line) is not None
        for line in entries
    )
    return (header_framed or list_framed) and _output_lists_names(value, relative_path)


def _ls_header_matches_scope(header: str, discovery: dict) -> bool:
    if not header or "\\" in header or header.startswith(("/", "~", "$")):
        return False
    path = PurePosixPath(header)
    if path.is_absolute() or ".." in path.parts:
        return False
    normalized = path.as_posix()
    bases = [PurePosixPath(value).as_posix() for value in discovery.get("paths", [])]
    recursive = discovery.get("recursive") is True
    return any(
        normalized == base
        or recursive and (base == "." or normalized.startswith(base.rstrip("/") + "/"))
        for base in bases
    )


def _ls_shell_segment(command: object, harness: str):
    if not isinstance(command, str):
        return None
    parsed = parse_shell_command(command, harness)
    if (
        parsed is None
        or len(parsed.segments) != 1
        or not parsed.segments[0].argv
        or Path(parsed.segments[0].argv[0]).name.casefold() != "ls"
    ):
        return None
    return parsed.segments[0]


def _long_ls_output_lists_names(value: object, discovery: dict) -> bool:
    """Accept a bounded, structurally valid ls long listing, never prose."""
    if not isinstance(value, str):
        return False
    try:
        if len(value.encode("utf-8")) > 65_536:
            return False
    except UnicodeEncodeError:
        return False
    lines = value.splitlines()
    if not lines or len(lines) > 512:
        return False
    rows = 0
    headers = 0
    recursive = discovery.get("recursive") is True
    multiple_operands = len(discovery.get("paths", [])) > 1
    for line in lines:
        if not line:
            if recursive or multiple_operands:
                continue
            return False
        if LS_TOTAL_RE.fullmatch(line):
            continue
        if line.endswith(":"):
            if not _ls_header_matches_scope(line[:-1], discovery):
                return False
            headers += 1
            continue
        if LS_LONG_ROW_RE.fullmatch(line) is None:
            return False
        rows += 1
    return rows > 0 and (not recursive or headers > 0)


def _shell_ls_output_lists_names(
    action: dict, harness: str, output: object, workspace: Path,
) -> bool:
    """Bind names evidence to a complete bounded ls command and parsed output."""
    command = action.get("command")
    if not isinstance(command, str) or action.get("command_truncated") is not False:
        return False
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if (
        action.get("command_sha256") != hashlib.sha256(command_bytes).hexdigest()
        or type(action.get("command_bytes")) is not int
        or action["command_bytes"] != len(command_bytes)
    ):
        return False
    segment = _ls_shell_segment(command, harness)
    if segment is None:
        return False
    discovery = filename_discovery_scope(segment, workspace)
    if discovery is None:
        return False

    if isinstance(output, str) and len(output.encode("utf-8", "replace")) <= 65_536:
        lines = output.splitlines()
        if lines and len(lines) <= 512:
            plain_names = True
            saw_name = False
            for line in lines:
                if not line:
                    if discovery.get("recursive") is True or len(discovery.get("paths", [])) > 1:
                        continue
                    plain_names = False
                    break
                if line.endswith(":"):
                    if not _ls_header_matches_scope(line[:-1], discovery):
                        plain_names = False
                        break
                    continue
                if line not in {".", ".."} and (
                    not _filename_entry(line) or any(char.isspace() for char in line)
                ):
                    plain_names = False
                    break
                saw_name = True
            if plain_names and saw_name and (
                discovery.get("recursive") is not True or any(line.endswith(":") for line in lines)
            ):
                return True
    return _long_ls_output_lists_names(output, discovery)


def _file_output_frame(
    value: object, relative_path: str | None, arguments: dict,
    workspace: Path | None = None,
) -> dict | None:
    if isinstance(value, dict):
        output_kind = value.get("type")
        if (
            set(value) != {"type", "path", "content", "complete"}
            or not isinstance(output_kind, str)
            or output_kind not in {"file", "file_content"}
            or not isinstance(relative_path, str)
            or value.get("path") != relative_path
            or not isinstance(value.get("content"), str)
            or type(value.get("complete")) is not bool
        ):
            return None
        content = value["content"]
        return {
            "content_bytes": len(content.encode("utf-8")),
            "first_line": None,
            "last_line": None,
            "line_count": None,
            "complete": False,
            "envelope_status": "structured",
            "path_sha256": hashlib.sha256(relative_path.encode("utf-8")).hexdigest(),
            "footer": "unknown",
            "total_lines": None,
            "next_offset": None,
        }
    if not isinstance(value, str):
        return None
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if encoded_size > OUTPUT_HASH_BYTE_LIMIT:
        return None
    lines = value.splitlines()
    if not lines or len(lines) > 10_000:
        return None

    # OpenCode 1.18.31 returns a real file-read envelope. Its absolute request
    # path is normalized back to the already-validated workspace-relative
    # argument; the host path is never retained in normalized evidence.
    if (
        len(lines) >= 5
        and lines[1] == "<type>file</type>"
        and lines[2] == "<content>"
        and lines[-1] == "</content>"
    ):
        path_match = re.fullmatch(r"<path>([^<>\r\n]+)</path>", lines[0])
        if path_match is None or not _native_output_path_matches(
            path_match.group(1), relative_path, workspace,
        ):
            return None
        # A close tag or a second envelope marker in the body is not allowed;
        # source lines themselves remain prefixed with their returned number.
        if lines.count("</content>") != 1 or any(
            line in {"<path>", "<type>file</type>", "<content>"}
            for line in lines[3:-1]
        ):
            return None
        footer_line = lines[-2]
        body = lines[3:-2]
        if body and body[-1] == "":
            body = body[:-1]
        framed: list[tuple[int, str]] = []
        for line in body:
            match = re.fullmatch(r"(\d+): ?(.*)", line)
            if match is None:
                return None
            framed.append((int(match.group(1)), match.group(2)))
        numbers = [number for number, _content in framed]
        expected_first = arguments.get("offset", 1)
        expected_first = 1 if expected_first == 0 else expected_first
        if (
            type(expected_first) is not int
            or not 1 <= expected_first <= 100_000_000
            or any(right != left + 1 for left, right in zip(numbers, numbers[1:]))
            or (numbers and numbers[0] != expected_first)
        ):
            return None
        limit = arguments.get("limit")
        if limit is not None and (
            type(limit) is not int
            or len(numbers) > limit
            or (numbers and numbers[-1] > expected_first + limit - 1)
        ):
            return None
        eof = re.fullmatch(r"\(End of file - total (\d+) lines\)", footer_line)
        capped = re.fullmatch(
            r"\(Output capped at 50 KB\. Showing lines (\d+)-(\d+)\. Use offset=(\d+) to continue\.\)",
            footer_line,
        )
        if eof is not None:
            total = int(eof.group(1))
            if (
                total > 100_000_000
                or (numbers and numbers[-1] != total)
                or (not numbers and total != 0)
            ):
                return None
            footer, next_offset = "eof", None
        elif capped is not None:
            first, last, next_offset = (int(value) for value in capped.groups())
            if (
                not numbers
                or first != numbers[0]
                or last != numbers[-1]
                or next_offset != last + 1
                or next_offset > 100_000_000
                or encoded_size < 50_000
            ):
                return None
            total, footer = None, "capped"
        else:
            return None
        return {
            "content_bytes": sum(len(content.encode("utf-8")) for _number, content in framed),
            "first_line": numbers[0] if numbers else None,
            "last_line": numbers[-1] if numbers else (0 if footer == "eof" else None),
            "line_count": len(numbers),
            "complete": footer == "eof",
            "envelope_status": "observed",
            "path_sha256": hashlib.sha256(relative_path.encode("utf-8")).hexdigest(),
            "footer": footer,
            "total_lines": total,
            "next_offset": next_offset,
        }

    # Retain bounded line-number-only results as partial access evidence. They
    # have no path/type/footer proof and can never establish whole-source EOF.
    offset = arguments.get("offset", 1)
    expected_first = 1 if offset == 0 else offset
    framed: list[tuple[int, str]] = []
    for line in lines:
        match = re.fullmatch(r"\s*(\d+)\s*(?::|\t|→)\s?(.*)", line)
        if match is None:
            return None
        framed.append((int(match.group(1)), match.group(2)))
    numbers = [number for number, _content in framed]
    if (
        not numbers
        or numbers[0] != expected_first
        or any(right != left + 1 for left, right in zip(numbers, numbers[1:]))
    ):
        return None
    return {
        "content_bytes": sum(len(content.encode("utf-8")) for _number, content in framed),
        "first_line": numbers[0],
        "last_line": numbers[-1],
        "line_count": len(numbers),
        "complete": False,
        "envelope_status": "unframed",
        "path_sha256": None,
        "footer": "unknown",
        "total_lines": None,
        "next_offset": None,
    }


def _record_native_output_framing(
    action: dict, tool: str, outputs: list[tuple[int, str | None, object]],
    terminal_statuses: list[tuple[int, str]], arguments: dict | None, workspace: Path,
) -> None:
    action.update({
        "native_output_result_type": "unknown",
        "native_output_names_status": "unknown",
        "native_output_content_status": "unknown",
        "native_output_content_bytes": None,
        "native_output_file_first_line": None,
        "native_output_file_last_line": None,
        "native_output_file_line_count": None,
        "native_output_file_complete": None,
        "native_output_file_envelope_status": "unknown",
        "native_output_file_path_sha256": None,
        "native_output_file_footer": None,
        "native_output_file_total_lines": None,
        "native_output_file_next_offset": None,
        "native_output_event_sequence": None,
        "native_output_identity_sha256": None,
    })
    if not outputs or len(terminal_statuses) != 1 or terminal_statuses[0][1] != "completed":
        return
    output_sequence, _output_status, raw_output = outputs[-1]
    identity = action.get("native_tool_call_id_sha256")
    action["native_output_event_sequence"] = output_sequence
    action["native_output_identity_sha256"] = identity if isinstance(identity, str) else None
    if any(value != raw_output for _sequence, _status, value in outputs[:-1]):
        return
    outcome = action.get("outcome") if isinstance(action.get("outcome"), dict) else {}
    output_type = outcome.get("native_output_type")
    relative = arguments.get("path") if isinstance(arguments, dict) else None
    names = _output_lists_names(raw_output, relative if isinstance(relative, str) else None)
    if output_type not in {"text", "structured"}:
        return
    if tool in {"bash", "shell"}:
        exit_code = outcome.get("shell_exit_code")
        if (
            output_type == "text"
            and outcome.get("output_hash_status") == "observed"
            and type(outcome.get("output_bytes")) is int
            and outcome.get("native_tool_status") == "completed"
            and outcome.get("shell_exit_code_status") == "observed"
            and type(exit_code) is int
            and exit_code == 0
            and _shell_ls_output_lists_names(action, "opencode", raw_output, workspace)
        ):
            action["native_output_names_status"] = "observed"
            action["native_output_result_type"] = "directory_listing"
        return
    if tool == "read" and isinstance(arguments, dict):
        relative_path = relative if isinstance(relative, str) else None
        directory_frame = _native_directory_frame(raw_output, relative_path, workspace)
        if directory_frame is not None:
            action["native_output_result_type"] = "directory_listing"
            action["native_output_names_status"] = "observed"
        elif _structured_directory_output(raw_output, relative_path) or _text_directory_output(raw_output, relative_path):
            action["native_output_result_type"] = "directory_listing"
            action["native_output_names_status"] = "observed" if names else "unknown"
        else:
            frame = _file_output_frame(raw_output, relative_path, arguments, workspace)
            if frame is not None:
                action.update({
                    "native_output_result_type": "file_content",
                    "native_output_content_status": "observed",
                    "native_output_content_bytes": frame["content_bytes"],
                    "native_output_file_first_line": frame["first_line"],
                    "native_output_file_last_line": frame["last_line"],
                    "native_output_file_line_count": frame["line_count"],
                    "native_output_file_complete": frame["complete"],
                    "native_output_file_envelope_status": frame["envelope_status"],
                    "native_output_file_path_sha256": frame["path_sha256"],
                    "native_output_file_footer": frame["footer"],
                    "native_output_file_total_lines": frame["total_lines"],
                    "native_output_file_next_offset": frame["next_offset"],
                })
    elif tool in {"list", "glob"}:
        action["native_output_result_type"] = "filename_discovery"
        action["native_output_names_status"] = "observed" if names else "unknown"
    elif tool == "grep":
        action["native_output_result_type"] = "content_search"
    else:
        action["native_output_names_status"] = "observed" if names else "unknown"


def _opencode_shell_text(raw_input: dict) -> tuple[str | None, str | None]:
    values = [raw_input[key] for key in ("command", "cmd") if key in raw_input]
    if not values:
        return None, "missing_command"
    if any(not isinstance(value, str) for value in values) or len(set(values)) != 1:
        return None, "conflicting_command_aliases"
    return values[0], None


def _replace_command_evidence(action: dict, raw_command: str | None, workspace: Path, repo: Path) -> None:
    if (
        raw_command is None
        or action.get("correlation_conflict") is True
        or action.get("native_tool_call_id_sha256") is None
    ):
        action.update({
            "command": None,
            "command_preview": None,
            "command_sha256": None,
            "command_bytes": None,
            "command_truncated": None,
        })
        return
    safe = _safe_text(raw_command, workspace, repo)
    try:
        encoded = safe.encode("utf-8")
    except UnicodeEncodeError:
        safe = ""
        encoded = b""
    if not safe:
        action.update({
            "command": None,
            "command_preview": None,
            "command_sha256": None,
            "command_bytes": None,
            "command_truncated": None,
        })
        return
    masked = ALLOWED_WRAPPER_PATH_RE.sub("", safe)
    if ABSOLUTE_PATH_RE.search(masked) or WINDOWS_ABSOLUTE_PATH_RE.search(safe):
        action.update({
            "command": None,
            "command_preview": None,
            "command_sha256": hashlib.sha256(encoded).hexdigest(),
            "command_bytes": len(encoded),
            "command_truncated": True,
            "command_redaction_status": "private_path",
        })
        return
    action["command_preview"] = safe[:COMMAND_PREVIEW_LIMIT]
    action["command"] = safe if len(encoded) <= MAX_COMMAND_BYTES else None
    action["command_sha256"] = hashlib.sha256(encoded).hexdigest()
    action["command_bytes"] = len(encoded)
    action["command_truncated"] = len(encoded) > MAX_COMMAND_BYTES
    action["command_redaction_status"] = "none"


def _file_action_path(tool: str, raw_input: dict | None) -> str | None:
    if not isinstance(raw_input, dict):
        return None
    if tool in {"edit", "write"}:
        candidates = [raw_input[key] for key in ("filePath", "file_path", "path") if key in raw_input]
        return candidates[0] if candidates and all(value == candidates[0] for value in candidates) and isinstance(candidates[0], str) else None
    if tool == "apply_patch":
        patch = raw_input.get("patchText")
        if not isinstance(patch, str):
            return None
        paths = PATCH_FILE_HEADER.findall(patch)
        return paths[0] if len(paths) == 1 else None
    return None


def _enrich_opencode_actions(actions: list[dict], events: list[dict], workspace: Path, repo: Path) -> None:
    groups = _opencode_input_groups(events)
    for action in actions:
        tool = action.get("tool")
        identity = action.get("native_tool_call_id_sha256")
        event_sequence = action.get("event_sequence")
        group_key = identity if isinstance(identity, str) else f"event:{event_sequence}"
        group = groups.get(group_key, {"tools": set(), "inputs": [], "outputs": [], "invalid_input": False})
        inputs = group["inputs"]
        outputs = group.get("outputs", [])
        raw_input = inputs[0][1] if inputs and _same_input_values(inputs) else None
        input_sequence = inputs[0][0] if inputs and raw_input is not None else None
        native_status = action.get("outcome", {}).get("native_tool_status") if isinstance(action.get("outcome"), dict) else None

        if tool in {"bash", "shell"}:
            if group.get("tools") != {tool} or group.get("invalid_input") or raw_input is None:
                _replace_command_evidence(action, None, workspace, repo)
                continue
            command, problem = _opencode_shell_text(raw_input)
            _replace_command_evidence(action, command if problem is None else None, workspace, repo)
            _record_native_output_framing(action, tool, outputs, group.get("terminal_statuses", []), None, workspace)
            continue

        if tool not in NATIVE_ARGUMENT_TOOLS:
            if tool in {"edit", "write", "apply_patch"}:
                if group.get("tools") != {tool} or group.get("invalid_input") or raw_input is None or action.get("correlation_conflict") is True:
                    _replace_command_evidence(action, None, workspace, repo)
                else:
                    _replace_command_evidence(action, _file_action_path(tool, raw_input), workspace, repo)
                continue
            preview = action.get("command")
            action["command_preview"] = preview if isinstance(preview, str) else None
            continue

        action.update({
            "native_arguments_schema_version": ARGUMENT_SCHEMA_VERSION,
            "native_arguments_status": "unknown",
            "native_arguments_problem": "missing_or_unsupported_arguments",
            "native_arguments": None,
            "native_arguments_sha256": None,
            "native_arguments_bytes": None,
            "native_arguments_event_sequence": input_sequence,
            "native_arguments_identity_sha256": identity,
            "native_arguments_completion_status": native_status,
        })
        if group.get("tools") != {tool}:
            action["native_arguments_problem"] = "conflicting_tool_identity"
            _replace_command_evidence(action, None, workspace, repo)
            _record_native_output_framing(action, tool, outputs, group.get("terminal_statuses", []), None, workspace)
            continue
        if group.get("invalid_input") or raw_input is None:
            action["native_arguments_problem"] = "malformed_or_missing_input"
            _replace_command_evidence(action, None, workspace, repo)
            _record_native_output_framing(action, tool, outputs, group.get("terminal_statuses", []), None, workspace)
            continue
        if not isinstance(identity, str) or action.get("correlation_conflict") is True:
            action["native_arguments_problem"] = "missing_or_conflicting_native_identity"
            _replace_command_evidence(action, None, workspace, repo)
            _record_native_output_framing(action, tool, outputs, group.get("terminal_statuses", []), None, workspace)
            continue
        arguments, problem = normalize_native_arguments(tool, raw_input, workspace)
        if problem is not None or arguments is None:
            action["native_arguments_problem"] = problem or "malformed_arguments"
            _replace_command_evidence(action, None, workspace, repo)
            _record_native_output_framing(action, tool, outputs, group.get("terminal_statuses", []), None, workspace)
            continue
        encoded = json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        action.update({
            "native_arguments_status": "observed",
            "native_arguments_problem": None,
            "native_arguments": arguments,
            "native_arguments_sha256": hashlib.sha256(encoded).hexdigest(),
            "native_arguments_bytes": len(encoded),
            "native_arguments_event_sequence": input_sequence,
            "native_arguments_identity_sha256": identity,
        })
        _replace_command_evidence(action, arguments["path"], workspace, repo)
        _record_native_output_framing(
            action, tool, outputs, group.get("terminal_statuses", []), arguments, workspace,
        )


def normalize_opencode(raw: bytes, workspace: Path, repo: Path) -> dict:
    events, complete, invalid = parse_jsonl(raw)
    replacements = (
        (str(workspace.resolve()), "<WORKSPACE>"),
        (str(repo.resolve()), "<REPO>"),
        (str(Path.home().resolve()), "<HOME>"),
    )
    actions = normalize_tool_actions(
        events,
        replacements=replacements,
        redact=redact_secret_shapes,
    )
    _enrich_opencode_actions(actions, events, workspace, repo)
    questions = [action for action in actions if action.get("tool") == "question"]
    question_ids = [action.get("native_tool_call_id_sha256") for action in questions]
    clarification_count = (
        len(set(question_ids))
        if all(isinstance(value, str) for value in question_ids)
        and all(action.get("correlation_conflict") is not True for action in questions)
        else None
    )
    incomplete_tools = {"other", "task", "webfetch", "websearch", "python", "lsp", "skill"}
    unclassified_tools = sum(1 for action in actions if action.get("tool") in incomplete_tools)
    terminal_event_by_call: dict[str, int] = {}
    terminal_event_by_sequence: dict[int, int] = {}
    for sequence, event in enumerate(events):
        part = event.get("part") if isinstance(event, dict) else None
        if not isinstance(part, dict):
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = state.get("status")
        if not isinstance(status, str) or status.casefold() not in OPENCODE_TERMINAL_STATUSES:
            continue
        call_id = part.get("callID")
        if isinstance(call_id, str) and call_id:
            terminal_event_by_call[hashlib.sha256(call_id.encode("utf-8")).hexdigest()] = sequence
        else:
            terminal_event_by_sequence[sequence] = sequence
    for action in actions:
        sequence = action.get("event_sequence")
        call_hash = action.get("native_tool_call_id_sha256")
        action["completion_sequence"] = (
            terminal_event_by_call.get(call_hash)
            if isinstance(call_hash, str)
            else terminal_event_by_sequence.get(sequence)
        )
        if action.get("native_arguments_schema_version") == ARGUMENT_SCHEMA_VERSION:
            action["native_arguments_completion_sequence"] = action["completion_sequence"]
        if type(sequence) is int and sequence < len(events):
            action["event_timestamp_epoch"] = _event_epoch(events[sequence])
    return {
        "actions": actions,
        "events_observed": len(events),
        "invalid_lines": invalid,
        "unclassified_tool_count": unclassified_tools,
        "trace_complete": complete and bool(events) and unclassified_tools == 0,
        "clarification_observations": clarification_count,
        "session_id_sha256": hashlib.sha256(_session_id(events, "opencode").encode()).hexdigest()
        if _session_id(events, "opencode") else None,
    }


def _native_status(item: dict) -> str:
    value = item.get("status")
    if not isinstance(value, str):
        return "missing"
    normalized = value.casefold()
    return normalized if normalized in STATUS_VALUES else "unknown"


def _codex_id(item: dict) -> tuple[str | None, str | None]:
    value = item.get("id")
    if not isinstance(value, str) or not value:
        return None, None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    safe = value if len(value) <= 160 and re.fullmatch(r"[A-Za-z0-9._:-]+", value) else None
    return safe, digest


def _valid_codex_todo_list(item: dict, event_type: str) -> bool:
    if event_type not in CODEX_TODO_LIST_LIFECYCLE or _codex_id(item)[1] is None:
        return False
    items = item.get("items")
    return isinstance(items, list) and all(
        isinstance(todo, dict)
        and isinstance(todo.get("text"), str)
        and type(todo.get("completed")) is bool
        for todo in items
    )


def _explicit_exit(item: dict) -> tuple[str, int | None]:
    if "exit_code" not in item:
        return "missing", None
    value = item.get("exit_code")
    if type(value) is not int:
        return "unknown", None
    return "observed", value


def _codex_file_changes(item: dict, event_type: str, sequence: int, workspace: Path) -> list[dict]:
    item_id, item_id_hash = _codex_id(item)
    status = _native_status(item)
    changes = item.get("changes")
    if not isinstance(changes, list) or not changes:
        return [{
            "tool": "file_change", "native_item_id": item_id,
            "native_item_id_sha256": item_id_hash, "event_sequence": sequence,
            "completion_sequence": sequence if event_type in {"item.completed", "item.failed"} else None,
            "change_index": None,
            "event_timestamp_epoch": None,
            "lifecycle_event": event_type, "native_status": status,
            "lifecycle_events": [event_type],
            "exit_code_status": "not_applicable", "exit_code": None,
            "path": None, "path_status": "missing", "change_kind": "unknown",
        }]
    actions = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            actions.append({
                "tool": "file_change", "native_item_id": item_id,
                "native_item_id_sha256": item_id_hash, "event_sequence": sequence,
                "completion_sequence": sequence if event_type in {"item.completed", "item.failed"} else None,
                "change_index": index, "lifecycle_event": event_type,
                "lifecycle_events": [event_type], "native_status": status,
                "path": None, "event_timestamp_epoch": None,
                "exit_code_status": "not_applicable", "exit_code": None,
                "path_status": "missing", "change_kind": "unknown",
            })
            continue
        raw_path = change.get("path")
        path = None
        path_status = "missing"
        if isinstance(raw_path, str):
            candidate = Path(raw_path)
            if "\\" in raw_path or ".." in candidate.parts:
                path_status = "outside_workspace"
            else:
                resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
                if resolved.is_relative_to(workspace.resolve()):
                    path = resolved.relative_to(workspace.resolve()).as_posix()
                    path_status = "workspace_relative"
                else:
                    path_status = "outside_workspace"
        raw_kind = change.get("kind")
        kind = raw_kind.casefold() if isinstance(raw_kind, str) and raw_kind.casefold() in {
            "add", "create", "update", "modify", "delete", "remove"
        } else "unknown"
        actions.append({
            "tool": "file_change", "native_item_id": item_id,
            "native_item_id_sha256": item_id_hash, "event_sequence": sequence,
            "completion_sequence": sequence if event_type in {"item.completed", "item.failed"} else None,
            "change_index": index, "lifecycle_event": event_type,
            "lifecycle_events": [event_type],
            "native_status": status, "path": path, "event_timestamp_epoch": None,
            "exit_code_status": "not_applicable", "exit_code": None,
            "path_status": path_status,
            "change_kind": kind,
        })
    return actions


def _file_change_signature(records: list[dict]) -> tuple:
    return tuple(sorted(
        (
            record.get("change_index"), record.get("path"),
            record.get("path_status"), record.get("change_kind"),
            record.get("native_status"),
        )
        for record in records
    ))


def _correlate_codex_file_changes(groups: dict[tuple[str, str], list[dict]]) -> list[dict]:
    """Collapse file-change lifecycle updates to one action per item/change index."""
    actions = []
    for snapshots in groups.values():
        snapshots.sort(key=lambda item: item["sequence"])
        first = snapshots[0]
        terminals = [
            item for item in snapshots
            if item["event_type"] in {"item.completed", "item.failed"}
        ]
        lifecycle_events = sorted({item["event_type"] for item in snapshots})
        signatures = {
            (item["event_type"], _file_change_signature(item["records"]))
            for item in terminals
        }
        ambiguous = len(signatures) > 1
        if terminals and not ambiguous:
            selected = min(terminals, key=lambda item: item["sequence"])
            records = selected["records"]
            terminal_sequence = selected["sequence"]
            terminal_event = selected["event_type"]
            native_status = selected["native_status"]
        elif terminals:
            selected = terminals[-1]
            indexed: dict[int | None, list[dict | None]] = {}
            for terminal in terminals:
                if terminal["records"]:
                    for record in terminal["records"]:
                        indexed.setdefault(record.get("change_index"), []).append(record)
                else:
                    indexed.setdefault(None, []).append(None)
            records = []
            for index, observations in sorted(
                indexed.items(), key=lambda item: (-1 if item[0] is None else item[0]),
            ):
                present = [item for item in observations if isinstance(item, dict)]
                complete_index = len(present) == len(terminals)
                signatures_for_index = {
                    (
                        item.get("path"), item.get("path_status"), item.get("change_kind"),
                        item.get("native_status"),
                    )
                    for item in present
                }
                same_path = complete_index and len(signatures_for_index) == 1
                base = present[0] if present else {
                    "tool": "file_change", "native_item_id": first["native_item_id"],
                    "native_item_id_sha256": first["native_item_id_sha256"],
                    "change_index": index, "path": None, "path_status": "missing",
                    "change_kind": "unknown", "native_status": "unknown",
                }
                records.append({
                    **base,
                    "path": base.get("path") if same_path else None,
                    "path_status": base.get("path_status") if same_path else "conflicting_lifecycle_path",
                    "native_status": "unknown",
                    "change_kind": base.get("change_kind") if same_path else "unknown",
                })
            if not records:
                records = [dict(selected["records"][0])] if selected["records"] else [{
                    "tool": "file_change", "native_item_id": first["native_item_id"],
                    "native_item_id_sha256": first["native_item_id_sha256"],
                    "change_index": None, "path": None, "path_status": "missing",
                    "change_kind": "unknown", "native_status": "unknown",
                }]
            terminal_sequence = None
            terminal_event = "ambiguous_terminal"
            native_status = "unknown"
        else:
            selected = snapshots[-1]
            records = selected["records"]
            terminal_sequence = None
            terminal_event = selected["event_type"]
            native_status = selected["native_status"]

        identity_missing = not isinstance(first.get("native_item_id_sha256"), str)
        for record in records:
            actions.append({
                **record,
                "event_sequence": first["sequence"],
                "completion_sequence": terminal_sequence,
                "lifecycle_event": terminal_event,
                "lifecycle_events": lifecycle_events,
                "native_status": native_status if ambiguous else record.get("native_status", native_status),
                "correlation_conflict": identity_missing or ambiguous,
                "lifecycle_conflict": ambiguous,
                "terminal_event_count": len(terminals),
            })
    return actions


def normalize_codex(raw: bytes, workspace: Path, repo: Path) -> dict:
    """Normalize Codex command/file items and non-executable todo metadata."""
    events, complete, invalid = parse_jsonl(raw)
    grouped: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    file_change_groups: dict[tuple[str, str], list[dict]] = {}
    planning_metadata: list[dict] = []
    unclassified = 0
    clarification_ids: set[str] = set()
    clarifications_complete = True
    for sequence, event in enumerate(events):
        event_type = event.get("type")
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(event_type, str) or not event_type.startswith("item."):
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str):
            unclassified += 1
            continue
        if item_type in {"question", "user_input"}:
            item_hash = _codex_id(item)[1]
            if item_hash is None:
                clarifications_complete = False
            else:
                clarification_ids.add(item_hash)
        if item_type == "todo_list":
            if _valid_codex_todo_list(item, event_type):
                _item_id, item_id_hash = _codex_id(item)
                planning_metadata.append({
                    "classification": "planning_metadata",
                    "native_item_id_sha256": item_id_hash,
                    "event_sequence": sequence,
                    "lifecycle_event": event_type,
                })
            else:
                unclassified += 1
            continue
        if item_type == "file_change":
            item_id, item_id_hash = _codex_id(item)
            identity_key = item_id_hash if item_id_hash is not None else f"event-{sequence}"
            file_key = (identity_key, "file_change")
            file_change_groups.setdefault(file_key, []).append({
                "sequence": sequence,
                "event_type": event_type,
                "native_item_id": item_id,
                "native_item_id_sha256": item_id_hash,
                "native_status": _native_status(item),
                "records": _codex_file_changes(item, event_type, sequence, workspace),
            })
            continue
        if item_type != "command_execution":
            if item_type not in {"agent_message", "reasoning", "user_input", "question"}:
                unclassified += 1
            continue
        item_id, item_id_hash = _codex_id(item)
        key = (item_id or f"event-{sequence}", "command_execution")
        command = item.get("command")
        command = command if isinstance(command, str) else None
        action = grouped.get(key)
        if action is None:
            action = {
                "tool": "command_execution", "native_item_id": item_id,
                "native_item_id_sha256": item_id_hash,
                "event_sequence": sequence, "completion_sequence": None,
                "command_value": command, "command_conflict": False,
                "exit_code_status": "missing", "exit_code": None,
                "native_status": "missing", "lifecycle_events": [],
                "terminal_outcomes": [], "output_values": [],
            }
            grouped[key] = action
            order.append(key)
        elif command is not None and action["command_value"] not in {None, command}:
            action["command_conflict"] = True
        elif command is not None:
            action["command_value"] = command
        action["lifecycle_events"].append(event_type)
        if "aggregated_output" in item:
            action["output_values"].append((sequence, event_type, item.get("aggregated_output")))
        explicit_status = _native_status(item)
        if explicit_status != "missing":
            action["native_status"] = explicit_status
        if event_type in {"item.completed", "item.failed"}:
            action["completion_sequence"] = sequence
            exit_status, exit_code = _explicit_exit(item)
            action["terminal_outcomes"].append((event_type, exit_status, exit_code))

    actions = []
    for key in order:
        item = grouped[key]
        original = item.pop("command_value")
        safe = _safe_text(original, workspace, repo) if isinstance(original, str) else None
        if item.pop("command_conflict"):
            safe = None
        terminal_outcomes = item.pop("terminal_outcomes")
        if len(terminal_outcomes) == 1:
            _event_type, item["exit_code_status"], item["exit_code"] = terminal_outcomes[0]
        elif terminal_outcomes:
            item["exit_code_status"], item["exit_code"] = "unknown", None
        output_values = item.pop("output_values")
        terminal_outputs = [
            value for _sequence, event_type, value in output_values
            if event_type in {"item.completed", "item.failed"}
        ]
        output_type = "missing"
        output_hash_status = "missing"
        output_bytes = None
        output_sha256 = None
        output_names_status = "unknown"
        output_result_type = "unknown"
        if terminal_outputs:
            if len(terminal_outputs) != len(terminal_outcomes) or any(
                value != terminal_outputs[0] for value in terminal_outputs[1:]
            ):
                output_type = "unknown"
                output_hash_status = "unknown"
            elif isinstance(terminal_outputs[0], str):
                output_type = "text"
                safe_output = _safe_text(terminal_outputs[0], workspace, repo)
                try:
                    output_encoded = safe_output.encode("utf-8")
                except UnicodeEncodeError:
                    output_type = "unknown"
                    output_hash_status = "unknown"
                else:
                    output_bytes = len(output_encoded)
                    if output_bytes > OUTPUT_HASH_BYTE_LIMIT:
                        output_hash_status = "too_large"
                    else:
                        output_hash_status = "observed"
                        output_sha256 = hashlib.sha256(output_encoded).hexdigest()
                    output_names_status = (
                        "observed" if _output_lists_names(terminal_outputs[0]) else "unknown"
                    )
                    output_result_type = "text"
            elif isinstance(terminal_outputs[0], (dict, list)):
                output_type = "structured"
                output_hash_status = "unknown"
                output_result_type = "structured"
            else:
                output_type = "unknown"
                output_hash_status = "unknown"
        try:
            encoded = safe.encode("utf-8") if safe is not None else None
        except UnicodeEncodeError:
            safe = None
            encoded = None
        private_path = False
        if safe is not None:
            masked = ALLOWED_WRAPPER_PATH_RE.sub("", safe)
            private_path = bool(ABSOLUTE_PATH_RE.search(masked) or WINDOWS_ABSOLUTE_PATH_RE.search(safe))
        # The authoritative parser input is either the whole safe command or absent;
        # a preview and an inner-shell normalization never replace this evidence.
        command_truncated = (
            len(encoded) > MAX_COMMAND_BYTES or private_path if encoded is not None else None
        )
        bounded_command = safe if encoded is not None and command_truncated is False else None
        if bounded_command is not None:
            normalized, normalization = normalize_command(bounded_command, "codex")
        elif safe is None:
            normalized, normalization = None, "missing"
        elif private_path:
            normalized, normalization = None, "private-path-redacted"
        else:
            normalized, normalization = None, "command-exceeds-shared-byte-bound"
        item.update({
            "command": bounded_command,
            "command_preview": safe[:COMMAND_PREVIEW_LIMIT] if safe is not None and not private_path else None,
            "command_sha256": hashlib.sha256(encoded).hexdigest() if encoded is not None else None,
            "command_bytes": len(encoded) if encoded is not None else None,
            "command_truncated": command_truncated,
            "command_redaction_status": "private_path" if private_path else "none" if safe is not None else "unknown",
            "normalized_command": normalized
            if normalized is not None and len(normalized.encode("utf-8")) <= NORMALIZED_COMMAND_LIMIT
            else None,
            "normalization": normalization,
            "outcome": {
                "native_tool_status": item.get("native_status", "unknown"),
                "output_hash_status": output_hash_status,
                "output_bytes": output_bytes,
                "output_sha256": output_sha256,
                "native_output_type": output_type,
                "shell_exit_code_status": item.get("exit_code_status", "missing"),
                **({"shell_exit_code": item["exit_code"]} if item.get("exit_code_status") == "observed" else {}),
            },
            "native_output_names_status": output_names_status,
            "native_output_result_type": output_result_type,
        })
        if _ls_shell_segment(item.get("command"), "codex") is not None:
            outcome = item["outcome"]
            exit_code = outcome.get("shell_exit_code")
            observed_listing = (
                output_type == "text"
                and output_hash_status == "observed"
                and item.get("native_status") == "completed"
                and item.get("exit_code_status") == "observed"
                and type(exit_code) is int
                and exit_code == 0
                and len(terminal_outputs) == 1
                and _shell_ls_output_lists_names(
                    item, "codex", terminal_outputs[0], workspace,
                )
            )
            item["native_output_names_status"] = "observed" if observed_listing else "unknown"
            if observed_listing:
                item["native_output_result_type"] = "directory_listing"
        item["lifecycle_events"] = sorted(set(item["lifecycle_events"]))
        sequence = item.get("event_sequence")
        if type(sequence) is int and sequence < len(events):
            item["event_timestamp_epoch"] = _event_epoch(events[sequence])
        actions.append(item)
    file_actions = _correlate_codex_file_changes(file_change_groups)
    for action in file_actions:
        sequence = action.get("event_sequence")
        if type(sequence) is int and sequence < len(events):
            action["event_timestamp_epoch"] = _event_epoch(events[sequence])
        completion = action.get("completion_sequence")
        if type(completion) is int and completion < len(events):
            action["completion_timestamp_epoch"] = _event_epoch(events[completion])
    actions.extend(file_actions)
    session = _session_id(events, "codex")
    return {
        "actions": actions,
        "planning_metadata": planning_metadata,
        "events_observed": len(events),
        "invalid_lines": invalid,
        "unclassified_item_count": unclassified,
        "clarification_observations": len(clarification_ids) if clarifications_complete else None,
        "trace_complete": complete and bool(events) and unclassified == 0,
        "session_id_sha256": hashlib.sha256(session.encode()).hexdigest() if session else None,
    }
