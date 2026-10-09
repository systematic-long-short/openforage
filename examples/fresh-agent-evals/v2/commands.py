"""Bounded shell-command recognition for v2 evidence and access auditing."""

from __future__ import annotations

from functools import lru_cache
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from onboarding.completion import (  # noqa: E402
    load_fixture_contract,
    parse_route_helper_argv,
)

from schema import VALIDATOR_MODES
from shell_parser import (
    ParsedShellCommand,
    ShellSegment,
    parse_shell_command,
    unwrap_codex_script,
)

PYTHON_RE = re.compile(r"python(?:3(?:\.\d+)?)?\Z")
V2_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])examples/fresh-agent-evals(?:/|\b)")
RESERVED_NAMES = {
    "cases.json", "oracle.json", "answer.json", "answers.json", "grader.py",
    "grade.py", "manifest.py", "series.json",
}
RESERVED_NAME_RE = re.compile(
    r"(?:^|[/\\\s'\"])(?:cases\.json|oracle\.json|answers?\.json|grader\.py|grade\.py|manifest\.py|series\.json)(?:$|[/\\\s'\"])"
)
RESERVED_NAME_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:cases\.json|oracle\.json|answers?\.json|grader\.py|grade\.py|manifest\.py|series\.json)(?![A-Za-z0-9_.-])"
)
_SED_ADDRESS_RE = re.compile(r"(?:\d+|\$)(?:,(?:\d+|\$))?[pP]?\Z")
def unwrap_codex_shell(command: str) -> tuple[str | None, str]:
    """Unwrap a bounded exact Codex login-shell wrapper (maximum depth two)."""
    return unwrap_codex_script(command)


def normalize_command(command: str, harness: str) -> tuple[str | None, str]:
    """Return the inner script only when the bounded shell grammar parses it."""
    parsed = parse_shell_command(command, harness)
    if parsed is None:
        return None, "command is malformed, redacted, or outside the bounded shell grammar"
    return parsed.script.strip(), parsed.wrapper


def command_semantic_key(command: str, harness: str) -> tuple | None:
    """Return complete inner execution identity for claim/native matching.

    Codex completion fields historically record the inner command; native
    actions are separately required to carry the exact shell wrapper.
    """
    parsed = parse_shell_command(command, harness, allow_unwrapped_codex=True)
    return parsed.execution_key if parsed is not None else None


def command_segments(command: str, harness: str) -> tuple[ShellSegment, ...] | None:
    """Return every segment only for a complete, non-truncated shell parse."""
    parsed = parse_shell_command(command, harness)
    return parsed.segments if parsed is not None else None


def _validator_segment_modes(
    segment: ShellSegment, workspace: Path,
) -> tuple[list[str] | None, str | None]:
    words = list(segment.argv)
    if len(words) < 3 or not PYTHON_RE.fullmatch(Path(words[0]).name):
        return None, "segment is not a Python repository-validator invocation"
    if words[1].removeprefix("./") != ".github/scripts/validate_agent_docs.py":
        return None, "segment does not name the repository validator"
    modes: list[str] = []
    index = 2
    seen_options: set[str] = set()
    while index < len(words):
        token = words[index]
        if token in {"--root", "--record-dir"}:
            if token in seen_options:
                return None, "validator option is duplicated"
            seen_options.add(token)
            index += 1
            if index >= len(words):
                return None, "validator option is missing its value"
            value = words[index]
            if token == "--root" and value not in {".", "./"}:
                return None, "validator root must be workspace-relative; absolute paths are not archived"
            if token == "--record-dir" and (
                value.startswith(("/", "<WORKSPACE>")) or ".." in Path(value).parts
            ):
                return None, "validator record directory escapes the workspace"
        elif token.startswith("-"):
            return None, "validator command contains an unsupported option"
        elif token in VALIDATOR_MODES:
            if token in modes:
                return None, "validator mode is duplicated"
            modes.append(token)
        else:
            return None, "validator command contains an unknown argument"
        index += 1
    if not modes:
        return None, "validator command selects no validation mode"
    if "all" in modes and len(modes) != 1:
        return None, "all cannot be combined with individual validator modes"
    return modes, None


def validator_segment_modes(
    segment: ShellSegment, workspace: Path,
) -> tuple[list[str] | None, str | None]:
    """Recognize one complete validator argv segment, never a text substring."""
    return _validator_segment_modes(segment, workspace)


def _python_helper_kind(segment: ShellSegment) -> str | None:
    words = list(segment.argv)
    if not words or not PYTHON_RE.fullmatch(Path(words[0]).name):
        return None
    if len(words) == 4 and words[1:3] == ["-m", "json.tool"] and words[3] == "trial-output/completion.json":
        return "completion-json"
    if len(words) >= 2 and words[1].removeprefix("./") == "scripts/start_here.py":
        invocation = parse_route_helper_argv(words, REPO_ROOT)
        if invocation is None:
            return None
        return "completion-checker" if invocation["check_completion"] else "route-helper"
    if words[1:] in (["--version"], ["--help"]):
        return "python-probe"
    return None


def start_here_task(segment: ShellSegment) -> str | None:
    """Return a task only for the bounded read-only public route-helper CLI."""
    words = list(segment.argv)
    if not words or not PYTHON_RE.fullmatch(Path(words[0]).name):
        return None
    invocation = parse_route_helper_argv(words, REPO_ROOT)
    return invocation["task"] if invocation is not None else None


def start_here_fixture(segment: ShellSegment) -> str | None:
    """Return the canonical fixture id for an explicitly selected task route."""
    invocation = parse_route_helper_argv(list(segment.argv), REPO_ROOT)
    return invocation.get("fixture") if invocation is not None else None


def is_completion_checker_segment(segment: ShellSegment) -> bool:
    invocation = parse_route_helper_argv(list(segment.argv), REPO_ROOT)
    return bool(invocation and invocation["check_completion"])


def is_completion_json_segment(segment: ShellSegment) -> bool:
    return _python_helper_kind(segment) == "completion-json"


def is_current_branch_segment(segment: ShellSegment) -> bool:
    return list(segment.argv) == ["git", "branch", "--show-current"]


def _simple_sed_operands(args: list[str]) -> tuple[list[str], bool]:
    """Recognize only a simple sed address/range followed by file operands."""
    scripts: list[str] = []
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
    if len(scripts) != 1 or _SED_ADDRESS_RE.fullmatch(scripts[0]) is None:
        return [], False
    operands = args[index:]
    if not positional:
        operands = [item for item in operands if not item.startswith("-")]
    return operands, True


def classify_shell_segment(segment: ShellSegment, workspace: Path) -> str | None:
    """Classify one segment from the explicitly admitted local command set."""
    validator_modes, _error = _validator_segment_modes(segment, workspace)
    if validator_modes is not None:
        return "validator"

    helper_kind = _python_helper_kind(segment)
    if helper_kind is not None:
        return helper_kind

    words = list(segment.argv)
    if not words:
        return None
    tool = Path(words[0]).name.casefold()
    args = words[1:]
    if is_current_branch_segment(segment):
        return "branch-observation"
    git_kind = _git_read_only(segment)
    if git_kind is not None:
        return git_kind
    inspection_kind = _inspection_kind(segment)
    if inspection_kind is not None:
        return inspection_kind
    if tool in {"true", "false"} and not args:
        return "no-op"
    return None


def _mentions_suite_material(value: str) -> bool:
    # Preserve the reserved-source audit across shell quote concatenation and
    # escaped characters; parsing remains the authority for admitting syntax.
    compact = value.replace("'", "").replace('"', "").replace("\\", "")
    return bool(
        V2_PATH_RE.search(compact)
        or RESERVED_NAME_RE.search(compact)
        or RESERVED_NAME_TOKEN_RE.search(compact)
    )


def _mentions_suite_argument(value: str) -> bool:
    """Match suite names in one shell word after quote/escape normalization."""
    return _mentions_suite_material(value) or Path(value.strip()).name in RESERVED_NAMES


def _segment_mentions_suite_material(segment: ShellSegment) -> bool:
    return _mentions_suite_material(segment.source) or any(
        _mentions_suite_argument(word) for word in segment.argv
    )


def _safe_path_words(words: list[str]) -> bool:
    return bool(words) and all(
        isinstance(value, str)
        and value not in {"-", "<REDACTED>", "<HOME>", "<REPO>"}
        and not value.startswith(("/", "~", "$"))
        and ".." not in Path(value).parts
        and "\\" not in value
        for value in words
    )


LS_SHORT_NO_ARGUMENT_FLAGS = frozenset("aA1dhlR")
LS_LONG_NO_ARGUMENT_FLAGS = frozenset({
    "--all", "--almost-all", "--human-readable", "--recursive",
})


def _ls_paths(args: list[str]) -> list[str] | None:
    """Parse only the already-admitted no-argument ``ls`` flags and paths."""
    paths: list[str] = []
    positional = False
    for token in args:
        if not positional and token == "--":
            positional = True
            continue
        if not positional and token.startswith("--"):
            if token not in LS_LONG_NO_ARGUMENT_FLAGS:
                return None
            continue
        if not positional and token.startswith("-"):
            if len(token) < 2 or any(
                flag not in LS_SHORT_NO_ARGUMENT_FLAGS for flag in token[1:]
            ):
                return None
            continue
        paths.append(token)
    if paths and not _safe_path_words(paths):
        return None
    return paths


def _ls_listing_semantics(args: list[str]) -> dict[str, bool]:
    """Retain bounded ls flags that change the meaning of the returned names."""
    positional = False
    recursive = False
    directory_operands = False
    for token in args:
        if not positional and token == "--":
            positional = True
        elif positional or not token.startswith("-"):
            continue
        elif token == "--recursive":
            recursive = True
        elif token.startswith("--"):
            continue
        else:
            recursive = recursive or "R" in token[1:]
            directory_operands = directory_operands or "d" in token[1:]
    result = {}
    if recursive:
        result["recursive"] = True
    if directory_operands:
        result["directory_operands"] = True
    return result


def _read_search_is_bounded(tool: str, args: list[str]) -> bool:
    """Recognize common content searches without reader-program options."""
    pattern_seen = False
    paths: list[str] = []
    index = 0
    value_options = {"-e", "--regexp"}
    flags = {
        "-n", "-i", "-F", "-E", "-w", "-x", "-v", "-m",
        "--line-number", "--ignore-case", "--fixed-strings", "--extended-regexp",
        "--word-regexp", "--count", "--files-with-matches", "--no-heading",
    }
    if tool in {"rg", "grep", "egrep", "fgrep"}:
        flags.update({"-r", "-R", "--recursive"})
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            if not pattern_seen:
                if index >= len(args):
                    return False
                pattern_seen = True
                index += 1
            paths.extend(args[index:])
            break
        if token in value_options:
            if pattern_seen or index + 1 >= len(args):
                return False
            pattern_seen = True
            index += 2
            continue
        if token.startswith("--regexp="):
            if pattern_seen or not token.split("=", 1)[1]:
                return False
            pattern_seen = True
            index += 1
            continue
        if token in flags:
            index += 1
            continue
        if token.startswith("-"):
            # Attached short-form pattern options are limited to -ePATTERN.
            if token.startswith("-e") and len(token) > 2 and not pattern_seen:
                pattern_seen = True
                index += 1
                continue
            return False
        if not pattern_seen:
            pattern_seen = True
        else:
            paths.append(token)
        index += 1
    return pattern_seen and bool(paths) and _safe_path_words(paths)


def _safe_discovery_glob(value: str) -> bool:
    pattern = value[1:] if value.startswith("!") else value
    return bool(
        pattern
        and len(pattern.encode("utf-8")) <= 2048
        and not pattern.startswith(("/", "~", "$"))
        and "\\" not in pattern
        and "\0" not in pattern
        and ".." not in Path(pattern).parts
    )


def _rg_files_scope(args: list[str]) -> dict | None:
    """Parse only bounded ``rg --files`` path and glob operands.

    Globs are retained in encounter order and split by polarity. Positive
    globs after exclusions are refused because ripgrep applies glob rules in
    order and that form needs a fuller matcher than this access auditor has.
    """
    paths: list[str] = []
    positive: list[str] = []
    excluded: list[str] = []
    index = 0
    files_seen = False
    exclusion_seen = False
    positional_only = False
    while index < len(args):
        token = args[index]
        if not positional_only and token == "--":
            positional_only = True
            index += 1
            continue
        if not positional_only and token == "--files":
            if files_seen:
                return None
            files_seen = True
            index += 1
            continue
        pattern = None
        if not positional_only and token in {"-g", "--glob"}:
            if index + 1 >= len(args):
                return None
            pattern = args[index + 1]
            index += 2
        elif not positional_only and token.startswith("--glob="):
            pattern = token.split("=", 1)[1]
            index += 1
        elif not positional_only and token.startswith("-g") and len(token) > 2:
            pattern = token[2:]
            index += 1
        elif not positional_only and token.startswith("-"):
            return None
        else:
            if not _safe_path_words([token]) or len(paths) >= 16:
                return None
            paths.append(token)
            index += 1
            continue

        if not _safe_discovery_glob(pattern) or len(positive) + len(excluded) >= 16:
            return None
        if pattern.startswith("!"):
            exclusion_seen = True
            excluded.append(pattern[1:])
        else:
            if exclusion_seen:
                return None
            positive.append(pattern)

    if not files_seen or len(set(paths)) != len(paths):
        return None
    if len(set(positive)) != len(positive) or len(set(excluded)) != len(excluded):
        return None
    return {
        "paths": paths or ["."],
        "positive_globs": positive,
        "excluded_globs": excluded,
    }


def filename_discovery_scope(segment: ShellSegment, workspace: Path) -> dict | None:
    """Return safe, polarity-aware filename-discovery scope for bounded tools."""
    words = list(segment.argv)
    if not words:
        return None
    tool = Path(words[0]).name.casefold()
    args = words[1:]
    if tool == "rg":
        return _rg_files_scope(args)
    if tool == "ls":
        paths = _ls_paths(args)
        if paths is None:
            return None
        return {
            "paths": paths or ["."], "positive_globs": [], "excluded_globs": [],
            **_ls_listing_semantics(args),
        }
    if tool == "git" and args[:1] == ["ls-files"]:
        allowed = {"--cached", "--deleted", "--modified", "--others", "--stage", "--full-name", "--error-unmatch"}
        paths: list[str] = []
        positional = False
        for token in args[1:]:
            if token == "--" and not positional:
                positional = True
            elif token.startswith("-") and not positional:
                if token not in allowed:
                    return None
            else:
                paths.append(token)
        if paths and not _safe_path_words(paths):
            return None
        return {"paths": paths or ["."], "positive_globs": [], "excluded_globs": []}
    return None


def _git_read_only(segment: ShellSegment) -> str | None:
    words = list(segment.argv)
    if not words or Path(words[0]).name.casefold() != "git" or len(words) < 2:
        return None
    subcommand, args = words[1], words[2:]
    if subcommand == "diff":
        allowed_flags = {"--check", "--stat", "--shortstat", "--name-only", "--name-status", "--numstat"}
        paths: list[str] = []
        positional = False
        for token in args:
            if token == "--":
                positional = True
                continue
            if token.startswith("-") and not positional:
                if token not in allowed_flags:
                    return None
            else:
                paths.append(token)
        if paths and not _safe_path_words(paths):
            return None
        return "git-read-only"
    if subcommand == "status":
        allowed_flags = {"--short", "--porcelain", "--branch", "--untracked-files=no", "--ignored"}
        paths: list[str] = []
        positional = False
        for token in args:
            if token == "--":
                positional = True
            elif token.startswith("-") and not positional:
                if token not in allowed_flags:
                    return None
            else:
                paths.append(token)
        return "git-read-only" if not paths or _safe_path_words(paths) else None
    if subcommand == "rev-parse":
        return "git-read-only" if args in ([], ["--show-toplevel"], ["--show-prefix"], ["--is-inside-work-tree"]) else None
    if subcommand == "ls-files":
        allowed_flags = {"--cached", "--deleted", "--modified", "--others", "--stage", "--full-name", "--error-unmatch"}
        paths: list[str] = []
        positional = False
        for token in args:
            if token == "--":
                positional = True
            elif token.startswith("-") and not positional:
                if token not in allowed_flags:
                    return None
            else:
                paths.append(token)
        if paths and not _safe_path_words(paths):
            return None
        return "git-read-only"
    if subcommand in {"show", "log"}:
        if any(token in {"--output", "--ext-diff", "--no-ext-diff", "--textconv"} for token in args):
            return None
        return "git-read-only"
    return None


def _inspection_kind(segment: ShellSegment) -> str | None:
    words = list(segment.argv)
    if not words:
        return None
    tool = Path(words[0]).name.casefold()
    args = words[1:]
    if tool == "cat":
        paths = args[1:] if args[:1] == ["--"] else args
        return "inspection" if paths and not any(value.startswith("-") for value in paths) else None
    if tool in {"head", "tail"}:
        index = 0
        count = None
        if args[:1] in (["-n"], ["--lines"]):
            if len(args) < 2 or not args[1].isdigit():
                return None
            count, index = int(args[1]), 2
        elif args and (match := re.fullmatch(r"(?:-n|--lines=)(\d+)", args[0])):
            count, index = int(match.group(1)), 1
        elif args and (match := re.fullmatch(r"-(\d+)", args[0])):
            count, index = int(match.group(1)), 1
        if count is not None and not 1 <= count <= 200:
            return None
        if args[index:index + 1] == ["--"]:
            index += 1
        paths = args[index:]
        return "inspection" if len(paths) == 1 and _safe_path_words(paths) else None
    if tool == "sed":
        _operands, valid = _simple_sed_operands(args)
        return "inspection" if valid else None
    if tool in {"grep", "rg", "egrep", "fgrep"}:
        if tool == "rg" and _rg_files_scope(args) is not None:
            return "inspection"
        return "inspection" if _read_search_is_bounded(tool, args) else None
    if tool == "ls":
        return "inspection" if _ls_paths(args) is not None else None
    if tool in {"wc", "uniq", "tr", "cut", "nl"}:
        # These bounded text utilities have no output-file option in this grammar.
        if any(token in {"-o", "--output", "--output-file"} for token in args):
            return None
        return "inspection" if all(not token.startswith("-") or token in {"-l", "-w", "-c", "-m", "-L", "-s", "-d", "-f", "-b", "-n"} for token in args) else None
    if tool == "sort":
        # GNU sort's -o/--output options mutate files; admit only plain file operands.
        paths = args[1:] if args[:1] == ["--"] else args
        return "inspection" if paths and not any(value.startswith("-") for value in paths) else None
    if tool == "pwd":
        return "inspection" if args in ([], ["-P"], ["-L"]) else None
    return None


def validator_modes(
    command: str, workspace: Path, harness: str,
) -> tuple[list[str] | None, str | None]:
    """Recognize one or more validator segments in a fully admitted AND chain."""
    parsed = parse_shell_command(command, harness, allow_unwrapped_codex=True)
    if parsed is None:
        return None, "command is not a complete bounded shell invocation"
    validators: list[tuple[int, list[str]]] = []
    for index, segment in enumerate(parsed.segments):
        modes, _error = _validator_segment_modes(segment, workspace)
        if modes is not None:
            validators.append((index, modes))
    for index, segment in enumerate(parsed.segments):
        if _segment_mentions_suite_material(segment):
            return None, "command directly references evaluation-suite material"
        if classify_shell_segment(segment, workspace) is None:
            return None, "AND chain contains an unclassified command segment"
    if not validators:
        return None, "command contains no complete repository-validator segment"
    modes = [mode for _index, segment_modes in validators for mode in segment_modes]
    if len(modes) != len(set(modes)):
        return None, "AND chain repeats a repository-validator mode"
    if "all" in modes and len(modes) != 1:
        return None, "all cannot be combined with another validator invocation"
    return modes, None


def is_relevant_modes(modes: list[str] | None) -> bool:
    return isinstance(modes, list) and ("all" in modes or "truthfulness" in modes)


def is_completion_json_helper(command: str, harness: str) -> bool:
    """Recognize the exact, standalone local JSON-artifact inspection command."""
    parsed = parse_shell_command(command, harness)
    return parsed is not None and len(parsed.segments) == 1 and is_completion_json_segment(parsed.segments[0])


def is_current_branch_observation(command: str, harness: str) -> bool:
    """Recognize only the exact read-only current-branch query."""
    parsed = parse_shell_command(command, harness)
    return parsed is not None and len(parsed.segments) == 1 and is_current_branch_segment(parsed.segments[0])


def _broad_suite_search_segment(segment: ShellSegment) -> bool:
    words = list(segment.argv)
    if not words:
        return False
    tool = Path(words[0]).name
    args = words[1:]
    recursive = tool == "rg" or tool in {"grep", "egrep", "fgrep"} and any(
        option in args for option in ("-r", "-R", "--recursive")
    ) or tool == "ls" and any(option in args for option in ("-R", "--recursive"))
    if not recursive and tool != "find":
        return False
    positional = [item for item in args if not item.startswith("-")]
    if tool in {"rg", "grep", "egrep", "fgrep"}:
        scopes = positional[1:]
    elif tool == "find":
        scopes = positional[:1] or ["."]
    else:
        scopes = positional or ["."]
    return not scopes or any(
        scope in {".", "./", "examples", "./examples"}
        or scope.startswith("examples/fresh-agent-evals")
        for scope in scopes
    )


def _broad_suite_search(command: str, harness: str) -> bool:
    parsed = parse_shell_command(command, harness)
    return parsed is not None and any(
        _broad_suite_search_segment(segment) for segment in parsed.segments
    )


@lru_cache(maxsize=1)
def _docs_fixture_inputs() -> frozenset[str]:
    """Return only the paths disclosed by the canonical docs-fixture contract."""
    try:
        contract = load_fixture_contract(
            REPO_ROOT, "contribute", "first-finished-contribution-primary",
        )
    except Exception:
        return frozenset()
    inputs = contract.get("available_input_paths")
    if not isinstance(inputs, list):
        return frozenset()
    return frozenset(path for path in inputs if isinstance(path, str))


def _regular_disclosed_input(path_text: str, workspace: Path) -> bool:
    """Require an exact disclosed path to resolve to a regular, non-symlink file."""
    if path_text not in _docs_fixture_inputs():
        return False
    path = Path(path_text)
    if (
        path.is_absolute()
        or path.as_posix() != path_text
        or "\\" in path_text
        or ".." in path.parts
        or "." in path.parts
        or any(part.startswith(("<", "~", "$")) for part in path.parts)
    ):
        return False
    try:
        absolute_workspace = workspace.absolute()
        current = Path(absolute_workspace.anchor)
        for part in absolute_workspace.parts[1:]:
            current = current / part
            if current.is_symlink():
                return False
        root = workspace.resolve(strict=True)
        candidate = workspace
        for part in path.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                return False
        resolved = candidate.resolve(strict=True)
        return candidate.is_file() and resolved.is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _native_disclosed_read(action: dict, workspace: Path) -> bool:
    """Admit one unambiguous native read of an exact disclosed fixture input."""
    if action.get("tool") != "read":
        return False
    if (
        "native_arguments" in action
        and action.get("native_arguments_status") != "observed"
    ):
        return False
    values = [
        action[key]
        for key in ("command", "path", "file_path", "filePath")
        if key in action
    ]
    arguments = action.get("native_arguments")
    if arguments is not None:
        if (
            not isinstance(arguments, dict)
            or arguments.get("tool") != "read"
            or not {"tool", "path"} <= set(arguments)
            or set(arguments) - {"tool", "path", "offset", "limit"}
        ):
            return False
        values.append(arguments.get("path"))
    elif action.get("native_arguments_status") == "observed":
        return False
    if not values or any(not isinstance(value, str) for value in values):
        return False
    if len(set(values)) != 1:
        return False
    path_text = values[0]
    return _regular_disclosed_input(path_text, workspace)


def _shell_disclosed_read(segment: ShellSegment, workspace: Path) -> bool:
    """Admit bounded, plainly-spelled shell reads of only disclosed input files."""
    words = list(segment.argv)
    if not words or any(char in segment.source for char in "'\"\\"):
        return False
    tool = Path(words[0]).name.casefold()
    args = words[1:]
    if tool == "cat":
        paths = args[1:] if args[:1] == ["--"] else args
        if not paths or any(value.startswith("-") for value in paths):
            return False
    elif tool in {"head", "tail"}:
        index = 0
        if args[:1] in (["-n"], ["--lines"]):
            if len(args) < 2 or not args[1].isdigit():
                return False
            index = 2
        elif args and re.fullmatch(r"(?:-n|--lines=)\d+", args[0]):
            index = 1
        elif args and re.fullmatch(r"-\d+", args[0]):
            index = 1
        if args[index:index + 1] == ["--"]:
            index += 1
        paths = args[index:]
        if len(paths) != 1:
            return False
    elif tool == "sed":
        paths, classified = _simple_sed_operands(args)
        if not classified:
            return False
    else:
        return False
    if (
        classify_shell_segment(segment, workspace) != "inspection"
        or not _safe_path_words(paths)
        or any(not _regular_disclosed_input(path, workspace) for path in paths)
    ):
        return False
    return True


def _filename_discovery_segment(segment: ShellSegment) -> bool:
    return filename_discovery_scope(segment, Path(".")) is not None


def direct_suite_access(action: dict, harness: str, workspace: Path) -> bool:
    """Flag protected suite access while honoring exact disclosed input reads."""
    tool = action.get("tool")
    command = action.get("command")
    path = action.get("path")
    if tool in {"bash", "shell", "command_execution"} and isinstance(command, str):
        parsed = parse_shell_command(command, harness)
        if parsed is not None:
            for segment in parsed.segments:
                if _filename_discovery_segment(segment):
                    continue
                if _broad_suite_search_segment(segment):
                    return True
                if _shell_disclosed_read(segment, workspace):
                    continue
                if _segment_mentions_suite_material(segment):
                    return True
            return False

    if tool in {"glob", "list"}:
        # These native tools expose filenames only. Their bounded argument
        # validity is checked by the access audit; discovery is not content access.
        return False
    if _native_disclosed_read(action, workspace):
        return False
    candidates = [item for item in (command, path, action.get("file_path")) if isinstance(item, str)]
    arguments = action.get("native_arguments")
    if tool in {"read", "grep"} and isinstance(arguments, dict):
        native_path = arguments.get("path")
        if isinstance(native_path, str):
            candidates.append(native_path)
    for value in candidates:
        if _mentions_suite_argument(value):
            return True
    return False
