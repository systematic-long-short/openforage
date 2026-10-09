"""Observed command and canonical-source checks shared by the discovery grader.

This module contains no expected artifact values and does not inspect
free-form reasoning. It recognizes selector invocations, exact route-source
reads, and validator modes from recorded actions.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Callable

VALIDATOR_REL = ".github/scripts/validate_agent_docs.py"
VALIDATOR_SCRIPT = ".github/scripts/validate_agent_docs.py"
START_HERE_SCRIPT = "scripts/start_here.py"
PYTHON_NAME_RE = re.compile(r"python(?:3(?:\.\d+)?)?\Z")
ENV_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*\Z")
CANONICAL_TASK_READS = {
    "contribute": frozenset(
        {
            START_HERE_SCRIPT,
            "docs/agents/references/onboarding.json",
            "docs/agents/start-here.md",
        }
    ),
}


def _command_segments(command: str) -> list[list[str]]:
    """Split a recorded shell command at unquoted control operators."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for word in words:
        if re.fullmatch(r"[;&|]+", word):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(word)
    if current:
        segments.append(current)
    return segments


def _has_control_operator(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        return any(re.fullmatch(r"[;&|]+", word) for word in lexer)
    except ValueError:
        return True


def is_standalone_shell_command(command: object) -> bool:
    """Whether a command is one complete, unchained shell invocation."""
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        return False
    return not _has_control_operator(command) and len(_command_segments(command)) == 1


def _is_python(token: str) -> bool:
    return bool(PYTHON_NAME_RE.fullmatch(Path(token).name))


def _drop_environment_prefix(words: list[str]) -> list[str]:
    result = list(words)
    if result and Path(result[0]).name == "env":
        result.pop(0)
    while result and ENV_ASSIGNMENT_RE.fullmatch(result[0]):
        result.pop(0)
    return result


def start_here_task_invoked(
    command: str,
    task: str,
    inspection_only: Callable[[str], bool],
) -> bool:
    """Recognize a real Python invocation of the requested startup route.

    The helper's task and output-format options are parsed independently of
    order and accept argparse's ``--option=value`` form. Quoted mentions in a
    read-only inspection command are not invocations.
    """
    if not command or inspection_only(command):
        return False

    for segment in _command_segments(command):
        rendered = " ".join(shlex.quote(word) for word in segment)
        if inspection_only(rendered):
            continue
        words = _drop_environment_prefix(segment)
        if len(words) < 2 or not _is_python(words[0]):
            continue
        script = words[1].removeprefix("./")
        if script != START_HERE_SCRIPT:
            continue

        selected_task = None
        action_flag = False
        args = words[2:]
        index = 0
        valid = True
        while index < len(args):
            token = args[index]
            if token == "--task":
                index += 1
                if index >= len(args) or args[index].startswith("--"):
                    valid = False
                    break
                selected_task = args[index]
            elif token.startswith("--task="):
                selected_task = token.partition("=")[2]
                if not selected_task:
                    valid = False
                    break
            elif token == "--format":
                index += 1
                if index >= len(args) or args[index] not in {"text", "json"}:
                    valid = False
                    break
            elif token.startswith("--format="):
                if token.partition("=")[2] not in {"text", "json"}:
                    valid = False
                    break
            elif token == "--harness":
                index += 1
                if index >= len(args) or not args[index] or args[index].startswith("--"):
                    valid = False
                    break
            elif token.startswith("--harness="):
                if not token.partition("=")[2]:
                    valid = False
                    break
            elif token in {"--generate", "--check", "--selfcheck", "--list-tasks", "--list-harnesses"}:
                action_flag = True
            else:
                valid = False
                break
            index += 1

        if valid and not action_flag and selected_task == task:
            return True
    return False


def _workspace_relative_path(command: str, workspace: Path) -> str | None:
    """Return one exact workspace-relative path, never a path mentioned in text."""
    value = command.strip()
    prefixes = (
        "<WORKSPACE>/",
        f"{Path(workspace).resolve().as_posix()}/",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    else:
        if value.startswith("/"):
            return None

    if value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        return None
    return value


def _cat_file_operands(command: str, workspace: Path, inspection_only: Callable[[str], bool]) -> set[str]:
    """Recognize simple real ``cat FILE`` reads without treating search text as a path."""
    if not command or not inspection_only(command):
        return set()
    paths: set[str] = set()
    for segment in _command_segments(command):
        words = _drop_environment_prefix(segment)
        if not words or Path(words[0]).name != "cat":
            continue
        args = words[1:]
        after_options = False
        skip_redirect_target = False
        for word in args:
            if skip_redirect_target:
                skip_redirect_target = False
                continue
            if word in {"<", ">", "<<", ">>"}:
                skip_redirect_target = True
                continue
            if word == "--":
                after_options = True
                continue
            if not after_options and word.startswith("-"):
                continue
            path = _workspace_relative_path(word, workspace)
            if path:
                paths.add(path)
    return paths


def _canonical_task_source_read(
    action,
    task: str,
    workspace: Path,
    inspection_only: Callable[[str], bool],
) -> bool:
    paths = CANONICAL_TASK_READS.get(task, frozenset())
    if not paths or not isinstance(action, dict):
        return False
    tool = action.get("tool")
    command = action.get("command")
    if not isinstance(tool, str) or not isinstance(command, str):
        return False
    tool = tool.casefold()
    if tool == "read":
        return _workspace_relative_path(command, workspace) in paths
    if tool in {"bash", "shell"}:
        return bool(_cat_file_operands(command, workspace, inspection_only) & paths)
    return False


def start_here_task_or_read_observed(
    observed_actions: list,
    task: str,
    workspace: Path,
    inspection_only: Callable[[str], bool],
) -> bool:
    """Accept the requested selector invocation or a recorded canonical-source read.

    Structured ``read`` tool paths are authoritative. Shell support is limited
    to actual ``cat`` file operands; grep/search strings and other mentions do
    not count as opening a task definition.
    """
    for action in observed_actions:
        if isinstance(action, dict):
            tool = action.get("tool")
            command = action.get("command") if isinstance(tool, str) and tool.casefold() in {"bash", "shell"} else None
        else:
            command = action
        if isinstance(command, str) and start_here_task_invoked(command, task, inspection_only):
            return True
        if _canonical_task_source_read(action, task, workspace, inspection_only):
            return True
    return False


def _validator_modes(repo: Path) -> set[str]:
    """Read the authoritative literal mode keys without importing the CLI."""
    path = repo / VALIDATOR_REL
    if not path.is_file():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "MODES" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return set()
        keys = {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        return keys | {"all"}
    return set()


def _validator_signature(words: list[str], modes: set[str]) -> tuple | None:
    words = _drop_environment_prefix(words)
    if len(words) < 3 or not _is_python(words[0]):
        return None
    script = words[1].removeprefix("./")
    if script != VALIDATOR_SCRIPT:
        return None

    selected_modes: list[str] = []
    options: list[tuple[str, str]] = []
    args = words[2:]
    index = 0
    while index < len(args):
        token = args[index]
        option = token.partition("=")[0]
        if option in {"--root", "--record-dir"}:
            if "=" in token:
                value = token.partition("=")[2]
            else:
                index += 1
                if index >= len(args):
                    return None
                value = args[index]
            if not value or value.startswith("--"):
                return None
            options.append((option, value))
        elif token.startswith("-"):
            return None
        elif token in modes:
            selected_modes.append(token)
        else:
            return None
        index += 1

    if not selected_modes:
        return None
    return ("python", tuple(selected_modes), tuple(options))


def matching_validator_actions(
    claim: str,
    observed_actions: list,
    repo: Path,
    inspection_only: Callable[[str], bool],
    *,
    standalone_only: bool = False,
) -> list[dict]:
    """Return shell actions matching a validator claim.

    Invocation-only callers may match an inner command in a compound shell
    string. Result-proof callers must request a complete, untruncated standalone
    action so the shell's exit metadata belongs to that validator invocation.
    """
    modes = _validator_modes(repo)
    claimed_segments = _command_segments(claim)
    signature = (
        _validator_signature(claimed_segments[0], modes)
        if len(claimed_segments) == 1 and is_standalone_shell_command(claim)
        else None
    )
    if signature is None or not isinstance(observed_actions, list):
        return []
    matches = []
    for action in observed_actions:
        tool = action.get("tool") if isinstance(action, dict) else None
        if not isinstance(tool, str) or tool.casefold() not in {"bash", "shell"}:
            continue
        recorded = action.get("command")
        if not isinstance(recorded, str) or not recorded or inspection_only(recorded):
            continue
        if standalone_only and (
            not is_standalone_shell_command(recorded)
            or action.get("command_truncated") is not False
            or action.get("correlation_conflict", False) is not False
        ):
            continue
        if standalone_only:
            segment = _command_segments(recorded)
            rendered = " ".join(shlex.quote(word) for word in segment[0])
            if inspection_only(rendered):
                continue
            if _validator_signature(segment[0], modes) == signature:
                matches.append(action)
            continue
        for segment in _command_segments(recorded):
            rendered = " ".join(shlex.quote(word) for word in segment)
            if inspection_only(rendered):
                continue
            if _validator_signature(segment, modes) == signature:
                matches.append(action)
                break
    return matches


def validator_command_observed(
    claim: str,
    observed_commands: list[str],
    repo: Path,
    inspection_only: Callable[[str], bool],
) -> tuple[bool, str]:
    """Require a permitted validator claim to match an actual trace command."""
    modes = _validator_modes(repo)
    claimed_segments = _command_segments(claim)
    signature = (
        _validator_signature(claimed_segments[0], modes)
        if len(claimed_segments) == 1 and is_standalone_shell_command(claim)
        else None
    )
    if signature is None:
        return False, "is not a permitted validate_agent_docs.py command"

    for recorded in observed_commands:
        if not recorded or inspection_only(recorded):
            continue
        for segment in _command_segments(recorded):
            rendered = " ".join(shlex.quote(word) for word in segment)
            if inspection_only(rendered):
                continue
            if _validator_signature(segment, modes) == signature:
                return True, "was observed as a permitted validator command in the actual trace"
    return False, "was not observed as the same permitted validator command in the actual trace"
