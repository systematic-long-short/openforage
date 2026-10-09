"""Trace-only evidence predicates shared by the v2 grader and runner."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from commands import (
    classify_shell_segment,
    command_semantic_key,
    command_segments,
    direct_suite_access,
    filename_discovery_scope,
    is_completion_checker_segment,
    is_completion_json_segment,
    start_here_task,
    validator_modes,
    validator_segment_modes,
)
from native_arguments import (
    NATIVE_ARGUMENT_TOOLS,
    native_read_covers_source,
    native_read_output_frame,
    validate_native_arguments,
)
from shell_parser import parse_shell_command

CALL_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ROUTE_SOURCES = {
    "README.md", "AGENTS.md", "CLAUDE.md", "docs/agents/first-contribution.md",
    "docs/agents/start-here.md", "docs/agents/references/onboarding.json",
}
ROOT_ENTRIES = {"README.md", "AGENTS.md"}
TASK_SOURCES = {"docs/agents/first-contribution.md", "docs/agents/start-here.md", "docs/agents/references/onboarding.json"}
ACCESS_OBSERVATION_VERSION = 1
ACCESS_OPERATIONS = {
    "file_content_read", "content_search", "filename_discovery",
    "directory_listing", "write", "unknown",
}


def action_sequence(action: dict) -> int | None:
    value = action.get("event_sequence")
    if type(value) is int:
        return value
    value = action.get("completion_sequence")
    return value if type(value) is int else None


def result_sequence(action: dict) -> int | None:
    value = action.get("completion_sequence")
    return value if type(value) is int else action_sequence(action)


def command_text(action: dict, harness: str) -> str | None:
    if action.get("command_truncated") is not False:
        return None
    value = action.get("command")
    return value if isinstance(value, str) else None


def explicit_exit_code(action: dict, harness: str) -> int | None:
    if harness == "opencode":
        if action.get("tool") not in {"bash", "shell"}:
            return None
        if action.get("correlation_conflict", False) is not False:
            return None
        if not isinstance(action.get("native_tool_call_id_sha256"), str) or not CALL_HASH_RE.fullmatch(action["native_tool_call_id_sha256"]):
            return None
        start = action.get("event_sequence")
        completed = action.get("completion_sequence")
        if type(start) is not int or type(completed) is not int or completed < start:
            return None
        if type(action.get("outcome_schema_version")) is not int or action["outcome_schema_version"] != 1:
            return None
        outcome = action.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("shell_exit_code_status") != "observed":
            return None
        if outcome.get("native_tool_status") not in {"completed", "error"}:
            return None
        value = outcome.get("shell_exit_code")
        return value if type(value) is int else None
    if harness == "codex":
        if action.get("tool") != "command_execution" or action.get("exit_code_status") != "observed":
            return None
        if action.get("native_status") not in {"completed", "failed", "error"}:
            return None
        native_item_id = action.get("native_item_id")
        native_item_hash = action.get("native_item_id_sha256")
        if (
            not isinstance(native_item_id, str)
            or not isinstance(native_item_hash, str)
            or not CALL_HASH_RE.fullmatch(native_item_hash)
            or hashlib.sha256(native_item_id.encode("utf-8")).hexdigest() != native_item_hash
        ):
            return None
        start = action.get("event_sequence")
        completed = action.get("completion_sequence")
        lifecycle = action.get("lifecycle_events")
        if (
            type(start) is not int
            or type(completed) is not int
            or completed < start
            or not isinstance(lifecycle, list)
            or "item.completed" not in lifecycle
        ):
            return None
        if "item.failed" in lifecycle or action.get("native_status") in {"denied", "cancelled", "timeout"}:
            return None
        value = action.get("exit_code")
        return value if type(value) is int else None
    return None


def validator_observations(actions: list, harness: str, workspace: Path) -> list[dict]:
    results = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        text = command_text(action, harness)
        if text is None:
            continue
        parsed = parse_shell_command(text, harness)
        if parsed is None:
            continue
        modes, error = validator_modes(text, workspace, harness)
        if modes is None:
            continue
        validator_segments = []
        for index, segment in enumerate(parsed.segments):
            segment_modes, _segment_error = validator_segment_modes(segment, workspace)
            if segment_modes is not None:
                validator_segments.append({
                    "index": index,
                    "argv": list(segment.argv),
                    "modes": segment_modes,
                })
        if not validator_segments:
            continue
        segment_modes = [mode for item in validator_segments for mode in item["modes"]]
        if segment_modes != modes:
            continue
        command_digest_valid = command_sha256_matches(action)
        shell_exit = explicit_exit_code(action, harness) if command_digest_valid else None
        native_status = (
            action.get("outcome", {}).get("native_tool_status")
            if harness == "opencode" and isinstance(action.get("outcome"), dict)
            else action.get("native_status")
        )
        if not command_digest_valid:
            inferred_exit = None
            inference_basis = "native_full_command_digest_missing_or_mismatched"
        elif shell_exit == 0 and native_status != "completed":
            inferred_exit = None
            inference_basis = "native_zero_exit_lacks_completed_lifecycle"
        elif len(parsed.segments) == 1 and shell_exit in {126, 127}:
            inferred_exit = None
            inference_basis = f"standalone_shell_launch_failure_{shell_exit}"
        elif len(parsed.segments) == 1:
            inferred_exit = shell_exit
            inference_basis = (
                "standalone_native_shell_exit"
                if shell_exit is not None
                else "standalone_native_exit_missing_denied_or_conflicting"
            )
        elif shell_exit is None:
            inferred_exit = None
            inference_basis = "and_chain_native_exit_missing_denied_or_conflicting"
        elif shell_exit != 0:
            inferred_exit = None
            inference_basis = "and_chain_nonzero_exit_does_not_locate_validator_outcome"
        elif any(
            len(item.argv) == 1 and Path(item.argv[0]).name.casefold() == "false"
            for item in parsed.segments
        ):
            inferred_exit = None
            inference_basis = "and_chain_zero_exit_conflicts_with_literal_false_segment"
        else:
            inferred_exit = 0
            inference_basis = "explicit_native_exit_zero_for_fully_admitted_and_chain"
        results.append({
            "action": action,
            "modes": modes,
            "segment_index": validator_segments[0]["index"],
            "validator_segment_indices": [item["index"] for item in validator_segments],
            "validator_segments": validator_segments,
            "validator_segment_argv": validator_segments[0]["argv"],
            "exit_code": inferred_exit,
            "compound_shell_exit_code": shell_exit if len(parsed.segments) > 1 else None,
            "native_shell_exit_code": shell_exit,
            "inference_basis": inference_basis,
            "command_key": parsed.execution_key,
            "parse_error": error,
        })
    return results


def _safe_relative(value: str, workspace: Path) -> str | None:
    text = value.strip().strip("\"'")
    if text.startswith("<WORKSPACE>/"):
        text = text[len("<WORKSPACE>/"):]
    elif text.startswith(str(workspace.resolve()) + "/"):
        text = text[len(str(workspace.resolve()) + "/"):]
    elif text.startswith("/"):
        return None
    if text.startswith("./"):
        text = text[2:]
    path = Path(text)
    if not text or path.is_absolute() or "\\" in text or ".." in path.parts:
        return None
    return path.as_posix()


def _segment_read_operands(segment, workspace: Path) -> tuple[list[str], str, bool]:
    words = list(segment.argv)
    if not words:
        return [], "", False
    tool = Path(words[0]).name.casefold()
    args = words[1:]
    if tool in {"cat", "head", "tail", "less", "more"}:
        selected = [item for item in args if not item.startswith("-")]
    elif tool in {"grep", "rg", "egrep", "fgrep"}:
        nonoptions = [item for item in args if not item.startswith("-")]
        selected = nonoptions[1:]
    elif tool == "sed":
        selected, classified = _simple_sed_operands(args)
        if not classified:
            return [], tool, False
    else:
        selected = []
    operands = []
    for value in selected:
        item = _safe_relative(value, workspace)
        if item is None:
            return [], tool, False
        operands.append(item)
    return operands, tool, True


def _read_operands(command: str, harness: str, workspace: Path) -> tuple[set[str], bool]:
    parsed = parse_shell_command(command, harness)
    if parsed is None:
        return set(), False
    operands: set[str] = set()
    for segment in parsed.segments:
        if classify_shell_segment(segment, workspace) is None:
            return set(), False
        selected, _tool, classified = _segment_read_operands(segment, workspace)
        if not classified:
            return set(), False
        operands.update(selected)
    return operands, True


def _shell_read_observations(
    command: str, harness: str, workspace: Path,
) -> list[tuple[str, str, int, int]]:
    """Return ordered path/kind/segment/operand records for admitted shell reads."""
    parsed = parse_shell_command(command, harness)
    if parsed is None:
        return []
    observations = []
    for segment_index, segment in enumerate(parsed.segments):
        if classify_shell_segment(segment, workspace) is None:
            return []
        operands, tool, classified = _segment_read_operands(segment, workspace)
        if not classified:
            return []
        if tool == "cat":
            kind = "shell_content_read"
        elif tool in {"grep", "rg", "egrep", "fgrep"}:
            kind = "shell_content_search"
        elif tool in {"head", "tail", "sed", "less", "more"}:
            kind = "shell_partial_content_read"
        else:
            continue
        observations.extend(
            (path, kind, segment_index, operand_index)
            for operand_index, path in enumerate(operands)
        )
    return observations


def _segment_read_observations(segment, workspace: Path) -> list[tuple[str, str, int]]:
    selected, tool, classified = _segment_read_operands(segment, workspace)
    if not classified:
        return []
    if tool == "cat":
        kind = "shell_content_read"
    elif tool in {"grep", "rg", "egrep", "fgrep"}:
        kind = "shell_content_search"
    elif tool in {"head", "tail", "sed", "less", "more"}:
        kind = "shell_partial_content_read"
    else:
        return []
    return [(path, kind, operand_index) for operand_index, path in enumerate(selected)]


def _native_reference(action: dict, harness: str) -> dict:
    identity_key = "native_tool_call_id_sha256" if harness == "opencode" else "native_item_id_sha256"
    identity = action.get(identity_key)
    outcome = action.get("outcome") if isinstance(action.get("outcome"), dict) else {}
    return {
        "original_tool_type": action.get("native_tool_name", action.get("tool")),
        "identity_kind": identity_key,
        "identity_sha256": identity if isinstance(identity, str) and CALL_HASH_RE.fullmatch(identity) else None,
        "event_sequence": action_sequence(action),
        "completion_sequence": result_sequence(action),
        "original_outcome_status": outcome.get("native_tool_status", action.get("native_status", "unknown")),
        "original_output_type": outcome.get("native_output_type", "unknown"),
        "native_output_result_type": action.get("native_output_result_type", "unknown"),
        "native_output_event_sequence": action.get("native_output_event_sequence"),
        "native_output_identity_sha256": action.get("native_output_identity_sha256"),
    }


def _native_status(action: dict, harness: str) -> str:
    outcome = action.get("outcome") if isinstance(action.get("outcome"), dict) else {}
    raw = outcome.get("native_tool_status") if harness == "opencode" else action.get("native_status")
    if raw == "denied":
        return "denied"
    if raw in {"cancelled", "canceled", "skipped"}:
        return "cancelled"
    if raw in {"error", "failed", "timeout", "timed_out"}:
        return "failed"
    return "completed" if raw == "completed" else "unknown"


def _native_read_output_is_bound(action: dict) -> bool:
    identity = action.get("native_tool_call_id_sha256")
    output_identity = action.get("native_output_identity_sha256")
    argument_identity = action.get("native_arguments_identity_sha256")
    start = action_sequence(action)
    input_sequence = action.get("native_arguments_event_sequence")
    output_sequence = action.get("native_output_event_sequence")
    completion = result_sequence(action)
    return (
        isinstance(identity, str)
        and CALL_HASH_RE.fullmatch(identity) is not None
        and output_identity == identity
        and argument_identity == identity
        and action.get("correlation_conflict") is not True
        and type(start) is int
        and type(input_sequence) is int
        and type(output_sequence) is int
        and type(completion) is int
        and start <= input_sequence <= output_sequence <= completion
    )


def _shell_status(action: dict, harness: str, operation: str, segment_count: int) -> str:
    native = _native_status(action, harness)
    if native != "completed":
        return native
    code = explicit_exit_code(action, harness)
    if type(code) is not int:
        return "unknown"
    if code == 0:
        return "completed"
    if operation == "content_search" and code == 1:
        return "completed" if segment_count == 1 else "unknown"
    return "failed" if segment_count == 1 else "unknown"


def _output_evidence(
    action: dict,
    operation: str,
    outcome_status: str,
    harness: str,
    workspace: Path,
) -> str:
    if outcome_status != "completed":
        return "unknown"
    outcome = action.get("outcome") if isinstance(action.get("outcome"), dict) else {}
    output_type = outcome.get("native_output_type")
    output_bytes = outcome.get("output_bytes")
    output_hash_status = outcome.get("output_hash_status")
    if output_hash_status != "observed" or type(output_bytes) is not int or output_bytes < 0:
        return "unknown"
    if operation in {"filename_discovery", "directory_listing"}:
        if action.get("tool") == "read" and not _native_read_output_is_bound(action):
            return "unknown"
        if (
            action.get("tool") in {"bash", "shell", "command_execution"}
            and output_type != "text"
        ):
            return "unknown"
        if output_type not in {"text", "structured"}:
            return "unknown"
        if output_bytes == 0:
            return "empty_observed"
        return "names_observed" if action.get("native_output_names_status") == "observed" else "unknown"
    if operation == "file_content_read" and action.get("tool") == "read":
        if (
            not _native_read_output_is_bound(action)
            or action.get("native_output_result_type") != "file_content"
            or action.get("native_output_content_status") != "observed"
        ):
            return "unknown"
        content_bytes = action.get("native_output_content_bytes")
        if type(content_bytes) is not int or content_bytes < 0:
            return "unknown"
        return "empty_observed" if content_bytes == 0 else "content_observed"
    if output_type != "text":
        return "unknown"
    if output_bytes == 0:
        return "empty_observed"
    if operation in {"file_content_read", "content_search"}:
        return "content_observed"
    return "unknown"


def _scope(
    paths: list[str] | None = None,
    *,
    positive_globs: list[str] | None = None,
    excluded_globs: list[str] | None = None,
    pattern: str | None = None,
    content_range: str = "not_applicable",
    recursive: bool = False,
    directory_operands: bool = False,
) -> dict:
    digest = None
    if isinstance(pattern, str):
        try:
            digest = hashlib.sha256(pattern.encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            digest = None
    scope = {
        "safe_paths": list(paths or []),
        "positive_globs": list(positive_globs or []),
        "excluded_globs": list(excluded_globs or []),
        "pattern_sha256": digest,
        "content_range": content_range,
    }
    if recursive:
        scope["recursive"] = True
    if directory_operands:
        scope["directory_operands"] = True
    return scope


def _search_pattern(segment) -> str | None:
    words = list(segment.argv)
    if len(words) < 2:
        return None
    args = words[1:]
    value_options = {"-e", "--regexp"}
    boolean_options = {
        "-n", "-i", "-F", "-E", "-w", "-x", "-v", "--line-number",
        "--ignore-case", "--fixed-strings", "--extended-regexp", "--word-regexp",
        "--count", "--files-with-matches", "--no-heading", "-r", "-R", "--recursive",
    }
    pattern = None
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            if pattern is None and index < len(args):
                return args[index]
            return pattern
        if token in value_options:
            if pattern is not None or index + 1 >= len(args):
                return None
            pattern = args[index + 1]
            index += 2
        elif token.startswith("--regexp="):
            if pattern is not None:
                return None
            pattern = token.split("=", 1)[1]
            index += 1
        elif token.startswith("-e") and len(token) > 2 and pattern is None:
            pattern = token[2:]
            index += 1
        elif token in boolean_options:
            index += 1
        elif token.startswith("-"):
            return None
        elif pattern is None:
            pattern = token
            index += 1
        else:
            return pattern
    return pattern


def _permission_class(operation: str, scope: dict, harness: str, workspace: Path) -> str:
    if operation == "unknown" or not scope.get("safe_paths"):
        return "unknown"
    if operation in {"filename_discovery", "directory_listing"}:
        return "filename_only"
    if operation == "write":
        return "workspace_write"
    protected = any(
        direct_suite_access({"tool": "read", "path": path}, harness, workspace)
        for path in scope["safe_paths"]
    )
    return "protected_content" if protected else "workspace_content"


def _observation(
    action: dict,
    action_index: int,
    harness: str,
    workspace: Path,
    operation: str,
    outcome_status: str,
    scope: dict,
    *,
    attempt_status: str = "observed",
    segment_index: int | None = None,
    operand_index: int | None = None,
) -> dict:
    if operation not in ACCESS_OPERATIONS:
        operation = "unknown"
    evidence = _output_evidence(action, operation, outcome_status, harness, workspace)
    if (
        operation == "file_content_read"
        and evidence == "content_observed"
        and action.get("tool") in {"bash", "shell", "command_execution"}
        and any(not _nonempty_regular_source(path, workspace) for path in scope.get("safe_paths", []))
    ):
        evidence = "unknown"
    return {
        "schema_version": ACCESS_OBSERVATION_VERSION,
        "operation": operation,
        "attempt_status": attempt_status,
        "outcome_status": outcome_status if outcome_status in {"completed", "failed", "denied", "cancelled", "unknown"} else "unknown",
        "returned_evidence": evidence,
        "scope": scope,
        "permission_class": _permission_class(operation, scope, harness, workspace),
        "native_reference": _native_reference(action, harness),
        "action_index": action_index,
        "segment_index": segment_index,
        "operand_index": operand_index,
    }


def _unknown_observation(action: dict, index: int, harness: str, workspace: Path) -> dict:
    return _observation(
        action, index, harness, workspace, "unknown", "unknown", _scope(),
        attempt_status="unknown",
    )


def normalize_access_observations(actions: list, harness: str, workspace: Path) -> list[dict]:
    """Normalize bounded file access attempts without replacing native actions.

    Version 1 records one operation per safe path operand. Unsupported syntax,
    missing lifecycle/output facts, and ambiguous directory results stay
    unknown. Discovery and listing observations never establish content reads.
    """
    if not isinstance(actions, list) or harness not in {"opencode", "codex"}:
        return []
    observations: list[dict] = []
    native_source_reads, _native_read_page_ids = _native_read_source_completions(
        actions, harness, workspace,
    )
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool", "")).casefold()
        if tool in NATIVE_ARGUMENT_TOOLS:
            arguments, problem = validate_native_arguments(action, workspace)
            if problem is not None or not isinstance(arguments, dict):
                observations.append(_unknown_observation(action, action_index, harness, workspace))
                continue
            path = arguments.get("path")
            if not isinstance(path, str):
                observations.append(_unknown_observation(action, action_index, harness, workspace))
                continue
            outcome_status = _native_status(action, harness)
            positive: list[str] = []
            excluded: list[str] = []
            search_pattern = None
            content_range = "not_applicable"
            if tool == "read":
                result_type = action.get("native_output_result_type")
                if not _native_read_output_is_bound(action):
                    operation = "unknown"
                elif result_type == "directory_listing":
                    operation = "directory_listing"
                elif result_type == "file_content":
                    operation = "file_content_read"
                    content_range = "full" if id(action) in native_source_reads else "partial"
                else:
                    operation = "unknown"
                scope = _scope([path], content_range=content_range)
            elif tool == "list":
                operation = "directory_listing"
                excluded = list(arguments.get("ignore", []))
                scope = _scope([path], excluded_globs=excluded)
            elif tool == "glob":
                operation = "filename_discovery"
                positive = [arguments["pattern"]]
                scope = _scope([path], positive_globs=positive)
            else:
                operation = "content_search"
                search_pattern = arguments.get("pattern")
                positive = [arguments["include"]] if isinstance(arguments.get("include"), str) else []
                scope = _scope([path], positive_globs=positive, pattern=search_pattern)
            observations.append(_observation(
                action, action_index, harness, workspace, operation,
                outcome_status, scope,
            ))
            continue

        if tool in {"bash", "shell", "command_execution"}:
            command = action.get("command")
            parsed = parse_shell_command(command, harness) if isinstance(command, str) and action.get("command_truncated") is False else None
            if parsed is None or not command_sha256_matches(action):
                observations.append(_unknown_observation(action, action_index, harness, workspace))
                continue
            emitted = False
            for segment_index, segment in enumerate(parsed.segments):
                discovery = filename_discovery_scope(segment, workspace)
                if discovery is not None:
                    tool_name = Path(segment.argv[0]).name.casefold()
                    operation = "directory_listing" if tool_name == "ls" else "filename_discovery"
                    listing_semantics = {
                        key: discovery[key]
                        for key in ("recursive", "directory_operands")
                        if discovery.get(key) is True
                    }
                    scope = _scope(
                        discovery["paths"],
                        positive_globs=discovery["positive_globs"],
                        excluded_globs=discovery["excluded_globs"],
                        **listing_semantics,
                    )
                    status = _shell_status(action, harness, operation, len(parsed.segments))
                    for operand_index, path in enumerate(discovery["paths"]):
                        observations.append(_observation(
                            action, action_index, harness, workspace, operation,
                            status, _scope(
                                [path], positive_globs=discovery["positive_globs"],
                                excluded_globs=discovery["excluded_globs"],
                                **listing_semantics,
                            ), segment_index=segment_index, operand_index=operand_index,
                        ))
                    emitted = True
                    continue
                reads = _segment_read_observations(segment, workspace)
                # The segment is already unwrapped; do not reparse a Codex
                # login-shell wrapper around this inner argv.
                if classify_shell_segment(segment, workspace) is None:
                    observations.append(_unknown_observation(action, action_index, harness, workspace))
                    emitted = True
                    continue
                for path, kind, operand_index in reads:
                    operation = "content_search" if kind == "shell_content_search" else "file_content_read"
                    status = _shell_status(action, harness, operation, len(parsed.segments))
                    pattern = _search_pattern(segment) if operation == "content_search" else None
                    content_range = "full" if kind == "shell_content_read" else "partial" if operation == "file_content_read" else "not_applicable"
                    observations.append(_observation(
                        action, action_index, harness, workspace, operation,
                        status, _scope([path], pattern=pattern, content_range=content_range),
                        segment_index=segment_index, operand_index=operand_index,
                    ))
                    emitted = True
            if not emitted and any(classify_shell_segment(segment, workspace) is None for segment in parsed.segments):
                observations.append(_unknown_observation(action, action_index, harness, workspace))
            continue

        if tool in {"edit", "write", "apply_patch", "file_change"}:
            raw_path = action.get("path", action.get("command", action.get("file_path")))
            path = _safe_relative(raw_path, workspace) if isinstance(raw_path, str) else None
            scope = _scope([path] if path is not None else [])
            observations.append(_observation(
                action, action_index, harness, workspace,
                "write" if path is not None else "unknown",
                _native_status(action, harness), scope,
                attempt_status="observed" if path is not None else "unknown",
            ))
    return observations


def access_observations_equivalent(left: dict, right: dict) -> bool:
    """Compare semantic effects only when scope, permission, result, and finish agree."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    operation = left.get("operation")
    result = left.get("returned_evidence")
    return (
        left.get("schema_version") == right.get("schema_version") == ACCESS_OBSERVATION_VERSION
        and operation == right.get("operation")
        and operation in ACCESS_OPERATIONS - {"unknown"}
        and left.get("attempt_status") == right.get("attempt_status") == "observed"
        and left.get("outcome_status") == right.get("outcome_status") == "completed"
        and result == right.get("returned_evidence")
        and result in {"content_observed", "names_observed", "empty_observed"}
        and left.get("scope") == right.get("scope")
        and left.get("permission_class") == right.get("permission_class")
        and left.get("permission_class") not in {None, "unknown"}
    )


def _nonempty_regular_source(path: str, workspace: Path) -> bool:
    """Confirm a workspace-relative source exists and has at least one line."""
    try:
        if workspace.is_symlink():
            return False
        source = workspace
        for part in Path(path).parts:
            source = source / part
            if source.is_symlink():
                return False
        if not source.resolve(strict=True).is_relative_to(workspace.resolve()):
            return False
        if not source.is_file():
            return False
        payload = source.read_bytes()
    except (OSError, RuntimeError):
        return False
    return bool(payload.splitlines())


def _observed_nonempty_tool_output(action: dict) -> bool:
    outcome = action.get("outcome")
    if not isinstance(outcome, dict):
        return False
    output_bytes = outcome.get("output_bytes")
    output_hash = outcome.get("output_sha256")
    output_type = outcome.get("native_output_type")
    if action.get("tool") == "read":
        content_bytes = action.get("native_output_content_bytes")
        return (
            outcome.get("output_hash_status") == "observed"
            and output_type in {"text", "structured"}
            and type(output_bytes) is int
            and output_bytes > 0
            and isinstance(output_hash, str)
            and CALL_HASH_RE.fullmatch(output_hash) is not None
            and action.get("native_output_content_status") == "observed"
            and type(content_bytes) is int
            and content_bytes > 0
            and _native_read_output_is_bound(action)
        )
    return (
        outcome.get("output_hash_status") == "observed"
        and output_type == "text"
        and type(output_bytes) is int
        and output_bytes > 0
        and isinstance(output_hash, str)
        and CALL_HASH_RE.fullmatch(output_hash) is not None
    )


def _usable_native_source_read(
    action: dict,
    arguments: dict,
    harness: str,
    workspace: Path,
    source_reads: dict[int, list[dict]] | None = None,
) -> bool:
    return (
        action.get("tool") == "read"
        and _read_succeeded(action, harness, workspace)
        and _observed_nonempty_tool_output(action)
        and action.get("native_output_result_type") == "file_content"
        and (
            id(action) in source_reads
            if source_reads is not None
            else native_read_covers_source(arguments, action)
        )
    )


def _native_read_action_interval(action: dict) -> tuple[int, int] | None:
    start = action_sequence(action)
    end = result_sequence(action)
    if type(start) is not int or type(end) is not int or end < start:
        return None
    return start, end


def _possible_write_to_path(
    action: dict, path: str, harness: str, workspace: Path,
) -> bool:
    """Fail closed for a write attempt that could affect one source path."""
    tool = str(action.get("tool", "")).casefold()
    if tool in {"edit", "write", "apply_patch"}:
        raw_path = action.get("command")
        candidate = _safe_relative(raw_path, workspace) if isinstance(raw_path, str) else None
        return candidate is None or candidate == path
    if tool == "file_change":
        candidate = action.get("path")
        return (
            action.get("path_status") != "workspace_relative"
            or not isinstance(candidate, str)
            or candidate == path
        )
    if tool in {"bash", "shell", "command_execution"}:
        command = command_text(action, harness)
        parsed = parse_shell_command(command, harness) if command is not None else None
        if parsed is None:
            return True
        return any(classify_shell_segment(segment, workspace) is None for segment in parsed.segments)
    if tool in NATIVE_ARGUMENT_TOOLS or tool in {"question", "todowrite"}:
        return False
    # Unknown native tools may mutate workspace state; lack of a recognized
    # write path is not evidence that they left the source epoch unchanged.
    return True


def _native_read_source_completions(
    actions: list, harness: str, workspace: Path,
) -> tuple[dict[int, list[dict]], set[int]]:
    """Bind whole-source credit to an EOF read or one complete paginated read.

    Page chains use only completed, identity-bound native observations. They
    must be contiguous from line 1 to an EOF footer for one normalized path,
    have a unique read identity per page, and contain no observed possible
    write or competing successful read during the chain's source epoch.
    """
    pages_by_path: dict[str, list[dict]] = {}
    successful_reads_by_path: dict[str, list[dict]] = {}
    identity_counts: dict[str, int] = {}
    for action in actions:
        if not isinstance(action, dict) or str(action.get("tool", "")).casefold() != "read":
            continue
        arguments, problem = validate_native_arguments(action, workspace)
        if problem is not None or not isinstance(arguments, dict):
            continue
        path = arguments.get("path")
        if not isinstance(path, str) or not _read_succeeded(action, harness, workspace):
            continue
        interval = _native_read_action_interval(action)
        if interval is None:
            continue
        success = {"action": action, "path": path, "interval": interval}
        successful_reads_by_path.setdefault(path, []).append(success)
        identity = action.get("native_tool_call_id_sha256")
        if not isinstance(identity, str) or CALL_HASH_RE.fullmatch(identity) is None:
            continue
        frame = native_read_output_frame(arguments, action)
        if (
            frame is None
            or not _observed_nonempty_tool_output(action)
            or not _native_read_output_is_bound(action)
            or action.get("native_output_result_type") != "file_content"
        ):
            continue
        page = {
            **success,
            "arguments": arguments,
            "frame": frame,
            "identity": identity,
            "start_sequence": interval[0],
            "completion_sequence": interval[1],
        }
        pages_by_path.setdefault(path, []).append(page)
        identity_counts[identity] = identity_counts.get(identity, 0) + 1

    completions: dict[int, list[dict]] = {}
    contributing_ids: set[int] = set()
    for path, pages in pages_by_path.items():
        pages.sort(key=lambda item: (item["start_sequence"], item["completion_sequence"]))
        successful = successful_reads_by_path.get(path, [])
        for first in pages:
            if first["frame"]["request_offset"] != 1 or first["frame"]["first_line"] != 1:
                continue
            chain = [first]
            seen_identities = {first["identity"]}
            current = first
            while current["frame"]["footer"] == "capped":
                expected_offset = current["frame"]["next_offset"]
                next_pages = [
                    candidate for candidate in pages
                    if candidate["start_sequence"] > current["completion_sequence"]
                    and candidate["frame"]["request_offset"] == expected_offset
                    and candidate["frame"]["first_line"] == expected_offset
                ]
                if len(next_pages) != 1:
                    chain = []
                    break
                candidate = next_pages[0]
                if candidate["identity"] in seen_identities:
                    chain = []
                    break
                chain.append(candidate)
                seen_identities.add(candidate["identity"])
                current = candidate
                if len(chain) > 128:
                    chain = []
                    break
            if not chain or chain[-1]["frame"]["footer"] != "eof":
                continue
            start = chain[0]["start_sequence"]
            end = chain[-1]["completion_sequence"]
            chain_action_ids = {id(item["action"]) for item in chain}
            if any(identity_counts.get(identity, 0) != 1 for identity in seen_identities):
                continue
            # No duplicate, overlapping, or unframed successful read of this
            # path may be hidden between the first request and the final EOF.
            competing_reads = [
                item for item in successful
                if id(item["action"]) not in chain_action_ids
                and item["interval"][0] <= end
                and item["interval"][1] >= start
            ]
            if competing_reads:
                continue
            interrupted = False
            for other in actions:
                if not isinstance(other, dict) or id(other) in chain_action_ids:
                    continue
                interval = _native_read_action_interval(other)
                if interval is None:
                    if _possible_write_to_path(other, path, harness, workspace):
                        interrupted = True
                        break
                    continue
                if interval[1] < start or interval[0] > end:
                    continue
                if _possible_write_to_path(other, path, harness, workspace):
                    interrupted = True
                    break
            if interrupted:
                continue
            if id(chain[-1]["action"]) in completions:
                # Two possible page sequences ending at one action are
                # ambiguous; do not select one by iteration order.
                completions.pop(id(chain[-1]["action"]), None)
                continue
            completions[id(chain[-1]["action"])] = chain
            contributing_ids.update(chain_action_ids)
    return completions, contributing_ids


def _native_read_page_reference(page: dict, harness: str) -> dict:
    action = page["action"]
    return {
        **_native_action_reference(action, harness),
        "arguments_sequence": action.get("native_arguments_event_sequence"),
        "output_sequence": action.get("native_output_event_sequence"),
        "first_line": page["frame"]["first_line"],
        "last_line": page["frame"]["last_line"],
        "footer": page["frame"]["footer"],
    }


def _simple_sed_operands(args: list[str]) -> tuple[list[str], bool]:
    """Recognize only a simple sed address/range followed by file operands."""
    scripts = []
    index = 0
    positional = False
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            positional = True
            break
        if token in {"-n", "-E", "-r", "--quiet", "--silent"}:
            index += 1
            continue
        if token in {"-e", "--expression"}:
            if index + 1 >= len(args):
                return [], False
            scripts.append(args[index + 1])
            index += 2
            continue
        if token.startswith("-"):
            return [], False
        scripts.append(token)
        index += 1
        break
    if len(scripts) != 1:
        return [], False
    if re.fullmatch(r"(?:\d+|\$)(?:,(?:\d+|\$))?[pP]?", scripts[0]) is None:
        return [], False
    operands = args[index:]
    if not positional:
        operands = [item for item in operands if not item.startswith("-")]
    return operands, True


def _read_succeeded(action: dict, harness: str, workspace: Path) -> bool:
    if harness == "opencode":
        if action.get("tool") in {"bash", "shell"}:
            return explicit_exit_code(action, harness) == 0 and command_sha256_matches(action)
        outcome = action.get("outcome")
        if action.get("tool") in {"read", "grep"}:
            arguments, problem = validate_native_arguments(action, workspace)
            return (
                problem is None
                and isinstance(arguments, dict)
                and isinstance(outcome, dict)
                and outcome.get("native_tool_status") == "completed"
                and command_sha256_matches(action)
            )
        return isinstance(outcome, dict) and outcome.get("native_tool_status") == "completed"
    return explicit_exit_code(action, harness) == 0 and command_sha256_matches(action)


def observed_route_sources(actions: list, harness: str, workspace: Path) -> tuple[set[str], bool, bool]:
    reads: set[str] = set()
    root_route_invoked = False
    route_reads_complete = True
    native_source_reads, native_page_ids = _native_read_source_completions(
        actions, harness, workspace,
    )
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool", "")).casefold()
        value = action.get("command")
        if tool in NATIVE_ARGUMENT_TOOLS:
            arguments, problem = validate_native_arguments(action, workspace)
            if problem is not None or arguments is None:
                route_reads_complete = False
                continue
            if tool == "read" and _read_succeeded(action, harness, workspace):
                relative = arguments.get("path")
                if relative in ROUTE_SOURCES:
                    if id(action) in native_source_reads:
                        reads.add(relative)
                    elif id(action) not in native_page_ids:
                        route_reads_complete = False
                else:
                    route_reads_complete = False
        elif tool in {"bash", "shell", "command_execution"}:
            if not isinstance(value, str) or action.get("command_truncated") is not False:
                route_reads_complete = False
                continue
            succeeded = _read_succeeded(action, harness, workspace)
            parsed = parse_shell_command(value, harness)
            if succeeded and parsed is None:
                route_reads_complete = False
                continue
            if not succeeded or parsed is None:
                continue
            _operands, classified = _read_operands(value, harness, workspace)
            if not classified:
                route_reads_complete = False
                continue
            full_reads = {
                path for path, kind, _segment_index, _operand_index
                in _shell_read_observations(value, harness, workspace)
                if kind == "shell_content_read"
                and _observed_nonempty_tool_output(action)
                and _nonempty_regular_source(path, workspace)
            }
            reads.update(full_reads & ROUTE_SOURCES)
            for segment in parsed.segments:
                if start_here_task(segment) == "contribute":
                    root_route_invoked = True
    return reads, root_route_invoked, route_reads_complete


def _native_action_reference(action: dict, harness: str) -> dict:
    identity_key = "native_tool_call_id_sha256" if harness == "opencode" else "native_item_id_sha256"
    identity = action.get(identity_key)
    return {
        "event_sequence": action_sequence(action),
        "completion_sequence": result_sequence(action),
        "identity_kind": identity_key,
        "identity_sha256": identity if isinstance(identity, str) and CALL_HASH_RE.fullmatch(identity) else None,
        "tool": action.get("tool"),
    }


def content_read_observations(actions: list, harness: str, workspace: Path) -> list[dict]:
    """List evidenced full-source reads and distinct searches; discovery is excluded."""
    observations: list[dict] = []
    native_source_reads, _native_page_ids = _native_read_source_completions(
        actions, harness, workspace,
    )
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool", "")).casefold()
        sequence = result_sequence(action)
        start_sequence = action_sequence(action)
        if type(sequence) is not int or type(start_sequence) is not int:
            continue
        if tool in {"read", "grep"}:
            arguments, problem = validate_native_arguments(action, workspace)
            if problem is None and isinstance(arguments, dict) and _read_succeeded(action, harness, workspace):
                relative = arguments.get("path")
                kind = None
                if tool == "read" and _usable_native_source_read(
                    action, arguments, harness, workspace, native_source_reads,
                ):
                    kind = "native_content_read"
                elif tool == "grep" and _observed_nonempty_tool_output(action):
                    kind = "native_content_search"
                if isinstance(relative, str) and relative != "." and kind is not None:
                    item = {
                        "path": relative,
                        "sequence": sequence,
                        "start_sequence": start_sequence,
                        "action": _native_action_reference(action, harness),
                        "kind": kind,
                        "segment_index": None,
                        "operand_index": None,
                    }
                    if tool == "read":
                        item["read_pages"] = [
                            _native_read_page_reference(page, harness)
                            for page in native_source_reads[id(action)]
                        ]
                    observations.append(item)
            continue
        if tool not in {"bash", "shell", "command_execution"}:
            continue
        command = action.get("command")
        if not isinstance(command, str) or not _read_succeeded(action, harness, workspace):
            continue
        if not _observed_nonempty_tool_output(action):
            continue
        parsed = parse_shell_command(command, harness)
        _operands, classified = _read_operands(command, harness, workspace)
        if parsed is None or not classified:
            continue
        for relative, kind, segment_index, operand_index in _shell_read_observations(
            command, harness, workspace,
        ):
            if kind == "shell_content_read" and not _nonempty_regular_source(relative, workspace):
                continue
            observations.append({
                "path": relative,
                "sequence": sequence,
                "start_sequence": start_sequence,
                "action": _native_action_reference(action, harness),
                "kind": kind,
                "segment_index": segment_index,
                "operand_index": operand_index,
            })
    return observations


def _route_action_identity(item: dict) -> tuple[str, str] | None:
    action = item.get("action")
    if not isinstance(action, dict):
        return None
    kind = action.get("identity_kind")
    identity = action.get("identity_sha256")
    if (
        not isinstance(kind, str)
        or not isinstance(identity, str)
        or CALL_HASH_RE.fullmatch(identity) is None
    ):
        return None
    return kind, identity


def _same_route_action(left: dict, right: dict) -> bool:
    left_identity = _route_action_identity(left)
    right_identity = _route_action_identity(right)
    if left_identity is None or left_identity != right_identity:
        return False
    left_action = left.get("action")
    right_action = right.get("action")
    assert isinstance(left_action, dict) and isinstance(right_action, dict)
    return (
        left_action.get("tool") == right_action.get("tool")
        and type(left_action.get("event_sequence")) is int
        and type(left_action.get("completion_sequence")) is int
        and left_action.get("event_sequence") == right_action.get("event_sequence")
        and left_action.get("completion_sequence") == right_action.get("completion_sequence")
    )


def _route_position(item: dict) -> tuple[int, int] | None:
    segment_index = item.get("segment_index")
    operand_index = item.get("operand_index")
    if type(segment_index) is not int or segment_index < 0:
        return None
    if operand_index is None:
        return segment_index, -1
    if type(operand_index) is not int or operand_index < 0:
        return None
    return segment_index, operand_index


def _strictly_precedes(left: dict, right: dict) -> bool | None:
    """Compare sub-actions in one native call or non-overlapping native calls.

    ``None`` means the trace does not establish an order. Positions are local
    to a native action and never create a new native identity or timestamp.
    """
    if _same_route_action(left, right):
        left_position = _route_position(left)
        right_position = _route_position(right)
        if left_position is None or right_position is None or left_position == right_position:
            return None
        return left_position < right_position

    left_start = left.get("start_sequence")
    left_end = left.get("sequence")
    right_start = right.get("start_sequence")
    right_end = right.get("sequence")
    if not all(type(value) is int for value in (left_start, left_end, right_start, right_end)):
        return None
    if left_end < right_start:
        return True
    if right_end < left_start:
        return False
    return None


def _earliest_ordered_item(items: list[dict]) -> dict | None:
    """Select an item proven to precede every other candidate, if unique."""
    for candidate_index, candidate in enumerate(items):
        if all(
            candidate_index == other_index
            or _strictly_precedes(candidate, other) is True
            for other_index, other in enumerate(items)
        ):
            return candidate
    return None


def entry_route_evidence(
    actions: list,
    harness: str,
    workspace: Path,
    *,
    task: str,
    task_route_paths: tuple[str, ...],
) -> dict:
    """Measure root-first, selector/start-here, then task-specific route reads."""
    observations = content_read_observations(actions, harness, workspace)
    reads = [
        item for item in observations
        if item.get("kind") in {"native_content_read", "shell_content_read"}
    ]
    consultations = [
        item for item in observations
        if item.get("kind") in {
            "native_content_read", "native_content_search", "shell_content_read",
            "shell_content_search", "shell_partial_content_read",
        }
    ]
    root_reads: dict[str, dict] = {}
    missing = []
    for path in sorted(ROOT_ENTRIES):
        candidates = [item for item in reads if item["path"] == path]
        first = _earliest_ordered_item(candidates)
        if first is None:
            missing.append(f"completed content read of {path}")
            if candidates:
                missing.append(f"orderable first-read position for {path}")
            continue
        root_reads[path] = first

    if len(root_reads) == len(ROOT_ENTRIES) and any(
        _strictly_precedes(root_read, item) is not True
        for item in consultations
        if item["path"] not in ROOT_ENTRIES
        for root_read in root_reads.values()
    ):
        missing.append("README.md and AGENTS.md are the first content sources consulted")

    helper_candidates = []
    if len(root_reads) == len(ROOT_ENTRIES):
        for action in actions:
            if not isinstance(action, dict) or action.get("tool") not in {"bash", "shell", "command_execution"}:
                continue
            command = action.get("command")
            if not isinstance(command, str) or not _read_succeeded(action, harness, workspace):
                continue
            parsed = parse_shell_command(command, harness)
            if parsed is None:
                continue
            if not all(classify_shell_segment(segment, workspace) is not None for segment in parsed.segments):
                continue
            sequence = result_sequence(action)
            start_sequence = action_sequence(action)
            if type(sequence) is not int or type(start_sequence) is not int:
                continue
            for segment_index, segment in enumerate(parsed.segments):
                if start_here_task(segment) != task:
                    continue
                candidate = {
                    "kind": "start_here_cli_selector",
                    "task": task,
                    "sequence": sequence,
                    "start_sequence": start_sequence,
                    "action": _native_action_reference(action, harness),
                    "segment_index": segment_index,
                    "operand_index": None,
                }
                if all(_strictly_precedes(root_read, candidate) is True for root_read in root_reads.values()):
                    helper_candidates.append(candidate)
    readable_candidates = []
    if len(root_reads) == len(ROOT_ENTRIES):
        for item in reads:
            if item["path"] != "docs/agents/start-here.md":
                continue
            candidate = {
                "kind": "readable_start_here_packet",
                "task": task,
                "sequence": item["sequence"],
                "start_sequence": item["start_sequence"],
                "action": item["action"],
                "segment_index": item.get("segment_index"),
                "operand_index": item.get("operand_index"),
            }
            if all(_strictly_precedes(root_read, candidate) is True for root_read in root_reads.values()):
                readable_candidates.append(candidate)
    route_candidates = [*helper_candidates, *readable_candidates]
    entry = _earliest_ordered_item(route_candidates)
    if entry is None:
        missing.append("successful task selector or completed readable docs/agents/start-here.md after root reads")

    task_reads: dict[str, dict] = {}
    if entry is not None:
        for path in task_route_paths:
            matches = [
                item for item in reads
                if item["path"] == path and _strictly_precedes(entry, item) is True
            ]
            first = _earliest_ordered_item(matches)
            if first is not None:
                task_reads[path] = first
            else:
                missing.append(f"completed task-specific route read of {path} after the entry route")
    else:
        missing.extend(f"completed task-specific route read of {path} after the entry route" for path in task_route_paths)

    observed_root = {
        path: {
            "sequence": item["sequence"],
            "action": item["action"],
            "segment_index": item.get("segment_index"),
            "operand_index": item.get("operand_index"),
        }
        for path, item in sorted(root_reads.items())
    }
    return {
        "contract_version": "entrypoint-first-v1",
        "task": task,
        "status": "pass" if not missing else "fail",
        "criteria": {
            "root_files": ["README.md", "AGENTS.md"],
            "root_files_first_content_sources": True,
            "entry_route": ["scripts/start_here.py --task <task>", "docs/agents/start-here.md"],
            "task_route_paths": list(task_route_paths),
            "injected_prompt_alone_satisfies_no_criterion": True,
        },
        "root_entry_reads": observed_root,
        "entry_route_observation": entry,
        "task_route_reads": task_reads,
        "content_read_observations": observations,
        "missing_evidence": list(dict.fromkeys(missing)),
    }


def suite_accesses(actions: list, harness: str, workspace: Path) -> list[int]:
    if not isinstance(actions, list):
        return []
    by_index: dict[int, list[dict]] = {}
    for observation in normalize_access_observations(actions, harness, workspace):
        index = observation.get("action_index")
        if type(index) is int:
            by_index.setdefault(index, []).append(observation)

    accesses = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        observations = by_index.get(index, [])
        if not observations:
            # A path-like field without a normalized native operation does not
            # establish that protected content was accessed. The audit caller
            # retains this action as unknown and can report its missing evidence.
            continue
        for observation in observations:
            operation = observation.get("operation")
            if operation in {"directory_listing", "filename_discovery"}:
                scope = observation.get("scope")
                safe_paths = scope.get("safe_paths") if isinstance(scope, dict) else None
                if (
                    observation.get("permission_class") == "filename_only"
                    and isinstance(safe_paths, list)
                    and bool(safe_paths)
                    and observation.get("returned_evidence") != "content_observed"
                ):
                    continue
                if observation.get("returned_evidence") == "content_observed" and direct_suite_access(
                    action, harness, workspace,
                ):
                    accesses.append(index)
                    break
                continue
            if operation == "unknown":
                # Only a fully observed protected-path attempt remains a
                # reserved-access finding here (for example, a denied read).
                # Unknown arguments/output alone never establish access.
                if (
                    observation.get("attempt_status") == "observed"
                    and direct_suite_access(action, harness, workspace)
                ):
                    accesses.append(index)
                    break
                continue
            if operation in {"file_content_read", "content_search"}:
                if observation.get("permission_class") == "protected_content":
                    accesses.append(index)
                    break
                continue
            if operation == "write" and direct_suite_access(action, harness, workspace):
                accesses.append(index)
                break
    return accesses


def command_sha256_matches(action: dict) -> bool:
    command = action.get("command")
    if not isinstance(command, str) or action.get("command_truncated") is not False:
        return False
    try:
        encoded = command.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        action.get("command_sha256") == hashlib.sha256(encoded).hexdigest()
        and type(action.get("command_bytes")) is int
        and action.get("command_bytes") == len(encoded)
    )


FINAL_CHECK_TOOLS = {"bash", "shell", "command_execution"}
FAILED_NATIVE_STATUSES = {
    "failed", "error", "denied", "permission_denied", "rejected", "blocked",
    "cancelled", "canceled", "timeout", "timed_out", "skipped",
}


def _final_check_identity(action: dict, harness: str) -> str | None:
    key = "native_tool_call_id_sha256" if harness == "opencode" else "native_item_id_sha256"
    value = action.get(key)
    return value if isinstance(value, str) and CALL_HASH_RE.fullmatch(value) else None


def _final_check_native_status(action: dict, harness: str) -> str | None:
    if harness == "opencode":
        outcome = action.get("outcome")
        value = outcome.get("native_tool_status") if isinstance(outcome, dict) else None
    else:
        value = action.get("native_status")
    return value if isinstance(value, str) else None


def _final_check_outcome(action: dict, harness: str) -> tuple[str, int | None, str | None]:
    native_status = _final_check_native_status(action, harness)
    exit_code = explicit_exit_code(action, harness)
    if exit_code is not None and exit_code != 0:
        return "failed", exit_code, native_status
    if native_status in FAILED_NATIVE_STATUSES:
        return "failed", exit_code, native_status
    if native_status == "completed" and exit_code == 0:
        return "passed", exit_code, native_status
    return "unverified", exit_code, native_status


def _marker_text(action: dict) -> str:
    command = action.get("command")
    if isinstance(command, str):
        return command
    preview = action.get("command_preview")
    return preview if isinstance(preview, str) else ""


def _raw_mentions_completion_checker(command: str) -> bool:
    unquoted = command.replace("'", "").replace('"', "").replace("\\", "")
    return (
        "scripts/start_here.py" in unquoted
        and re.search(r"(?<![A-Za-z0-9_-])--check-completion(?![A-Za-z0-9_-])", unquoted) is not None
    )


def _segment_mentions_completion_checker(segment) -> bool:
    words = list(segment.argv)
    if len(words) < 3 or re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(words[0]).name.casefold()) is None:
        return False
    script = words[1].removeprefix("./")
    has_cli = script == "scripts/start_here.py" or script.endswith("/scripts/start_here.py")
    has_flag = any(
        word == "--check-completion" or word.startswith("--check-completion=")
        for word in words[2:]
    )
    return has_cli and has_flag


def _completion_checker_attempt(action: dict, harness: str) -> bool:
    if action.get("tool") not in FINAL_CHECK_TOOLS:
        return False
    command = _marker_text(action)
    parsed = parse_shell_command(command, harness) if command else None
    if parsed is not None:
        return any(
            is_completion_checker_segment(segment) or _segment_mentions_completion_checker(segment)
            for segment in parsed.segments
        )
    return bool(command and _raw_mentions_completion_checker(command))


def _segment_mentions_git_diff_check(segment) -> bool:
    words = list(segment.argv)
    return bool(
        words
        and Path(words[0]).name.casefold() == "git"
        and "diff" in words[1:]
        and "--check" in words[1:]
    )


def _raw_mentions_git_diff_check(command: str) -> bool:
    unquoted = command.replace("'", "").replace('"', "").replace("\\", "")
    return re.search(r"(?<![A-Za-z0-9_-])git\s+.*?\bdiff\b.*?(?<![A-Za-z0-9_-])--check(?![A-Za-z0-9_-])", unquoted) is not None


def _git_diff_check_attempt(action: dict, harness: str) -> bool:
    if action.get("tool") not in FINAL_CHECK_TOOLS:
        return False
    command = _marker_text(action)
    parsed = parse_shell_command(command, harness) if command else None
    if parsed is not None:
        return any(_segment_mentions_git_diff_check(segment) for segment in parsed.segments)
    return bool(command and _raw_mentions_git_diff_check(command))


def _final_attempt_record(
    action: dict,
    index: int,
    harness: str,
    workspace: Path,
    expected_command: str,
    expected_kind: str,
    attempt_kind: str,
    identity_counts: dict[str, int],
) -> dict:
    command = command_text(action, harness)
    parsed = parse_shell_command(command, harness) if command is not None else None
    expected_key = command_semantic_key(expected_command, harness)
    canonical = bool(
        parsed is not None
        and len(parsed.segments) == 1
        and expected_key is not None
        and command_semantic_key(command, harness) == expected_key
        and classify_shell_segment(parsed.segments[0], workspace) == expected_kind
    )
    identity = _final_check_identity(action, harness)
    start = action_sequence(action)
    completed = action.get("completion_sequence")
    if type(completed) is not int:
        completed = None
    outcome_status, exit_code, native_status = _final_check_outcome(action, harness)
    return {
        "action_index": index,
        "attempt_kind": attempt_kind,
        "event_sequence": start,
        "completion_sequence": completed,
        "native_identity_sha256": identity,
        "native_identity_unique": identity is not None and identity_counts.get(identity, 0) == 1,
        "native_status": native_status,
        "exit_code": exit_code,
        "outcome_status": outcome_status,
        "command_sha256": action.get("command_sha256") if isinstance(action.get("command_sha256"), str) else None,
        "command_digest_matches": command_sha256_matches(action),
        "canonical_command": canonical,
        "lifecycle_order_valid": (
            type(start) is int and start >= 0
            and type(completed) is int and completed >= start
        ),
        "selection": "unresolved",
    }


def _terminal_final_attempt(records: list[dict]) -> tuple[dict | None, str | None]:
    """Select a unique last-started attempt only when all earlier calls ended before it began."""
    if not records:
        return None, "missing"
    if any(type(item.get("event_sequence")) is not int or item["event_sequence"] < 0 for item in records):
        return None, "incomplete_start_order"
    latest_start = max(item["event_sequence"] for item in records)
    latest = [item for item in records if item["event_sequence"] == latest_start]
    if len(latest) != 1:
        return None, "ambiguous_terminal_start"
    selected = latest[0]
    for earlier in records:
        if earlier is selected:
            continue
        earlier_end = earlier.get("completion_sequence")
        if (
            type(earlier_end) is not int
            or type(earlier.get("event_sequence")) is not int
            or earlier_end < earlier["event_sequence"]
            or earlier_end >= selected["event_sequence"]
        ):
            return None, "overlapping_or_incomplete_lifecycle"
    return selected, None


def _attempt_history(records: list[dict], selected: dict | None) -> list[dict]:
    history = []
    for number, item in enumerate(sorted(
        records,
        key=lambda record: (
            record.get("event_sequence") if type(record.get("event_sequence")) is int else 2**63,
            record["action_index"],
        ),
    ), 1):
        history.append({
            "attempt": number,
            "event_sequence": item.get("event_sequence"),
            "completion_sequence": item.get("completion_sequence"),
            "native_identity_sha256": item.get("native_identity_sha256"),
            "native_identity_unique": item.get("native_identity_unique"),
            "native_status": item.get("native_status"),
            "exit_code": item.get("exit_code"),
            "outcome_status": item.get("outcome_status"),
            "command_sha256": item.get("command_sha256"),
            "command_digest_matches": item.get("command_digest_matches"),
            "canonical_command": item.get("canonical_command"),
            "lifecycle_order_valid": item.get("lifecycle_order_valid"),
            "selection": (
                "terminal" if item is selected
                else "superseded" if selected is not None
                and type(item.get("completion_sequence")) is int
                and type(selected.get("event_sequence")) is int
                and item["completion_sequence"] < selected["event_sequence"]
                else "ambiguous"
            ),
        })
    return history


def _attempt_action_summary(record: dict | None) -> dict | None:
    if record is None:
        return None
    return {
        "event_sequence": record.get("event_sequence"),
        "completion_sequence": record.get("completion_sequence"),
        "native_identity_sha256": record.get("native_identity_sha256"),
        "native_identity_unique": record.get("native_identity_unique"),
        "native_status": record.get("native_status"),
        "exit_code": record.get("exit_code"),
        "outcome_status": record.get("outcome_status"),
        "command_digest_matches": record.get("command_digest_matches"),
        "canonical_command": record.get("canonical_command"),
        "lifecycle_order_valid": record.get("lifecycle_order_valid"),
    }


def _later_completion_relevant_kind(action: dict, harness: str, workspace: Path) -> str | None:
    if _completion_checker_attempt(action, harness):
        return "completion_checker_attempt"
    if _git_diff_check_attempt(action, harness):
        return "git_diff_check_attempt"
    command = command_text(action, harness)
    if command is None:
        preview = _marker_text(action)
        if "validate_agent_docs.py" in preview:
            return "validator_attempt_unclassified"
        return "unclassified_shell_action"
    parsed = command_segments(command, harness)
    if parsed is None:
        if "validate_agent_docs.py" in command:
            return "validator_attempt_unclassified"
        return "unclassified_shell_action"
    kinds = [classify_shell_segment(segment, workspace) for segment in parsed]
    if "validator" in kinds:
        return "validator_attempt"
    if "completion-checker" in kinds:
        return "completion_checker_attempt"
    if "completion-json" in kinds or any(is_completion_json_segment(segment) for segment in parsed):
        return "completion_artifact_inspection"
    if any(kind is None for kind in kinds):
        return "unclassified_shell_action"
    return None


def final_completion_sequence_evidence(
    actions: list,
    harness: str,
    workspace: Path,
    *,
    checker_command: str,
    diff_command: str,
    artifact_write_sequences: dict,
    required_artifact_paths: tuple[str, ...],
    scope: str,
) -> tuple[dict, list[str]]:
    """Select the terminal native check attempts and verify their final artifact ordering.

    Every checker/diff attempt is retained in the result. Repairs may supersede
    earlier attempts only when their native lifecycles are distinctly ordered;
    the terminal action itself must use the exact fixed command and prove its
    own completed native outcome.
    """
    problems: list[str] = []
    checker_key = command_semantic_key(checker_command, harness)
    diff_key = command_semantic_key(diff_command, harness)
    if checker_key is None or diff_key is None:
        problems.append("fixed final completion command grammar is not supported by the selected harness")

    identity_counts: dict[str, int] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        identity = _final_check_identity(action, harness)
        if identity is not None:
            identity_counts[identity] = identity_counts.get(identity, 0) + 1

    checker_records = []
    diff_records = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("tool") not in FINAL_CHECK_TOOLS:
            continue
        if _completion_checker_attempt(action, harness):
            checker_records.append(_final_attempt_record(
                action, index, harness, workspace, checker_command,
                "completion-checker", "completion_checker", identity_counts,
            ))
        if _git_diff_check_attempt(action, harness):
            diff_records.append(_final_attempt_record(
                action, index, harness, workspace, diff_command,
                "git-read-only", "git_diff_check", identity_counts,
            ))

    selected_checker, checker_selection_problem = _terminal_final_attempt(checker_records)
    selected_diff, diff_selection_problem = _terminal_final_attempt(diff_records)
    if selected_checker is not None:
        selected_checker["selection"] = "terminal"
    if selected_diff is not None:
        selected_diff["selection"] = "terminal"
    for record in checker_records:
        if record is not selected_checker:
            record["selection"] = "superseded" if (
                selected_checker is not None
                and type(record.get("completion_sequence")) is int
                and type(selected_checker.get("event_sequence")) is int
                and record["completion_sequence"] < selected_checker["event_sequence"]
            ) else "ambiguous"
    for record in diff_records:
        if record is not selected_diff:
            record["selection"] = "superseded" if (
                selected_diff is not None
                and type(record.get("completion_sequence")) is int
                and type(selected_diff.get("event_sequence")) is int
                and record["completion_sequence"] < selected_diff["event_sequence"]
            ) else "ambiguous"

    if not checker_records:
        problems.append(f"exact final {scope} --check-completion invocation is missing")
    elif selected_checker is None:
        problems.append(f"{scope} completion-check attempt history has no unique terminal action ({checker_selection_problem})")
    if not diff_records:
        problems.append(f"exact final {scope} git diff --check invocation is missing")
    elif selected_diff is None:
        problems.append(f"{scope} git diff --check attempt history has no unique terminal action ({diff_selection_problem})")

    for name, records in (("completion checker", checker_records), ("git diff --check", diff_records)):
        if any(not record.get("native_identity_unique") for record in records):
            problems.append(f"{scope} {name} attempt history contains a missing or duplicated native lifecycle identity")

    def check_terminal(record: dict | None, label: str) -> str:
        if record is None:
            return "unverified"
        if not record.get("canonical_command"):
            problems.append(f"terminal {scope} {label} is malformed or differs from the exact fixed grammar")
        if not record.get("command_digest_matches"):
            problems.append(f"terminal {scope} {label} command digest is missing or mismatched")
        if not record.get("native_identity_unique"):
            problems.append(f"terminal {scope} {label} native lifecycle identity is missing, duplicated, or ambiguous")
        if not record.get("lifecycle_order_valid"):
            problems.append(f"terminal {scope} {label} has missing or invalid native lifecycle completion ordering")
        if record.get("outcome_status") == "failed":
            problems.append(f"terminal {scope} {label} has an observed failed native outcome")
        elif record.get("outcome_status") != "passed":
            problems.append(f"terminal {scope} {label} has an unknown or incomplete native outcome")
        return record.get("outcome_status", "unverified")

    checker_status = check_terminal(selected_checker, "completion checker")
    diff_outcome = check_terminal(selected_diff, "git diff --check")

    write_sequences = artifact_write_sequences if isinstance(artifact_write_sequences, dict) else {}
    normalized_writes: dict[str, list[int]] = {}
    missing_write_paths = []
    malformed_write_paths = []
    for path in required_artifact_paths:
        raw_sequences = write_sequences.get(path)
        if not isinstance(raw_sequences, (list, tuple)) or not raw_sequences:
            missing_write_paths.append(path)
            normalized_writes[path] = []
            continue
        if any(type(value) is not int or value < 0 for value in raw_sequences):
            malformed_write_paths.append(path)
            normalized_writes[path] = [value for value in raw_sequences if type(value) is int and value >= 0]
            continue
        normalized_writes[path] = sorted(set(raw_sequences))
    if missing_write_paths:
        problems.append("final artifact write history is missing for: " + ", ".join(missing_write_paths))
    if malformed_write_paths:
        problems.append("final artifact write history has invalid native completion sequences for: " + ", ".join(malformed_write_paths))
    final_write = max(
        (sequence for values in normalized_writes.values() for sequence in values),
        default=None,
    )
    checker_start = selected_checker.get("event_sequence") if selected_checker is not None else None
    diff_start = selected_diff.get("event_sequence") if selected_diff is not None else None
    diff_end = selected_diff.get("completion_sequence") if selected_diff is not None else None
    checker_end = selected_checker.get("completion_sequence") if selected_checker is not None else None
    if final_write is None or missing_write_paths or malformed_write_paths:
        pass
    elif diff_start is None or final_write >= diff_start:
        problems.append(f"all final {scope} artifacts were not written before the selected git diff --check began")
    if final_write is None or missing_write_paths or malformed_write_paths:
        pass
    elif checker_start is None or final_write >= checker_start:
        problems.append(f"all final {scope} artifacts were not written before the selected completion checker began")
    if (
        selected_checker is not None
        and selected_diff is not None
        and (
            type(diff_end) is not int
            or type(checker_start) is not int
            or diff_end >= checker_start
        )
    ):
        problems.append(f"successful canonical {scope} git diff --check did not complete before the selected completion checker began")

    later_relevant = []
    if selected_checker is not None:
        selected_checker_index = selected_checker["action_index"]
        for index, action in enumerate(actions):
            if (
                not isinstance(action, dict)
                or action.get("tool") not in FINAL_CHECK_TOOLS
                or index == selected_checker_index
            ):
                continue
            kind = _later_completion_relevant_kind(action, harness, workspace)
            if kind is None:
                continue
            start = action_sequence(action)
            end = action.get("completion_sequence")
            if type(end) is not int:
                end = None
            relation = "unorderable"
            if type(start) is int and type(end) is int and type(checker_start) is int and type(checker_end) is int:
                if end < checker_start:
                    continue
                relation = "later" if checker_end < start else "overlapping"
            summary = {
                "kind": kind,
                "event_sequence": start,
                "completion_sequence": end,
                "native_identity_sha256": _final_check_identity(action, harness),
                "command_sha256_matches": command_sha256_matches(action),
                "relation_to_terminal_checker": relation,
            }
            later_relevant.append(summary)
    if later_relevant:
        problems.append(f"another relevant write/check or unclassified shell action ran after the final {scope} completion checker")

    for history, selected in ((checker_records, selected_checker), (diff_records, selected_diff)):
        if selected is not None:
            for record in history:
                if record is selected:
                    continue
                if record.get("selection") != "superseded":
                    problems.append(f"{scope} check attempts are concurrent or not causally ordered")
                    break

    checker_action = actions[selected_checker["action_index"]] if selected_checker is not None else None
    diff_action = actions[selected_diff["action_index"]] if selected_diff is not None else None
    checker_exit = selected_checker.get("exit_code") if selected_checker is not None else None
    diff_exit = selected_diff.get("exit_code") if selected_diff is not None else None
    diff_status = (
        "passed" if diff_outcome == "passed" and selected_diff is not None
        else "failed" if diff_outcome == "failed"
        else "unverified"
    )
    identity_conflict = any(
        not record.get("native_identity_unique") for record in checker_records + diff_records
    )
    known_failure = (
        checker_status == "failed"
        or diff_outcome == "failed"
        or bool(later_relevant)
        or any(
            "malformed" in problem or "were not written before" in problem
            or "did not complete before" in problem or "concurrent" in problem
            for problem in problems
        )
    )
    final_check = not problems
    status = "passed" if final_check else "failed" if known_failure else "unverified"
    evidence = {
        "status": status,
        "completion_checker_status": checker_status,
        "completion_command": checker_command,
        "completion_event_sequence": checker_start,
        "completion_sequence": checker_end,
        "completion_exit_code": checker_exit,
        "completion_native_identity_sha256": (
            selected_checker.get("native_identity_sha256") if selected_checker is not None else None
        ),
        "completion_attempt_history": _attempt_history(checker_records, selected_checker),
        "diff_command": diff_command,
        "diff_event_sequence": diff_start,
        "diff_completion_sequence": diff_end,
        "diff_exit_code": diff_exit,
        "diff_status": diff_status,
        "diff_native_identity_sha256": (
            selected_diff.get("native_identity_sha256") if selected_diff is not None else None
        ),
        "diff_attempt_history": _attempt_history(diff_records, selected_diff),
        "artifact_write_sequences": normalized_writes,
        "final_artifact_write_sequence": final_write,
        "final_actions": {
            "git_diff_check": _attempt_action_summary(selected_diff),
            "completion_checker": _attempt_action_summary(selected_checker),
            "establish_completion": final_check,
        },
        "later_relevant_actions": later_relevant,
        "final_check": final_check,
    }
    # Identity ambiguity is evidence uncertainty even when a shell exit was observed.
    if identity_conflict and status == "failed" and checker_status != "failed" and diff_outcome != "failed":
        evidence["status"] = "unverified"
    return evidence, list(dict.fromkeys(problems))
