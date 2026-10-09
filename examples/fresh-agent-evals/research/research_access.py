"""Bounded subject-action access audit for the public research fixture."""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath

from commands import (
    classify_shell_segment,
    command_segments,
    command_semantic_key,
    filename_discovery_scope,
    is_completion_checker_segment,
    start_here_task,
)
from evidence import command_sha256_matches, normalize_access_observations
from native_arguments import validate_native_arguments

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from onboarding.completion import (  # noqa: E402
    load_fixture_contract,
    render_finish_commands,
    series_directory,
    validate_series_manifest_path,
)

RESEARCH_REL = "examples/fresh-agent-evals/research"
CASE_ID = "paper-backed-coverage-challenger"
BLOCKED_SUBJECT_PATHS = {
    f"{RESEARCH_REL}/fixture.json",
    f"{RESEARCH_REL}/research_access.py",
    f"{RESEARCH_REL}/research_grader.py",
    f"{RESEARCH_REL}/research_selfcheck.py",
    f"{RESEARCH_REL}/runner.py",
    f"{RESEARCH_REL}/synthetic_evaluator.py",
    f"{RESEARCH_REL}/typed_schemas.py",
    f"{RESEARCH_REL}/series.py",
    f"{RESEARCH_REL}/callback_worker.py",
    "examples/fresh-agent-evals/v2/grader.py",
}
BLOCKED_BASENAMES = {PurePosixPath(path).name for path in BLOCKED_SUBJECT_PATHS}
HASH_TOOLS = {"sha256sum", "shasum"}
READ_TOOLS = {"cat", "head", "tail", "sed", "grep", "rg", "ls"}
PATH_TOOLS = {"read", "list", "glob", "grep"}
EDIT_TOOLS = {"edit", "write", "apply_patch"}
PYTHON_EXECUTABLE_RE = re.compile(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?\Z", re.IGNORECASE)
SED_ADDRESS_RE = re.compile(r"(?:\d+|\$)(?:,(?:\d+|\$))?[pP]?\Z")


def _compact_text(value: str) -> str:
    text = value.casefold().replace("\\", "/").replace("'", "").replace('"', "")
    text = re.sub(r"/+", "/", text)
    return re.sub(r"/\./", "/", text)


def _forbidden_source_reference(value: str, *, allow_runner: bool = False) -> str | None:
    compact = _compact_text(value)
    for path in sorted(BLOCKED_SUBJECT_PATHS):
        basename = PurePosixPath(path).name
        if allow_runner and path == f"{RESEARCH_REL}/runner.py":
            continue
        if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(basename)}(?![A-Za-z0-9_.-])", compact):
            return path
    return None


def _workspace_relative(value: str, workspace: Path) -> str | None:
    text = value.strip().strip("\"'").replace("\\", "/")
    if not text:
        return None
    workspace_prefix = str(workspace.resolve()).replace("\\", "/")
    if text == "<WORKSPACE>":
        return None
    if text.startswith("<WORKSPACE>/"):
        text = text[len("<WORKSPACE>/"):]
    elif text.startswith(workspace_prefix + "/"):
        text = text[len(workspace_prefix) + 1:]
    elif text.startswith("<REPO>/") or text.startswith("<HOME>/") or text.startswith("/"):
        return None
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    return None if normalized in {"", "."} else normalized


def _field_strings(action: dict) -> list[str]:
    return [
        value for key in ("command", "path", "file_path", "filePath", "normalized_command", "raw_command")
        if isinstance((value := action.get(key)), str)
    ]


def _protected_reference_values(action: dict, harness: str, workspace: Path) -> list[str]:
    tool = action.get("tool")
    if tool not in {"bash", "shell", "command_execution"}:
        return _field_strings(action)
    command = action.get("command")
    if (
        not isinstance(command, str)
        or action.get("command_truncated") is not False
        or not command_sha256_matches(action)
    ):
        return _field_strings(action)
    segments = command_segments(command, harness)
    if not segments:
        return _field_strings(action)
    values = [
        value for key in ("path", "file_path", "filePath")
        if isinstance((value := action.get(key)), str)
    ]
    for segment in segments:
        # A bounded filename-discovery segment has polarity-aware normalized
        # scope. Its raw tokens are not content-access evidence; positive
        # protected discovery remains visible in the access observation.
        if filename_discovery_scope(segment, workspace) is not None:
            continue
        values.extend((segment.source, *segment.argv))
    return values


def _is_exact_development_action(
    action: dict, harness: str, expected_key: tuple | None,
) -> bool:
    command = action.get("command")
    return (
        expected_key is not None
        and isinstance(command, str)
        and action.get("command_truncated") is False
        and command_sha256_matches(action)
        and command_semantic_key(command, harness) == expected_key
    )


def _path_from_action(action: dict, workspace: Path) -> str | None:
    values = []
    invalid = False
    for key in ("command", "path", "file_path", "filePath"):
        value = action.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            invalid = True
            continue
        relative = _workspace_relative(value, workspace)
        if relative is None:
            invalid = True
        else:
            values.append(relative)
    if invalid or not values or len(set(values)) != 1:
        return None
    return values[0]


def _many_file_operands(words: list[str], workspace: Path, *, allow_many: bool = True) -> list[str] | None:
    if not words or len(words) > (8 if allow_many else 1) or any(item == "-" for item in words):
        return None
    result = [_workspace_relative(item, workspace) for item in words]
    return [item for item in result if item is not None] if all(item is not None for item in result) else None


def _reader_operands(argv: tuple[str, ...], workspace: Path) -> list[str] | None:
    if not argv:
        return None
    tool = Path(argv[0]).name.casefold()
    args = list(argv[1:])
    if tool == "cat":
        if args[:1] == ["--"]:
            args = args[1:]
        if any(item.startswith("-") for item in args):
            return None
        return _many_file_operands(args, workspace)
    if tool in {"head", "tail"}:
        index = 0
        count = None
        if args[:1] in (["-n"], ["--lines"]):
            if len(args) < 2 or not args[1].isdigit():
                return None
            count = int(args[1])
            index = 2
        elif args and (match := re.fullmatch(r"(?:-n|--lines=)(\d+)", args[0])):
            count = int(match.group(1))
            index = 1
        elif args and (match := re.fullmatch(r"-(\d+)", args[0])):
            count = int(match.group(1))
            index = 1
        if count is not None and not 1 <= count <= 200:
            return None
        if args[index:index + 1] == ["--"]:
            index += 1
        return _many_file_operands(args[index:], workspace, allow_many=False)
    if tool == "sed":
        scripts = []
        index = 0
        while index < len(args):
            token = args[index]
            if token in {"-n", "--quiet", "--silent"}:
                index += 1
            elif token in {"-e", "--expression"}:
                if index + 1 >= len(args):
                    return None
                scripts.append(args[index + 1])
                index += 2
                break
            elif token.startswith("-"):
                return None
            else:
                scripts.append(token)
                index += 1
                break
        if len(scripts) != 1 or SED_ADDRESS_RE.fullmatch(scripts[0]) is None:
            return None
        if args[index:index + 1] == ["--"]:
            index += 1
        return _many_file_operands(args[index:], workspace)
    if tool in {"grep", "rg"}:
        flags = {"-n", "-i", "-F", "-E", "--line-number", "--ignore-case", "--fixed-strings", "--extended-regexp"}
        pattern = None
        index = 0
        while index < len(args):
            token = args[index]
            if token in flags:
                index += 1
            elif token == "--":
                if pattern is None:
                    if index + 1 >= len(args):
                        return None
                    pattern = args[index + 1]
                    index += 2
                else:
                    index += 1
                break
            elif token in {"-e", "--regexp"}:
                if pattern is not None or index + 1 >= len(args):
                    return None
                pattern = args[index + 1]
                index += 2
            elif token.startswith("--regexp="):
                if pattern is not None:
                    return None
                pattern = token.split("=", 1)[1]
                index += 1
            elif token.startswith("-e") and len(token) > 2:
                if pattern is not None:
                    return None
                pattern = token[2:]
                index += 1
            elif token.startswith("-"):
                return None
            elif pattern is None:
                pattern = token
                index += 1
            else:
                break
        if pattern is None:
            return None
        return _many_file_operands(args[index:], workspace)
    return None


def _hash_operands(argv: tuple[str, ...], workspace: Path) -> list[str] | None:
    if not argv:
        return None
    tool = Path(argv[0]).name.casefold()
    args = list(argv[1:])
    if tool == "sha256sum":
        if args[:1] == ["-b"]:
            args = args[1:]
    elif tool == "shasum":
        if args[:2] != ["-a", "256"]:
            return None
        args = args[2:]
    else:
        return None
    if args[:1] == ["--"]:
        args = args[1:]
    if any(item.startswith("-") for item in args):
        return None
    return _many_file_operands(args, workspace)


def _trace_is_complete(actions: list, harness: str, trace: dict | None) -> bool:
    if not isinstance(trace, dict) or trace.get("actions") != actions:
        return False
    if trace.get("trace_complete") is not True:
        return False
    if type(trace.get("events_observed")) is not int or trace["events_observed"] < 1:
        return False
    if type(trace.get("invalid_lines")) is not int or trace["invalid_lines"] != 0:
        return False
    counter = "unclassified_tool_count" if harness == "opencode" else "unclassified_item_count"
    return type(trace.get(counter)) is int and trace[counter] == 0 and bool(actions)


def research_action_access_problems(
    actions: list,
    harness: str,
    workspace: Path,
    series_manifest_path: str,
    expected_development_command: str,
    trace: dict | None,
    *,
    causal_actions: dict[str, object] | None = None,
) -> list[str]:
    """Audit only normalized subject actions; evaluator-owned reads are not inputs here."""
    problems: list[str] = []

    def action_problem(message: str, action_index: int, action: dict) -> None:
        reason = f"{message} (action_index={action_index})" if causal_actions is not None else message
        problems.append(reason)
        if causal_actions is not None:
            causal_actions[reason] = action

    if harness not in {"opencode", "codex"} or not isinstance(actions, list):
        return ["native subject access audit is unknown: harness or action list is unsupported"]
    if not _trace_is_complete(actions, harness, trace):
        problems.append("native subject access audit is unknown: the normalized action trace is incomplete or unsupported")
    try:
        contract = load_fixture_contract(REPO_ROOT, "research", CASE_ID)
        deliverables = set(contract["deliverables"])
        scratch_paths = set(contract["scratch_paths"])
        code_rel = next(path for path in deliverables if PurePosixPath(path).name == "challenger.py")
        report_rel = next(path for path in deliverables if PurePosixPath(path).name == "comparison-report.json")
        result_rel = next(path for path in deliverables if PurePosixPath(path).name == "research-result.json")
        readable_paths = set(contract["available_input_paths"])
    except (OSError, ValueError, KeyError, StopIteration):
        return problems + ["native subject access audit is unknown: canonical fixture contract is unavailable or malformed"]
    normalized_access = normalize_access_observations(actions, harness, workspace)
    access_by_index = {}
    for item in normalized_access:
        if type(item.get("action_index")) is int:
            access_by_index.setdefault(item["action_index"], item)

    manifest_relative = _workspace_relative(series_manifest_path, workspace)
    try:
        manifest_relative = validate_series_manifest_path("research", manifest_relative) if manifest_relative else None
    except ValueError:
        manifest_relative = None
    expected_command = next(
        item["command"] for item in render_finish_commands(contract, "research", manifest_relative)
        if item["check_id"] == "research-development-evaluation"
    ) if manifest_relative else None
    series_is_selected = manifest_relative is not None and expected_development_command == expected_command
    expected_key = command_semantic_key(expected_command, harness) if series_is_selected and expected_command else None
    command_is_exact = {
        id(action): _is_exact_development_action(action, harness, expected_key)
        for action in actions if isinstance(action, dict)
    }
    if series_is_selected:
        readable_paths.add(manifest_relative)
    subject_readable_paths = readable_paths | deliverables | scratch_paths
    hashable_paths = readable_paths | deliverables
    finish_commands = {
        item["check_id"]: item["command"]
        for item in render_finish_commands(contract, "research", manifest_relative)
    } if manifest_relative else {}
    expected_diff_key = command_semantic_key(finish_commands.get("research-git-diff-check", ""), harness)
    expected_completion_key = command_semantic_key(finish_commands.get("research-completion-check", ""), harness)

    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            problems.append("native subject access audit is unknown: malformed action observation")
            continue
        tool = action.get("tool")
        exact_development_action = command_is_exact.get(id(action), False)
        filename_discovery = tool in {"list", "glob"}
        if not filename_discovery:
            for value in _protected_reference_values(action, harness, workspace):
                blocked = _forbidden_source_reference(value, allow_runner=exact_development_action)
                if blocked is not None:
                    action_problem(
                        "reserved fixture or protected grader source access was observed",
                        action_index, action,
                    )
                    break

        if tool in {"bash", "shell", "command_execution"}:
            command = action.get("command")
            if (
                not isinstance(command, str)
                or action.get("command_truncated") is not False
                or not command_sha256_matches(action)
            ):
                action_problem(
                    "native subject access audit is unknown: shell command text or digest is incomplete",
                    action_index, action,
                )
                continue
            segments = command_segments(command, harness)
            if not segments:
                action_problem(
                    "native subject access audit is unknown: shell command is outside the bounded grammar",
                    action_index, action,
                )
                continue
            if exact_development_action:
                continue
            for segment in segments:
                if not segment.argv:
                    action_problem(
                        "native subject access audit is unknown: shell segment has no parsed argv",
                        action_index, action,
                    )
                    continue
                argv = segment.argv
                discovery_scope = filename_discovery_scope(segment, workspace)
                if discovery_scope is not None:
                    continue
                if any(_forbidden_source_reference(value) is not None for value in (segment.source, *argv)):
                    action_problem(
                        "reserved fixture or protected grader source access was observed",
                        action_index, action,
                    )
                tool_name = Path(argv[0]).name.casefold()
                if tool_name == "git":
                    key = command_semantic_key(segment.source, harness)
                    if key == expected_diff_key:
                        continue
                    action_problem(
                        "native subject access audit is unknown: git command is outside the fixed completion grammar",
                        action_index, action,
                    )
                    continue
                if PYTHON_EXECUTABLE_RE.fullmatch(tool_name):
                    if is_completion_checker_segment(segment):
                        if command_semantic_key(segment.source, harness) == expected_completion_key:
                            continue
                        action_problem(
                            "native subject access audit is unknown: completion command differs from the fixed fixture grammar",
                            action_index, action,
                        )
                        continue
                    if start_here_task(segment) == "research":
                        continue
                    action_problem(
                        "native subject access audit is unknown: arbitrary interpreter execution is unsupported",
                        action_index, action,
                    )
                    continue
                if tool_name in READ_TOOLS:
                    operands = _reader_operands(argv, workspace)
                    if operands is None or any(path not in subject_readable_paths for path in operands):
                        action_problem(
                            "native subject access audit is unknown: shell inspection is outside the bounded read allowlist",
                            action_index, action,
                        )
                    continue
                if tool_name in HASH_TOOLS:
                    operands = _hash_operands(argv, workspace)
                    if operands is None or any(path not in hashable_paths for path in operands):
                        action_problem(
                            "native subject access audit is unknown: shell hash operands are outside the bounded selected-source allowlist",
                            action_index, action,
                        )
                    continue
                if tool_name == "pwd" and len(argv) == 1:
                    continue
                if tool_name in {"true", "false"} and len(argv) == 1:
                    continue
                action_problem(
                    "native subject access audit is unknown: shell segment is outside the bounded allowlist",
                    action_index, action,
                )
            continue

        if tool in PATH_TOOLS:
            arguments, argument_problem = validate_native_arguments(action, workspace)
            if (
                arguments is None
                or argument_problem is not None
                or action.get("command_truncated") is not False
                or not command_sha256_matches(action)
            ):
                action_problem(
                    "native subject access audit is unknown: native file-tool arguments or completion are incomplete",
                    action_index, action,
                )
            elif tool == "read":
                access = access_by_index.get(action_index)
                operation = access.get("operation") if isinstance(access, dict) else "unknown"
                if operation == "unknown":
                    action_problem(
                        "native subject access audit is unknown: native read target/result type is not evidenced",
                        action_index, action,
                    )
                elif operation == "file_content_read" and arguments.get("path") not in subject_readable_paths:
                    action_problem(
                        "native content read is outside the documented public-input/output allowlist",
                        action_index, action,
                    )
            elif tool == "grep" and arguments.get("path") not in subject_readable_paths:
                action_problem(
                    "native content search is outside the documented public-input/output allowlist",
                    action_index, action,
                )
            # list/glob are explicitly filename-only discovery. Their complete,
            # bounded argument evidence is required, but discovery does not
            # grant or claim access to protected file contents.
            continue

        if tool in EDIT_TOOLS:
            path = _path_from_action(action, workspace)
            if (
                path is None
                or action.get("command_truncated") is not False
                or not command_sha256_matches(action)
            ):
                action_problem(
                    "native subject access audit is unknown: native edit path is incomplete",
                    action_index, action,
                )
            elif path not in {code_rel, result_rel, *scratch_paths}:
                action_problem(
                    "native file edit is outside the declared callback and research-artifact outputs",
                    action_index, action,
                )
            continue

        if tool == "file_change":
            path = action.get("path")
            identity = action.get("native_item_id_sha256")
            allowed_changes = {code_rel, result_rel, *scratch_paths}
            if any(command_is_exact.values()):
                allowed_changes.add(report_rel)
            if (
                action.get("path_status") != "workspace_relative"
                or not isinstance(path, str)
                or not isinstance(action.get("native_item_id"), str)
                or not isinstance(identity, str)
                or re.fullmatch(r"[0-9a-f]{64}", identity) is None
                or type(action.get("change_index")) is not int
                or action.get("change_index") < 0
                or type(action.get("event_sequence")) is not int
                or type(action.get("completion_sequence")) is not int
                or action.get("completion_sequence") < action.get("event_sequence")
                or action.get("correlation_conflict") is not False
                or action.get("lifecycle_conflict") is not False
            ):
                action_problem(
                    "native subject access audit is unknown: native file-change identity, index, lifecycle, or path is incomplete",
                    action_index, action,
                )
            elif path not in allowed_changes:
                action_problem(
                    "native file change is outside the declared contribution outputs",
                    action_index, action,
                )
            continue

        if tool == "todowrite":
            continue
        action_problem(
            "native subject access audit is unknown: native action type is not audited",
            action_index, action,
        )

    return sorted(set(problems))
