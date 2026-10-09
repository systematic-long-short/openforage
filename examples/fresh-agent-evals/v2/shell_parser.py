"""Bounded parser for the shell command shapes admitted by v2 evidence.

This is deliberately not a general shell interpreter. It recognizes one
direct OpenCode script or an exact Codex ``bash|sh -lc SCRIPT`` wrapper with
at most one nested login-shell wrapper, simple argv segments, the harmless
bytecode environment setting, and quote-aware ``&&`` chains. Every other
shell control form stays unparsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

MAX_COMMAND_BYTES = 8192
MAX_WRAPPER_DEPTH = 2
MAX_SHELL_SEGMENTS = 8
MAX_SEGMENT_WORDS = 64
MAX_ARGUMENT_BYTES = 4096
ALLOWED_ENVIRONMENT = {"PYTHONDONTWRITEBYTECODE": "1"}
ASSIGNMENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z", re.DOTALL)
DIRECT_ASSIGNMENT_RE = re.compile(
    r"\s*(PYTHONDONTWRITEBYTECODE=1)(?=\s|$)"
)
REDACTED_MARKERS = ("<REDACTED>", "<HOME>", "<REPO>")
SHELL_EXECUTABLES = {
    "bash", "sh", "/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh",
}


@dataclass(frozen=True)
class ShellSegment:
    """One parsed simple command, with only validated environment prefixes."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    source: str


@dataclass(frozen=True)
class ParsedShellCommand:
    """The complete parsed command and its native wrapper identity."""

    harness: str
    wrapper: str
    wrapper_argv: tuple[str, ...]
    script: str
    segments: tuple[ShellSegment, ...]
    operators: tuple[str, ...]
    wrapper_depth: int

    @property
    def semantic_key(self) -> tuple:
        """Return the complete argv/control representation, never a substring."""
        return (
            self.harness,
            self.wrapper,
            self.wrapper_argv,
            self.wrapper_depth,
            self.execution_key,
        )

    @property
    def execution_key(self) -> tuple:
        """Return all simple-command argv/environment pairs and AND operators."""
        return (
            tuple((segment.environment, segment.argv) for segment in self.segments),
            self.operators,
        )


def _exact_login_wrapper(command: str) -> tuple[str, tuple[str, ...]] | None:
    """Parse one exact, control-free bash/sh ``-lc`` argv wrapper."""
    if not isinstance(command, str) or not command.strip() or not _outer_wrapper_control_free(command):
        return None
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    if (
        len(words) != 3
        or words[0] not in SHELL_EXECUTABLES
        or words[1] != "-lc"
        or not words[2].strip()
    ):
        return None
    return words[2], (words[0], words[1])


def _codex_script(command: str) -> tuple[str | None, tuple[str, ...], str, int]:
    """Unwrap Codex's login shell and at most one nested login shell."""
    if not isinstance(command, str) or not command.strip():
        return None, (), "Codex shell wrapper is missing or malformed", 0
    outer = _exact_login_wrapper(command)
    if outer is None:
        return None, (), "Codex command is not an exact control-free bash/sh -lc wrapper", 0
    script, outer_argv = outer
    nested = _exact_login_wrapper(script)
    if nested is None:
        return script, outer_argv, "exact-codex-login-shell", 1

    nested_script, nested_argv = nested
    if _exact_login_wrapper(nested_script) is not None:
        return None, (), "Codex shell wrapper depth exceeds the bound of two", 0
    return (
        nested_script,
        (*outer_argv, "<nested-login-shell>", *nested_argv),
        "exact-codex-nested-login-shell",
        MAX_WRAPPER_DEPTH,
    )


def _outer_wrapper_control_free(command: str) -> bool:
    """Reject shell syntax outside a Codex wrapper's quoted argv words."""
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if char in "\0\r\n":
            return False
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                if index + 1 >= len(command) or command[index + 1] == "\n":
                    return False
                if command[index + 1] in '$`"\\':
                    index += 2
                else:
                    index += 1
                continue
            if char in "$`":
                return False
            if char == '"':
                quote = None
            index += 1
            continue
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            if index + 1 >= len(command) or command[index + 1] == "\n":
                return False
            escaped = True
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char in ";&|<>(){}!$`#*?[~":
            return False
        index += 1
    return quote is None and not escaped


def unwrap_codex_script(command: str) -> tuple[str | None, str]:
    """Public compatibility helper for the exact supported Codex wrapper."""
    script, _wrapper_argv, kind, _depth = _codex_script(command)
    return script, kind


def _split_and_chain(script: str) -> tuple[list[str], list[str]] | None:
    """Split only unquoted ``&&`` operators and reject other shell syntax."""
    segments: list[str] = []
    operators: list[str] = []
    quote: str | None = None
    escaped = False
    start = 0
    index = 0

    while index < len(script):
        char = script[index]
        if char in "\0\r\n":
            return None

        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue

        if quote == '"':
            if char == "\\":
                if index + 1 >= len(script) or script[index + 1] == "\n":
                    return None
                next_char = script[index + 1]
                if next_char in '$`"\\':
                    index += 2
                else:
                    index += 1
                continue
            if char in "$`":
                return None
            if char == '"':
                quote = None
            index += 1
            continue

        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            if index + 1 >= len(script) or script[index + 1] == "\n":
                return None
            escaped = True
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char in "$`":
            return None
        if char == "#":
            # Comments need shell token-position semantics. They are outside
            # the bounded grammar rather than being partially interpreted.
            return None
        if char == "<" and script.startswith("<WORKSPACE>", index):
            # Native adapters normalize the trial's absolute workspace path to
            # this explicit, non-shell placeholder before storing commands.
            index += len("<WORKSPACE>")
            continue
        if char in ";|<>(){}!":
            return None
        if char in "*?[~":
            # Reject pathname expansion and home expansion; quoted literals
            # retain their ordinary argv meaning.
            return None
        if char == "]":
            return None
        if char == "&":
            if index + 1 >= len(script) or script[index + 1] != "&":
                return None
            if index + 2 < len(script) and script[index + 2] == "&":
                return None
            segment = script[start:index].strip()
            if not segment:
                return None
            segments.append(segment)
            operators.append("&&")
            if len(segments) >= MAX_SHELL_SEGMENTS:
                return None
            index += 2
            start = index
            continue
        index += 1

    if quote is not None or escaped:
        return None
    final = script[start:].strip()
    if not final:
        return None
    segments.append(final)
    if len(segments) > MAX_SHELL_SEGMENTS:
        return None
    return segments, operators


def _allowed_assignment(word: str) -> tuple[str, str] | None:
    match = ASSIGNMENT_RE.fullmatch(word)
    if match is None:
        return None
    name, value = match.groups()
    if ALLOWED_ENVIRONMENT.get(name) != value:
        return None
    return name, value


def _parse_segment(source: str) -> ShellSegment | None:
    environment: list[tuple[str, str]] = []
    text = source.strip()
    if not text:
        return None

    direct_prefix = DIRECT_ASSIGNMENT_RE.match(text)
    if direct_prefix is not None:
        environment.append(("PYTHONDONTWRITEBYTECODE", "1"))
        text = text[direct_prefix.end():].lstrip()
        if not text:
            return None

    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not words or len(words) > MAX_SEGMENT_WORDS:
        return None
    try:
        if any(len(word.encode("utf-8")) > MAX_ARGUMENT_BYTES for word in words):
            return None
    except UnicodeEncodeError:
        return None

    if Path(words[0]).name == "env":
        index = 1
        while index < len(words) and "=" in words[index]:
            assignment = _allowed_assignment(words[index])
            if assignment is None or assignment[0] in {name for name, _value in environment}:
                return None
            environment.append(assignment)
            index += 1
        if not environment or index >= len(words):
            return None
        words = words[index:]
    elif ASSIGNMENT_RE.fullmatch(words[0]):
        # Shell assignment words are admitted only in the exact unquoted form
        # recognized above. Quoted or arbitrary assignments are ambiguous.
        return None

    if not words:
        return None
    return ShellSegment(tuple(words), tuple(environment), source.strip())


def parse_shell_command(
    command: object, harness: str, *, allow_unwrapped_codex: bool = False,
) -> ParsedShellCommand | None:
    """Parse the small shell grammar shared by v2 recognition and evidence.

    ``command`` must be the full, unredacted, non-truncated native command.
    OpenCode supplies the shell script directly. Codex supplies one exact
    ``bash|sh -lc SCRIPT`` wrapper, optionally containing one exact nested
    login-shell wrapper. Wrapper depth is at most two, chains at most eight
    segments, segments at most 64 argv words, and each word at most 4096 bytes.
    Every simple command is parsed with POSIX quote handling; only unquoted
    ``&&`` joins segments.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        encoded = command.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > MAX_COMMAND_BYTES or any(marker in command for marker in REDACTED_MARKERS):
        return None
    if any(char in command for char in "\0\r\n"):
        return None

    if harness == "opencode":
        script = command
        wrapper = "direct-opencode-shell"
        wrapper_argv: tuple[str, ...] = ()
        wrapper_depth = 0
    elif harness == "codex":
        script, wrapper_argv, wrapper, wrapper_depth = _codex_script(command)
        if script is None:
            if not allow_unwrapped_codex:
                return None
            script = command
            wrapper_argv = ()
            wrapper = "codex-declared-inner-command"
            wrapper_depth = 0
    else:
        return None

    split = _split_and_chain(script)
    if split is None:
        return None
    raw_segments, operators = split
    parsed_segments = tuple(_parse_segment(segment) for segment in raw_segments)
    if any(segment is None for segment in parsed_segments):
        return None
    segments = tuple(segment for segment in parsed_segments if segment is not None)
    if len(operators) != len(segments) - 1:
        return None
    return ParsedShellCommand(
        harness=harness,
        wrapper=wrapper,
        wrapper_argv=wrapper_argv,
        script=script,
        segments=segments,
        operators=tuple(operators),
        wrapper_depth=wrapper_depth,
    )
