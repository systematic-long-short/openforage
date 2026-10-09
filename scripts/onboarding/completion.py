"""Canonical fixture contracts and an offline, read-only completion check.

This module is deliberately independent of the evaluation graders. It reads
only the disclosed onboarding contract, declared inputs and deliverables, and
the public artifact schemas. Fixed validator functions are scoped to disclosed
inputs; command strings from JSON are validated and displayed, never executed.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import posixpath
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

ONBOARDING_REL = "docs/agents/references/onboarding.json"
V2_COMPLETION_SCHEMA_REL = "examples/fresh-agent-evals/v2/completion.schema.json"
RESEARCH_RESULT_SCHEMA_REL = "examples/fresh-agent-evals/research/research-result.schema.json"
RESEARCH_REPORT_SCHEMA_REL = "examples/fresh-agent-evals/research/comparison-report.schema.json"
SCRATCH_REL = ".tmp/completion-context/notes.md"
TASK_SERIES_DIRS = {
    "contribute": "examples/fresh-agent-evals/v2/series",
    "research": "examples/fresh-agent-evals/research/series",
}
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SCHEMA_DEPTH = 96
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SCHEMA_CONSTRAINT_KEYWORDS = frozenset({
    "$ref", "type", "const", "enum", "required", "properties",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "format", "minimum", "maximum",
})
SCHEMA_CONTAINER_KEYWORDS = frozenset({"$defs", "definitions"})
SCHEMA_ANNOTATION_KEYWORDS = frozenset({
    "$schema", "$id", "$comment", "title", "description", "default",
    "examples", "deprecated", "readOnly", "writeOnly",
})
SCHEMA_KEYWORDS = (
    SCHEMA_CONSTRAINT_KEYWORDS | SCHEMA_CONTAINER_KEYWORDS | SCHEMA_ANNOTATION_KEYWORDS
)
SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})

DOCS_CHECK_IDS = (
    "docs-baseline-validation",
    "docs-final-validation",
    "docs-git-diff-check",
    "docs-completion-check",
)
RESEARCH_CHECK_IDS = (
    "research-development-evaluation",
    "research-git-diff-check",
    "research-completion-check",
)
CONTRACT_KEYS = {
    "contract_version",
    "available_input_paths",
    "deliverables",
    "scratch_paths",
    "fixed_finish_check_ids",
    "finish_sequence",
    "permitted_finish_commands",
}


class CompletionError(ValueError):
    """A fixture contract, selected path, or completion artifact is invalid."""


class _MissingPath(CompletionError):
    pass


class _UnsafePath(CompletionError):
    pass


def _safe_parts(value: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or value.startswith("/")
        or ":" in value
    ):
        raise _UnsafePath("path is not a safe canonical repository-relative path")
    parsed = PurePosixPath(value)
    if (
        not parsed.parts
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise _UnsafePath("path is not a safe canonical repository-relative path")
    return parsed.parts


def _root_path(root: Path) -> Path:
    supplied = Path(root)
    if not supplied.is_absolute():
        supplied = Path.cwd() / supplied
    try:
        if supplied.is_symlink():
            raise _UnsafePath("workspace root is a symlink")
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise _MissingPath("workspace root is unavailable") from exc
    if not resolved.is_dir():
        raise _UnsafePath("workspace root is not a directory")
    return resolved


def _open_root_fd(root: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise _UnsafePath("workspace root could not be opened safely") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _UnsafePath("workspace root is not a regular directory")
    return descriptor


def _open_dir_fd(root: Path, parts: tuple[str, ...]) -> int:
    descriptor = _open_root_fd(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in parts:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError as exc:
                raise _MissingPath("declared directory is missing") from exc
            except OSError as exc:
                raise _UnsafePath("declared path traverses a symlink or non-directory") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_file_fd(root: Path, relative: str) -> int:
    parts = _safe_parts(relative)
    parent = _open_dir_fd(root, parts[:-1])
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=parent)
        except FileNotFoundError as exc:
            raise _MissingPath("declared file is missing") from exc
        except OSError as exc:
            raise _UnsafePath("declared file is a symlink or cannot be opened safely") from exc
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _UnsafePath("declared path is not a regular file")
    return descriptor


def _read_safe_bytes(root: Path, relative: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    root = _root_path(root)
    descriptor = _open_file_fd(root, relative)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(data) > maximum:
        raise CompletionError("declared file exceeds the bounded read size")
    return data


def _probe_safe_file(root: Path, relative: str) -> None:
    root = _root_path(root)
    descriptor = _open_file_fd(root, relative)
    os.close(descriptor)


def _strict_json_loads(data: bytes | str) -> Any:
    text = data.decode("utf-8") if isinstance(data, bytes) else data

    def object_no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CompletionError("JSON object contains a duplicate field")
            result[key] = value
        return result

    def reject_constant(_value):
        raise CompletionError("JSON contains a non-standard numeric constant")

    try:
        return json.loads(
            text,
            object_pairs_hook=object_no_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise CompletionError("JSON is malformed or is not UTF-8") from exc


def read_json_safe(root: Path, relative: str) -> Any:
    return _strict_json_loads(_read_safe_bytes(root, relative))


def series_directory(task_id: str) -> str:
    try:
        return TASK_SERIES_DIRS[task_id]
    except KeyError as exc:
        raise CompletionError("this task has no selectable series-manifest directory") from exc


def validate_series_manifest_path(task_id: str, value: str) -> str:
    parts = _safe_parts(value)
    expected_parent = _safe_parts(series_directory(task_id))
    if (
        parts[:-1] != expected_parent
        or PurePosixPath(value).suffix != ".json"
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}\.json", parts[-1]) is None
    ):
        raise _UnsafePath("series manifest must be one exact named JSON file beneath this task's series directory")
    return value


def _selected_manifest_versions(root: Path, task_id: str) -> tuple[int, int]:
    """Read version authority from the existing task schema/path definition.

    Research publishes its manifest schema beside the task series directory.
    The contribution task uses the versioned v2 series directory as its
    manifest-format definition. This reads no frozen grading source or map.
    """
    series_dir = PurePosixPath(series_directory(task_id))
    if task_id == "research":
        schema_path = (series_dir.parent / "series.schema.json").as_posix()
        try:
            schema = read_json_safe(root, schema_path)
        except (CompletionError, OSError) as exc:
            raise CompletionError("canonical research series schema is unavailable or malformed") from exc
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict):
            raise CompletionError("canonical research series schema has no property definitions")
        versions = []
        for name in ("series_version", "manifest_schema_version"):
            definition = properties.get(name)
            value = definition.get("const") if isinstance(definition, dict) else None
            if type(value) is not int or value < 1:
                raise CompletionError("canonical research series schema has malformed version constants")
            versions.append(value)
        return versions[0], versions[1]

    # The contribution task's established definition is its vN series folder;
    # both version fields in that v2 format are the directory's N.
    version_segments = [
        match.group(1)
        for part in series_dir.parts
        if (match := re.fullmatch(r"v([1-9][0-9]*)", part)) is not None
    ]
    if task_id != "contribute" or len(version_segments) != 1:
        raise CompletionError("task has no authoritative selected-manifest version definition")
    version = int(version_segments[0])
    return version, version


def _expected_commands(task_id: str, fixture_id: str) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    if task_id == "contribute":
        ids = DOCS_CHECK_IDS
        sequence = (
            "docs-baseline-validation",
            "edit-source-deliverable",
            "docs-final-validation",
            "write-completion-artifact",
            "docs-git-diff-check",
            "docs-completion-check",
        )
        commands = {
            "docs-baseline-validation": "python .github/scripts/validate_agent_docs.py truthfulness links --root .",
            "docs-final-validation": "python .github/scripts/validate_agent_docs.py truthfulness links --root .",
            "docs-git-diff-check": "git diff --check",
            "docs-completion-check": (
                f"python scripts/start_here.py --task contribute --fixture {fixture_id} --check-completion"
            ),
        }
    elif task_id == "research":
        ids = RESEARCH_CHECK_IDS
        sequence = (
            "write-challenger",
            "research-development-evaluation",
            "write-research-result",
            "research-git-diff-check",
            "research-completion-check",
        )
        commands = {
            "research-development-evaluation": (
                "PYTHONDONTWRITEBYTECODE=1 python examples/fresh-agent-evals/research/runner.py "
                "--evaluate-development --implementation trial-output/challenger.py "
                "--report-out trial-output/comparison-report.json --series-manifest {series_manifest}"
            ),
            "research-git-diff-check": "git diff --check",
            "research-completion-check": (
                f"python scripts/start_here.py --task research --fixture {fixture_id} "
                "--series-manifest {series_manifest} --check-completion"
            ),
        }
    else:
        raise CompletionError("fixture contracts are supported only for contribution and research tasks")
    return ids, sequence, commands


def _validate_path_list(value: Any, field: str, *, allow_trial_output: bool) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise CompletionError(f"fixture contract {field} must be a non-empty list of paths")
    if len(set(value)) != len(value):
        raise CompletionError(f"fixture contract {field} contains a duplicate path")
    for item in value:
        parts = _safe_parts(item)
        if any(
            marker in part.casefold()
            for part in parts
            for marker in ("grader", "oracle", "heldout", "held-out", "answer")
        ) or PurePosixPath(item).name in {"selfcheck.py", "fixture.json"}:
            raise CompletionError(f"fixture contract {field} names protected evaluator material")
        if not allow_trial_output and parts[0] in {"trial-output", ".tmp"}:
            raise CompletionError(f"fixture contract {field} has an invalid path class")
    return list(value)


def _validate_fixture_contract(task_id: str, fixture_id: str, value: Any) -> dict[str, Any]:
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", fixture_id)
        or any(marker in fixture_id.casefold() for marker in ("grader", "oracle", "heldout", "held-out", "answer"))
    ):
        raise CompletionError("fixture id is not a bounded public identifier")
    if not isinstance(value, dict) or set(value) != CONTRACT_KEYS:
        raise CompletionError("fixture contract fields do not match the strict disclosed shape")
    if type(value.get("contract_version")) is not int or value["contract_version"] != 1:
        raise CompletionError("fixture contract version is unsupported")
    inputs = _validate_path_list(value.get("available_input_paths"), "available_input_paths", allow_trial_output=False)
    deliverables = _validate_path_list(value.get("deliverables"), "deliverables", allow_trial_output=True)
    scratch = _validate_path_list(value.get("scratch_paths"), "scratch_paths", allow_trial_output=True)
    if scratch != [SCRATCH_REL]:
        raise CompletionError("fixture contract scratch must be the one optional completion-context notes path")
    if any(path.startswith(".tmp/") for path in deliverables):
        raise CompletionError("scratch paths cannot be declared as deliverables")
    if SCRATCH_REL in inputs or SCRATCH_REL in deliverables:
        raise CompletionError("scratch is separate from inputs and deliverables")

    trial_outputs = [path for path in deliverables if path.startswith("trial-output/")]
    if task_id == "contribute":
        completion_artifacts = [path for path in trial_outputs if PurePosixPath(path).name == "completion.json"]
        public_outputs = [path for path in deliverables if not path.startswith("trial-output/")]
        completion_schemas = [path for path in inputs if PurePosixPath(path).name == "completion.schema.json"]
        if (
            len(completion_artifacts) != 1
            or len(trial_outputs) != 1
            or len(public_outputs) != 1
            or not public_outputs[0].startswith("docs/agents/")
            or completion_schemas != [V2_COMPLETION_SCHEMA_REL]
        ):
            raise CompletionError("docs fixture must disclose one public docs deliverable and the existing v2 completion artifact/schema")
    elif task_id == "research":
        roles = {
            "challenger.py": 1,
            "comparison-report.json": 1,
            "research-result.json": 1,
        }
        if (
            len(trial_outputs) != 3
            or any(sum(PurePosixPath(path).name == name for path in trial_outputs) != count for name, count in roles.items())
            or len([path for path in inputs if PurePosixPath(path).name == "comparison-report.schema.json"]) != 1
            or len([path for path in inputs if PurePosixPath(path).name == "research-result.schema.json"]) != 1
            or len([path for path in inputs if path.endswith("/primary-source-excerpt.md")]) != 1
        ):
            raise CompletionError("research fixture must disclose one callback, report, result, public schema pair, and attributed excerpt")

    expected_ids, expected_sequence, expected_commands = _expected_commands(task_id, fixture_id)
    ids = value.get("fixed_finish_check_ids")
    sequence = value.get("finish_sequence")
    commands = value.get("permitted_finish_commands")
    if ids != list(expected_ids):
        raise CompletionError("fixture contract fixed finishing-check ids are unsupported or out of order")
    if sequence != list(expected_sequence):
        raise CompletionError("fixture contract finish sequence is unsupported")
    if not isinstance(commands, dict) or commands != expected_commands:
        raise CompletionError("fixture contract permitted command grammar does not match fixed implementations")
    if not all(isinstance(path, str) for path in inputs + deliverables + scratch):
        raise CompletionError("fixture contract contains a non-string path")
    return value


def load_onboarding(root: Path) -> dict[str, Any]:
    data = read_json_safe(root, ONBOARDING_REL)
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        raise CompletionError("canonical onboarding data must contain a task object")
    return data


def fixture_ids(root: Path) -> dict[str, list[str]]:
    onboarding = load_onboarding(root)
    result: dict[str, list[str]] = {}
    owners: dict[str, str] = {}
    for task_id, task in onboarding["tasks"].items():
        contracts = task.get("fixture_contracts") if isinstance(task, dict) else None
        if contracts is None:
            continue
        if not isinstance(contracts, dict) or not contracts:
            raise CompletionError("task fixture_contracts must be a non-empty object when present")
        if task_id not in TASK_SERIES_DIRS:
            raise CompletionError("fixture contracts may be nested only under contribution or research tasks")
        for fixture_id, contract in contracts.items():
            if fixture_id in owners:
                raise CompletionError("fixture id is duplicated across canonical onboarding tasks")
            owners[fixture_id] = task_id
            _validate_fixture_contract(task_id, fixture_id, contract)
        result[task_id] = sorted(contracts)
    return result


def load_fixture_contract(root: Path, task_id: str, fixture_id: str) -> dict[str, Any]:
    onboarding = load_onboarding(root)
    tasks = onboarding["tasks"]
    owners = []
    for other_task, other in tasks.items():
        other_contracts = other.get("fixture_contracts") if isinstance(other, dict) else None
        if isinstance(other_contracts, dict) and fixture_id in other_contracts:
            owners.append(other_task)
    if len(owners) > 1:
        raise CompletionError("fixture id is duplicated across canonical onboarding tasks")
    if owners and owners[0] != task_id:
        raise CompletionError(f"fixture {fixture_id!r} belongs to task {owners[0]!r}, not {task_id!r}")
    task = tasks.get(task_id)
    if not isinstance(task, dict):
        raise CompletionError("selected task is not in canonical onboarding")
    contracts = task.get("fixture_contracts")
    if not isinstance(contracts, dict) or fixture_id not in contracts:
        raise CompletionError(f"fixture {fixture_id!r} is not defined under task {task_id!r}")
    return _validate_fixture_contract(task_id, fixture_id, contracts[fixture_id])


def fixture_deliverable(root: Path, task_id: str, fixture_id: str, basename: str) -> str:
    """Resolve one artifact role from the canonical deliverable list."""
    contract = load_fixture_contract(root, task_id, fixture_id)
    matches = [path for path in contract["deliverables"] if PurePosixPath(path).name == basename]
    if len(matches) != 1:
        raise CompletionError("canonical fixture contract does not declare exactly one requested deliverable")
    return matches[0]


def selected_input_paths(contract: dict[str, Any], task_id: str, series_manifest: str | None) -> list[str]:
    paths = list(contract["available_input_paths"])
    if series_manifest is not None:
        selected = validate_series_manifest_path(task_id, series_manifest)
        if selected in paths:
            raise CompletionError("selected series manifest duplicates a canonical input path")
        paths.append(selected)
    return paths


def render_finish_commands(
    contract: dict[str, Any], task_id: str, series_manifest: str | None,
) -> list[dict[str, str]]:
    rendered = []
    for check_id in contract["fixed_finish_check_ids"]:
        command = contract["permitted_finish_commands"][check_id]
        if "{series_manifest}" in command:
            if series_manifest is None:
                command = command.replace(
                    "{series_manifest}", f"{series_directory(task_id)}/<selected-name>.json"
                )
            else:
                command = command.replace(
                    "{series_manifest}", validate_series_manifest_path(task_id, series_manifest)
                )
        rendered.append({"check_id": check_id, "command": command})
    return rendered


def parse_route_helper_argv(argv: list[str] | tuple[str, ...], root: Path) -> dict[str, Any] | None:
    """Parse only the documented, read-only start_here helper grammar."""
    words = list(argv)
    if (
        len(words) < 3
        or re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(words[0]).name) is None
        or words[1].removeprefix("./") != "scripts/start_here.py"
    ):
        return None
    values: dict[str, str] = {}
    flags: set[str] = set()
    allowed_values = {"--task", "--fixture", "--series-manifest", "--format", "--harness"}
    allowed_flags = {"--check-completion"}
    index = 2
    while index < len(words):
        token = words[index]
        if token in allowed_flags:
            if token in flags:
                return None
            flags.add(token)
            index += 1
            continue
        if "=" in token:
            name, value = token.split("=", 1)
            if name not in allowed_values or name in values or not value:
                return None
            values[name] = value
            index += 1
            continue
        if token not in allowed_values or token in values or index + 1 >= len(words):
            return None
        values[token] = words[index + 1]
        index += 2
    task_id = values.get("--task")
    fixture_id = values.get("--fixture")
    series_manifest = values.get("--series-manifest")
    if task_id is None:
        return None
    try:
        onboarding = load_onboarding(root)
        if task_id not in onboarding["tasks"]:
            return None
        if values.get("--format", "text") not in {"text", "json"}:
            return None
        if "--harness" in values and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", values["--harness"]) is None:
            return None
        contract = None
        if fixture_id is not None:
            contract = load_fixture_contract(root, task_id, fixture_id)
        if series_manifest is not None:
            if contract is None:
                return None
            validate_series_manifest_path(task_id, series_manifest)
        if "--check-completion" in flags and contract is None:
            return None
        if "--check-completion" in flags and task_id == "research" and series_manifest is None:
            return None
        return {
            "task": task_id,
            "fixture": fixture_id,
            "series_manifest": series_manifest,
            "check_completion": "--check-completion" in flags,
        }
    except (CompletionError, OSError, TypeError):
        return None


def _resolve_ref(reference: str, root_schema: dict) -> Any:
    if not reference.startswith("#/"):
        raise CompletionError("public artifact schema contains a non-local reference")
    value: Any = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            raise CompletionError("public artifact schema contains an unresolved local reference")
        value = value[token]
    return value


def _type_matches(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return type(value) is bool
    if kind == "null":
        return value is None
    raise CompletionError("public artifact schema uses an unsupported type keyword")


def _schema_keyword_problems(schema: dict, root_schema: dict) -> list[str]:
    """Validate the supported schema grammar without treating data as schema."""
    problems: list[str] = []
    visited: set[int] = set()

    def walk(node: object, location: str, depth: int) -> None:
        if depth > MAX_SCHEMA_DEPTH:
            problems.append(f"{location}: schema nesting exceeds the safe validation bound")
            return
        if not isinstance(node, dict):
            problems.append(f"{location}: public schema node is malformed")
            return
        identity = id(node)
        if identity in visited:
            return
        visited.add(identity)

        unknown = sorted((key for key in node if key not in SCHEMA_KEYWORDS), key=str)
        if unknown:
            rendered = ", ".join(repr(key) for key in unknown[:12])
            problems.append(f"{location}: unsupported JSON Schema keyword(s): {rendered}")

        for keyword in ("$schema", "$id", "$comment", "title", "description"):
            if keyword in node and not isinstance(node[keyword], str):
                problems.append(f"{location}: schema annotation {keyword} must be a string")
        for keyword in ("deprecated", "readOnly", "writeOnly"):
            if keyword in node and type(node[keyword]) is not bool:
                problems.append(f"{location}: schema annotation {keyword} must be boolean")
        if "examples" in node and not isinstance(node["examples"], list):
            problems.append(f"{location}: schema annotation examples must be an array")

        if "type" in node:
            expected = node["type"]
            types = expected if isinstance(expected, list) else [expected]
            if (
                not types
                or any(not isinstance(item, str) or item not in SCHEMA_TYPES for item in types)
                or len(types) != len(set(types))
            ):
                problems.append(f"{location}: schema type must use supported JSON types")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            problems.append(f"{location}: schema enum must be a nonempty array")
        if "required" in node:
            required = node["required"]
            if (
                not isinstance(required, list)
                or any(not isinstance(item, str) for item in required)
                or len(required) != len(set(required))
            ):
                problems.append(f"{location}: schema required must be a unique string array")
        for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
            if keyword in node and (type(node[keyword]) is not int or node[keyword] < 0):
                problems.append(f"{location}: schema {keyword} must be a nonnegative integer")
        for minimum, maximum in (("minItems", "maxItems"), ("minLength", "maxLength")):
            if (
                type(node.get(minimum)) is int
                and type(node.get(maximum)) is int
                and node[minimum] > node[maximum]
            ):
                problems.append(f"{location}: schema {minimum} exceeds {maximum}")
        if "uniqueItems" in node and type(node["uniqueItems"]) is not bool:
            problems.append(f"{location}: schema uniqueItems must be boolean")
        for keyword in ("minimum", "maximum"):
            if keyword in node and (
                isinstance(node[keyword], bool)
                or not isinstance(node[keyword], (int, float))
            ):
                problems.append(f"{location}: schema {keyword} must be numeric")
        if (
            isinstance(node.get("minimum"), (int, float))
            and not isinstance(node.get("minimum"), bool)
            and isinstance(node.get("maximum"), (int, float))
            and not isinstance(node.get("maximum"), bool)
            and node["minimum"] > node["maximum"]
        ):
            problems.append(f"{location}: schema minimum exceeds maximum")
        if "pattern" in node:
            if not isinstance(node["pattern"], str):
                problems.append(f"{location}: schema pattern must be a string")
            else:
                try:
                    re.compile(node["pattern"])
                except re.error:
                    problems.append(f"{location}: public schema pattern is malformed")
        if "format" in node and node["format"] != "uri":
            problems.append(f"{location}: unsupported schema format {node['format']!r}")
        if "format" in node and not isinstance(node["format"], str):
            problems.append(f"{location}: schema format must be a supported string")

        reference = node.get("$ref")
        if "$ref" in node:
            if not isinstance(reference, str):
                problems.append(f"{location}: schema $ref must be a local reference string")
            else:
                try:
                    target = _resolve_ref(reference, root_schema)
                except CompletionError as exc:
                    problems.append(f"{location}: {exc}")
                else:
                    walk(target, f"{location}.$ref", depth + 1)

        for keyword in ("properties", "$defs", "definitions"):
            if keyword not in node:
                continue
            children = node[keyword]
            if not isinstance(children, dict):
                problems.append(f"{location}: schema {keyword} must be an object of subschemas")
                continue
            for name, child in children.items():
                if not isinstance(name, str):
                    problems.append(f"{location}: schema {keyword} keys must be strings")
                walk(child, f"{location}.{keyword}.{name}", depth + 1)

        if "additionalProperties" in node:
            additional = node["additionalProperties"]
            if type(additional) is bool:
                pass
            elif isinstance(additional, dict):
                walk(additional, f"{location}.additionalProperties", depth + 1)
            else:
                problems.append(f"{location}: schema additionalProperties must be boolean or a subschema")
        if "items" in node:
            items = node["items"]
            if isinstance(items, dict):
                walk(items, f"{location}.items", depth + 1)
            else:
                problems.append(f"{location}: schema items must be an object subschema")

    walk(schema, "$", 0)
    return problems[:80]


def _schema_problems(
    value: Any,
    schema: dict,
    root_schema: dict,
    location: str = "$",
    depth: int = 0,
    *,
    _schema_checked: bool = False,
) -> list[str]:
    if depth > MAX_SCHEMA_DEPTH:
        return [f"{location}: schema nesting exceeds the safe validation bound"]
    if not isinstance(schema, dict):
        return [f"{location}: public schema node is malformed"]
    if not _schema_checked:
        grammar_problems = _schema_keyword_problems(schema, root_schema)
        if grammar_problems:
            return grammar_problems
    problems: list[str] = []
    reference = schema.get("$ref")
    if reference is not None:
        try:
            target = _resolve_ref(reference, root_schema)
        except CompletionError as exc:
            return [f"{location}: {exc}"]
        if isinstance(target, dict):
            problems.extend(_schema_problems(
                value, target, root_schema, location, depth + 1, _schema_checked=True,
            ))
        else:
            return [f"{location}: referenced public schema node is malformed"]

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(isinstance(item, str) and _type_matches(value, item) for item in types):
            return problems + [f"{location}: value has the wrong JSON type"]
    if "const" in schema and value != schema["const"]:
        problems.append(f"{location}: value does not match the disclosed constant")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{location}: value is outside the disclosed enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                problems.append(f"{location}.{key}: required field is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    problems.append(f"{location}.{key}: additional field is not disclosed")
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None and isinstance(schema.get("additionalProperties"), dict):
                child_schema = schema["additionalProperties"]
            if isinstance(child_schema, dict):
                problems.extend(_schema_problems(
                    item, child_schema, root_schema, f"{location}.{key}", depth + 1,
                    _schema_checked=True,
                ))
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            problems.append(f"{location}: array has fewer than {minimum} item(s)")
        if maximum is not None and len(value) > maximum:
            problems.append(f"{location}: array has more than {maximum} item(s)")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for item in value]
            if len(encoded) != len(set(encoded)):
                problems.append(f"{location}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                problems.extend(_schema_problems(
                    item, item_schema, root_schema, f"{location}[{index}]", depth + 1,
                    _schema_checked=True,
                ))
    elif isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < schema["minLength"]:
            problems.append(f"{location}: string is shorter than the disclosed minimum")
        if schema.get("maxLength") is not None and len(value) > schema["maxLength"]:
            problems.append(f"{location}: string exceeds the disclosed maximum")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            problems.append(f"{location}: string does not match the disclosed pattern")
        if schema.get("format") == "uri":
            parsed = urlsplit(value)
            if not parsed.scheme or not parsed.netloc:
                problems.append(f"{location}: value is not a well-formed absolute URI")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            problems.append(f"{location}: number is below the disclosed minimum")
        if maximum is not None and value > maximum:
            problems.append(f"{location}: number exceeds the disclosed maximum")
    return problems[:80]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _walk_regular_files(root: Path, directory: str) -> tuple[list[str], list[str]]:
    """Return a bounded inventory without following any symlink or special file."""
    parts = _safe_parts(directory)
    try:
        start = _open_dir_fd(root, parts)
    except _MissingPath:
        return [], []
    files: list[str] = []
    unsafe: list[str] = []
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def walk(fd: int, prefix: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(fd), key=lambda item: item.name)
        except OSError:
            unsafe.append("/".join(prefix))
            return
        for entry in entries:
            name = entry.name
            relative_parts = prefix + (name,)
            relative = "/".join(relative_parts)
            try:
                if entry.is_symlink():
                    unsafe.append(relative)
                elif entry.is_dir(follow_symlinks=False):
                    try:
                        child = os.open(name, directory_flags, dir_fd=fd)
                    except OSError:
                        unsafe.append(relative)
                    else:
                        try:
                            walk(child, relative_parts)
                        finally:
                            os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    files.append(relative)
                else:
                    unsafe.append(relative)
            except OSError:
                unsafe.append(relative)

    try:
        walk(start, parts)
    finally:
        os.close(start)
    return files, unsafe


def _workspace_symlink_paths(root: Path) -> list[str]:
    """Discover path hazards without opening file contents or following links."""
    root_fd = _open_root_fd(root)
    unsafe: list[str] = []
    skipped = {".git", ".tmp", ".venv", ".venv-docs", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules", ".opencode"}
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def walk(fd: int, prefix: tuple[str, ...]) -> None:
        try:
            with os.scandir(fd) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError:
            unsafe.append("/".join(prefix) or ".")
            return
        for entry in entries:
            parts = prefix + (entry.name,)
            relative = "/".join(parts)
            try:
                if entry.is_symlink():
                    unsafe.append(relative)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in skipped:
                        continue
                    try:
                        child = os.open(entry.name, directory_flags, dir_fd=fd)
                    except OSError:
                        unsafe.append(relative)
                    else:
                        try:
                            walk(child, parts)
                        finally:
                            os.close(child)
                elif entry.is_file(follow_symlinks=False) and entry.name.casefold().endswith(".md"):
                    if any(
                        marker in part.casefold()
                        for part in parts
                        for marker in ("grader", "oracle", "heldout", "held-out", "answer")
                    ):
                        unsafe.append(relative)
            except OSError:
                unsafe.append(relative)

    try:
        walk(root_fd, ())
    finally:
        os.close(root_fd)
    return sorted(set(unsafe))


def _safe_link_target_exists(root: Path, relative: str) -> bool:
    """Check a normalized local link target with metadata only; never open it."""
    if relative in {"", "."}:
        try:
            descriptor = _open_root_fd(root)
        except (CompletionError, OSError):
            return False
        os.close(descriptor)
        return True
    try:
        parts = _safe_parts(relative)
        parent = _open_dir_fd(root, parts[:-1])
    except (CompletionError, OSError):
        return False
    try:
        try:
            metadata = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
    finally:
        os.close(parent)


def _markdown_link_escape_paths(
    root: Path,
    disclosed_markdown_paths: set[str] | tuple[str, ...] | list[str],
) -> list[str]:
    """Preflight only declared Markdown pages and inspect link targets by metadata."""
    skipped = {".git", ".tmp", ".venv", ".venv-docs", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules", ".opencode"}
    unsafe: list[str] = []
    paths = sorted(set(disclosed_markdown_paths))
    for relative in paths:
        if not isinstance(relative, str) or not relative.casefold().endswith(".md"):
            continue
        try:
            parts = _safe_parts(relative)
        except CompletionError:
            unsafe.append(relative)
            continue
        if any(
            marker in part.casefold()
            for part in parts
            for marker in ("grader", "oracle", "heldout", "held-out", "answer")
        ):
            unsafe.append(relative)
            continue
        try:
            text = _read_safe_bytes(root, relative, maximum=MAX_JSON_BYTES).decode("utf-8", "replace")
        except (CompletionError, OSError):
            unsafe.append(relative)
            continue
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw = match.group(1)
            if raw.startswith(("http://", "https://", "mailto:", "tel:")):
                continue
            target = raw.partition("#")[0].split("?", 1)[0]
            if not target or "<" in target or ">" in target or target.startswith("{"):
                continue
            if target.startswith("/"):
                normalized = posixpath.normpath(target.lstrip("/"))
            else:
                normalized = posixpath.normpath(
                    posixpath.join(PurePosixPath(relative).parent.as_posix(), target)
                )
            normalized_parts = set(PurePosixPath(normalized).parts)
            if (
                normalized == ".."
                or normalized.startswith("../")
                or normalized.startswith("/")
                or normalized_parts & skipped
                or any(
                    marker in part.casefold()
                    for part in normalized_parts
                    for marker in ("grader", "oracle", "heldout", "held-out", "answer")
                )
            ):
                unsafe.append(relative)
                break
            if not _safe_link_target_exists(root, normalized):
                unsafe.append(relative)
                break
    return sorted(set(unsafe))


def _run_fixed_validator(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Run the existing truthfulness/link functions over disclosed public inputs only."""
    sys.dont_write_bytecode = True
    root = Path(root)
    available = contract.get("available_input_paths")
    deliverables = contract.get("deliverables")
    if (
        not isinstance(available, list)
        or not isinstance(deliverables, list)
        or any(not isinstance(path, str) for path in (*available, *deliverables))
    ):
        return {"status": "failed", "summary": "disclosed Markdown input paths are malformed", "exit_code": None}
    disclosed = set(available) | {
        path for path in deliverables if not path.startswith("trial-output/")
    }
    try:
        for relative in disclosed:
            _safe_parts(relative)
    except CompletionError:
        return {"status": "failed", "summary": "disclosed input path is unsafe", "exit_code": None}
    disclosed_markdown = {
        relative for relative in disclosed
        if PurePosixPath(relative).suffix.casefold() == ".md"
    }
    validator_rel = ".github/scripts/validate_agent_docs.py"
    _probe_safe_file(root, validator_rel)
    if _workspace_symlink_paths(root):
        return {"status": "failed", "summary": "validator input tree contains a symlink; no validator was run", "exit_code": None}
    if _markdown_link_escape_paths(root, disclosed_markdown):
        return {"status": "failed", "summary": "a disclosed Markdown page has an unsafe or missing local link target; no validator was run", "exit_code": None}
    _files, unsafe = _walk_regular_files(root, ".github/scripts/validation")
    if unsafe:
        return {"status": "failed", "summary": "validator dependency path is unsafe", "exit_code": None}
    for relative in _files:
        if relative.endswith(".py"):
            _probe_safe_file(root, relative)

    for relative in disclosed:
        if Path(relative).suffix.casefold() in {".json", ".jsonl"}:
            try:
                _read_safe_bytes(root, relative)
            except (CompletionError, OSError):
                return {"status": "failed", "summary": "a declared truthfulness input is unsafe or exceeds the bounded read size", "exit_code": None}

    scripts_path = str(root / ".github/scripts")
    sys.path[:] = [path for path in sys.path if path != scripts_path]
    sys.path.insert(0, scripts_path)
    validation_root = (root / ".github/scripts/validation").resolve()
    for module_name in ("validate_agent_docs", "validation"):
        cached = sys.modules.get(module_name)
        cached_path = getattr(cached, "__file__", None) if cached is not None else None
        if isinstance(cached_path, str) and not Path(cached_path).resolve().is_relative_to(root / ".github/scripts"):
            return {"status": "failed", "summary": "a non-workspace validator module is already loaded; no validator ran", "exit_code": None}
    try:
        validator = importlib.import_module("validate_agent_docs")
        anchors = validator.validation_anchors
    except (ImportError, OSError) as exc:
        return {"status": "failed", "summary": f"fixed truthfulness/link validator could not load ({type(exc).__name__})", "exit_code": None}
    validator_path = Path(validator.__file__).resolve()
    anchors_path = Path(anchors.__file__).resolve()
    if validator_path != (root / validator_rel).resolve() or not anchors_path.is_relative_to(validation_root):
        return {"status": "failed", "summary": "fixed validator implementation resolved outside its checked local source paths", "exit_code": None}
    for module_name, module in tuple(sys.modules.items()):
        if module_name == "validation" or module_name.startswith("validation."):
            module_path = getattr(module, "__file__", None)
            if isinstance(module_path, str) and not Path(module_path).resolve().is_relative_to(validation_root):
                return {"status": "failed", "summary": "validation helper import escaped its checked source directory", "exit_code": None}

    def scoped_iter_files(base: Path, suffixes: set[str] | None = None):
        accepted = {suffix.casefold() for suffix in suffixes} if suffixes is not None else None
        for relative in sorted(disclosed):
            path = root.joinpath(*_safe_parts(relative))
            if not path.is_relative_to(base):
                continue
            if accepted is None or path.suffix.casefold() in accepted:
                yield path

    truth_report = validator.Report()
    link_report = validator.Report()
    canonical_pages = tuple(path for path in validator.CANONICAL_ONBOARDING if path in disclosed)
    original_is_file = Path.is_file

    def scoped_is_file(path: Path) -> bool:
        if path == root / "CONTRIBUTING.md" and path.relative_to(root).as_posix() not in disclosed:
            return False
        return original_is_file(path)

    unverified_anchor_targets: set[str] = set()
    original_collect_anchors = anchors.collect_anchors

    class UnverifiedAnchors:
        def __contains__(self, _fragment: object) -> bool:
            return True

    def scoped_collect_anchors(path: Path):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            return UnverifiedAnchors()
        if relative not in disclosed:
            unverified_anchor_targets.add(relative)
            return UnverifiedAnchors()
        return original_collect_anchors(path)

    from unittest import mock

    try:
        with (
            mock.patch.object(validator, "iter_files", scoped_iter_files),
            mock.patch.object(anchors, "iter_files", scoped_iter_files),
            mock.patch.object(validator, "CANONICAL_ONBOARDING", canonical_pages),
            mock.patch.object(anchors, "collect_anchors", scoped_collect_anchors),
            mock.patch.object(Path, "is_file", scoped_is_file),
        ):
            validator.check_truthfulness(truth_report, root)
            anchors.check_links(link_report, root)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return {"status": "failed", "summary": f"fixed scoped truthfulness/link validator failed safely ({type(exc).__name__})", "exit_code": None}

    errors = truth_report.errors + link_report.errors
    if errors:
        return {"status": "failed", "summary": f"fixed scoped truthfulness/link validators reported {len(errors)} issue(s)", "exit_code": 1}
    if unverified_anchor_targets:
        return {
            "status": "unverified",
            "summary": f"truthfulness and disclosed links passed; {len(unverified_anchor_targets)} out-of-contract Markdown anchor target(s) were not opened",
            "next_action": "Review undeclared link targets manually; the checker did not open their contents.",
            "exit_code": 0,
        }
    return {
        "status": "passed",
        "summary": "fixed scoped truthfulness and Markdown link validators passed on disclosed inputs",
        "exit_code": 0,
    }


def _contract_artifact_paths(contract: dict[str, Any]) -> tuple[list[str], str | None]:
    trial_outputs = [path for path in contract["deliverables"] if path.startswith("trial-output/")]
    completion = next((path for path in trial_outputs if PurePosixPath(path).name == "completion.json"), None)
    return trial_outputs, completion


def _load_selected_manifest(root: Path, task_id: str, relative: str | None) -> tuple[dict | None, bytes | None, str | None]:
    if relative is None:
        return None, None, None
    selected = validate_series_manifest_path(task_id, relative)
    raw = _read_safe_bytes(root, selected)
    data = _strict_json_loads(raw)
    if not isinstance(data, dict):
        raise CompletionError("selected series manifest must contain a JSON object")
    expected_series_version, expected_schema_version = _selected_manifest_versions(root, task_id)
    required_keys = {
        "series_id", "series_version", "manifest_schema_version", "frozen_files", "freeze_note",
    }
    if set(data) != required_keys:
        raise CompletionError("selected manifest fields do not match the selected task's manifest schema")
    series_id = data.get("series_id")
    if (
        not isinstance(series_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", series_id) is None
        or PurePosixPath(selected).name != f"{series_id}.json"
        or type(data.get("series_version")) is not int
        or data.get("series_version") != expected_series_version
        or type(data.get("manifest_schema_version")) is not int
        or data.get("manifest_schema_version") != expected_schema_version
        or not isinstance(data.get("frozen_files"), dict)
        or not data["frozen_files"]
        or not isinstance(data.get("freeze_note"), str)
        or not data["freeze_note"].strip()
    ):
        raise CompletionError("selected manifest identity or task-specific manifest schema is invalid")
    for frozen_path, digest in data["frozen_files"].items():
        if not isinstance(frozen_path, str):
            raise CompletionError("selected manifest contains a non-string frozen source path")
        try:
            _safe_parts(frozen_path)
        except CompletionError as exc:
            raise CompletionError("selected manifest content contains an unsafe frozen source path") from exc
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise CompletionError("selected manifest contains a malformed source digest")
    return data, raw, _hash(raw)


def _completion_record_problems(
    root: Path,
    task_id: str,
    fixture_id: str,
    contract: dict[str, Any],
    completion_path: str,
) -> tuple[list[str], dict | None]:
    problems: list[str] = []
    raw = _read_safe_bytes(root, completion_path)
    completion = _strict_json_loads(raw)
    schema_paths = [
        path for path in contract["available_input_paths"]
        if PurePosixPath(path).name == "completion.schema.json"
    ]
    if schema_paths != [V2_COMPLETION_SCHEMA_REL]:
        return ["docs contract does not declare the existing v2 completion schema"], None
    schema = read_json_safe(root, schema_paths[0])
    if not isinstance(schema, dict):
        return ["v2 completion schema is malformed"], None
    problems.extend(_schema_problems(completion, schema, schema))
    if not isinstance(completion, dict):
        return problems or ["completion artifact must be a JSON object"], None
    if completion.get("case_id") != fixture_id:
        problems.append("completion artifact case_id does not match the selected fixture")
    route = completion.get("route")
    if not isinstance(route, dict) or route.get("selected_task") != task_id:
        problems.append("completion artifact selected task does not match the fixture contract")
    elif task_id == "contribute":
        onboarding = load_onboarding(root)
        canonical_task = onboarding["tasks"].get(task_id, {})
        expected_live_auth = (
            "explicit_user_authorization_required"
            if "explicitly authorized live campaign" in str(onboarding.get("access_rule", ""))
            else "unknown"
        )
        if canonical_task.get("offline_allowed") is not True or canonical_task.get("live_campaign_required") is not False:
            problems.append("canonical contribution route no longer establishes offline, non-live completion access")
        if route.get("offline_contribution_access") != "not_required":
            problems.append("completion artifact offline-contribution fact differs from canonical onboarding")
        if route.get("live_campaign_authorization") != expected_live_auth:
            problems.append("completion artifact live-authorization fact differs from canonical onboarding")
        sources = route.get("authoritative_sources")
        if not isinstance(sources, list) or any(path not in contract["available_input_paths"] for path in sources):
            problems.append("completion artifact authoritative sources are outside the disclosed input contract")
        contribution = completion.get("contribution")
        public_outputs = [path for path in contract["deliverables"] if not path.startswith("trial-output/")]
        if not isinstance(contribution, dict) or contribution.get("changed_paths") != public_outputs:
            expected_paths = ", ".join(public_outputs) if public_outputs else "(none declared)"
            problems.append(
                "completion artifact contribution.changed_paths must match the declared public deliverable paths "
                f"(excluding the completion record and optional scratch): {expected_paths}"
            )
        validation = completion.get("validation")
        if isinstance(validation, dict):
            expected_command = contract["permitted_finish_commands"]["docs-final-validation"]
            for label in ("baseline", "final"):
                record = validation.get(label)
                if not isinstance(record, dict):
                    continue
                if record.get("command") != expected_command:
                    problems.append(f"completion artifact validation.{label} command is outside the fixed grammar")
                if record.get("modes") != ["truthfulness", "links"]:
                    problems.append(f"completion artifact validation.{label} modes do not match the fixed validator")
                if type(record.get("exit_code")) is int and (
                    (record["exit_code"] == 0 and record.get("result") != "passed")
                    or (record["exit_code"] != 0 and record.get("result") != "failed")
                ):
                    problems.append(f"completion artifact validation.{label} result contradicts its explicit exit code")
            final = validation.get("final")
            if isinstance(final, dict) and (final.get("exit_code") != 0 or final.get("result") != "passed"):
                problems.append("completion artifact final validator record does not declare a successful result")
    return problems, completion


def _by_basename(contract: dict[str, Any], basename: str) -> str | None:
    matches = [path for path in contract["deliverables"] if PurePosixPath(path).name == basename]
    return matches[0] if len(matches) == 1 else None


def _research_artifact_problems(
    root: Path,
    contract: dict[str, Any],
    selected_manifest_path: str | None,
    manifest: dict | None,
    manifest_digest: str | None,
) -> list[str]:
    problems: list[str] = []
    code_path = _by_basename(contract, "challenger.py")
    report_path = _by_basename(contract, "comparison-report.json")
    result_path = _by_basename(contract, "research-result.json")
    if not all((code_path, report_path, result_path)):
        return ["research contract does not declare exactly one code, report, and result deliverable"]
    code_bytes = _read_safe_bytes(root, code_path)
    report_bytes = _read_safe_bytes(root, report_path)
    result_bytes = _read_safe_bytes(root, result_path)
    report = _strict_json_loads(report_bytes)
    result = _strict_json_loads(result_bytes)
    report_schema_path = next(
        (path for path in contract["available_input_paths"] if PurePosixPath(path).name == "comparison-report.schema.json"),
        None,
    )
    result_schema_path = next(
        (path for path in contract["available_input_paths"] if PurePosixPath(path).name == "research-result.schema.json"),
        None,
    )
    excerpt_path = next(
        (path for path in contract["available_input_paths"] if path.endswith("/primary-source-excerpt.md")),
        None,
    )
    if not report_schema_path or not result_schema_path or not excerpt_path:
        return ["research input contract is missing a public report/result schema or source excerpt"]
    report_schema = read_json_safe(root, report_schema_path)
    result_schema = read_json_safe(root, result_schema_path)
    if not isinstance(report_schema, dict) or not isinstance(result_schema, dict):
        return ["research public artifact schema is malformed"]
    problems.extend(_schema_problems(report, report_schema, report_schema))
    problems.extend(_schema_problems(result, result_schema, result_schema))
    if isinstance(report, dict):
        if report.get("role_scope") != "development_inputs_only":
            problems.append("research report must be development-input-only; reserved input use is not permitted")
        runs = report.get("runs")
        if isinstance(runs, list) and any(not isinstance(run, dict) or run.get("role") != "development" for run in runs):
            problems.append("research report contains a non-development run")
    if not isinstance(result, dict):
        return problems + ["research result artifact must be a JSON object"]
    source = result.get("source")
    claims = result.get("machine_claims")
    if not isinstance(source, dict) or not isinstance(claims, dict):
        return problems + ["research result source or machine_claims object is missing"]
    excerpt_bytes = _read_safe_bytes(root, excerpt_path)
    excerpt_digest = _hash(excerpt_bytes)
    code_digest = _hash(code_bytes)
    report_digest = _hash(report_bytes)
    expected_result_values = {
        "comparison_report_path": report_path,
        "comparison_report_sha256": report_digest,
        "implementation_path": code_path,
        "implementation_sha256": code_digest,
    }
    for key, expected in expected_result_values.items():
        if claims.get(key) != expected:
            problems.append(f"research result machine_claims.{key} does not match the disclosed deliverable")
    if source.get("source_excerpt_path") != excerpt_path or source.get("source_excerpt_sha256") != excerpt_digest:
        problems.append("research result source excerpt path/hash does not match the declared public input")
    if selected_manifest_path is None or manifest is None or manifest_digest is None:
        problems.append("research completion check requires one selected safe research series manifest")
    else:
        if (
            claims.get("series_manifest_path") != selected_manifest_path
            or claims.get("series_manifest_sha256") != manifest_digest
            or claims.get("series_id") != manifest.get("series_id")
        ):
            problems.append("research result series identity does not match the selected manifest")
        if isinstance(report, dict) and (
            report.get("series_id") != manifest.get("series_id")
            or report.get("series_manifest_sha256") != manifest_digest
            or report.get("implementation_sha256") != code_digest
            or report.get("source_excerpt_sha256") != excerpt_digest
        ):
            problems.append("research report linked identities do not match the selected inputs and deliverables")
    return problems[:100]


def check_completion(
    root: Path,
    task_id: str,
    fixture_id: str,
    series_manifest: str | None = None,
    *,
    run_fixed_checks: bool = True,
) -> dict[str, Any]:
    """Check a selected completion contract without changing the workspace."""
    result: dict[str, Any] = {
        "status": "fail",
        "task": task_id,
        "fixture": fixture_id,
        "read_only": True,
        "contract": None,
        "input_results": [],
        "checks": [],
        "missing_inputs": [],
        "unsafe_paths": [],
        "artifact_problems": [],
        "unexpected_deliverables": [],
        "manual_review": [
            "Unrelated or pre-existing working-tree changes are not authorized by this contract and remain unresolved for manual review."
        ],
        "next_steps": [],
    }
    try:
        root = _root_path(root)
        contract = load_fixture_contract(root, task_id, fixture_id)
        inputs = selected_input_paths(contract, task_id, series_manifest)
        result["contract"] = {
            "contract_version": contract["contract_version"],
            "available_input_paths": inputs,
            "deliverables": list(contract["deliverables"]),
            "scratch_paths": list(contract["scratch_paths"]),
            "fixed_finish_check_ids": list(contract["fixed_finish_check_ids"]),
            "finish_sequence": list(contract["finish_sequence"]),
            "permitted_finish_commands": render_finish_commands(contract, task_id, series_manifest),
        }
    except _UnsafePath as exc:
        if series_manifest is not None:
            result["unsafe_paths"].append(series_manifest)
            result["next_steps"].append(
                "The selected series manifest path is unsafe; do not follow or inspect it. Select a canonical named file under this task's series directory, then rerun the check."
            )
        else:
            result["artifact_problems"].append(f"contract path: {exc}")
            result["next_steps"].append("Correct the unsafe canonical contract path before rerunning this read-only check.")
        return result
    except (CompletionError, OSError, TypeError) as exc:
        result["artifact_problems"].append(f"contract: {exc}")
        result["next_steps"].append("Correct the canonical task fixture contract or select the matching task and fixture, then rerun this read-only check.")
        return result

    for relative in inputs:
        try:
            _probe_safe_file(root, relative)
        except _MissingPath:
            result["missing_inputs"].append(relative)
        except (CompletionError, OSError):
            result["unsafe_paths"].append(relative)
    for relative in contract["scratch_paths"]:
        try:
            _probe_safe_file(root, relative)
            result["input_results"].append({"path": relative, "status": "optional scratch present; content not read"})
        except _MissingPath:
            result["input_results"].append({"path": relative, "status": "optional scratch absent"})
        except (CompletionError, OSError):
            result["unsafe_paths"].append(relative)

    selected_data = selected_raw = selected_digest = None
    selected_manifest_problems: list[str] = []
    if series_manifest is not None:
        try:
            selected_data, selected_raw, selected_digest = _load_selected_manifest(root, task_id, series_manifest)
        except _MissingPath:
            result["missing_inputs"].append(series_manifest)
        except _UnsafePath:
            result["unsafe_paths"].append(series_manifest)
        except OSError:
            result["unsafe_paths"].append(series_manifest)
        except CompletionError as exc:
            selected_manifest_problems.append(f"selected manifest: {exc}")

    output_paths, completion_path = _contract_artifact_paths(contract)
    actual_outputs, unsafe_outputs = _walk_regular_files(root, "trial-output")
    result["unsafe_paths"].extend(unsafe_outputs)
    result["unsafe_paths"].extend(_workspace_symlink_paths(root))
    allowed_outputs = set(output_paths)
    result["unexpected_deliverables"] = sorted(set(actual_outputs) - allowed_outputs)
    for relative in output_paths:
        try:
            _probe_safe_file(root, relative)
        except _MissingPath:
            result["missing_inputs"].append(relative)
        except (CompletionError, OSError):
            result["unsafe_paths"].append(relative)

    blockers: list[str] = []
    if result["missing_inputs"]:
        blockers.append("one or more declared inputs or deliverables are missing")
    if result["unsafe_paths"]:
        blockers.append("one or more declared paths or output entries are unsafe")
    if result["unexpected_deliverables"]:
        blockers.append("trial-output contains files outside the exact deliverable list")

    artifact_problems: list[str] = list(selected_manifest_problems)
    completion_record: dict | None = None
    if task_id == "contribute" and completion_path is not None and not blockers:
        try:
            problems, completion_record = _completion_record_problems(
                root, task_id, fixture_id, contract, completion_path,
            )
            artifact_problems.extend(problems)
        except (CompletionError, OSError) as exc:
            artifact_problems.append(f"completion artifact: {exc}")
    elif task_id == "research" and not blockers:
        try:
            artifact_problems.extend(
                _research_artifact_problems(
                    root, contract, series_manifest, selected_data, selected_digest,
                )
            )
        except (CompletionError, OSError) as exc:
            artifact_problems.append(f"research artifacts: {exc}")

    checks: list[dict[str, Any]] = []
    for check_id in contract["fixed_finish_check_ids"]:
        if task_id == "contribute" and check_id == "docs-baseline-validation":
            status = "unverified"
            summary = "baseline command/result shape is checked in completion.json; native execution order is not observable here"
            next_action = "Use the retained native action trace or a reviewer to verify the baseline ran before edits."
        elif task_id == "contribute" and check_id == "docs-final-validation":
            declared_ok = completion_record is not None and not any(
                "validation.final" in problem for problem in artifact_problems
            )
            fixed = _run_fixed_validator(root, contract) if run_fixed_checks and not result["unsafe_paths"] else {
                "status": "unverified", "summary": "fixed validators were not run in this selfcheck mode", "exit_code": None,
            }
            if not declared_ok:
                status = "failed"
                summary = (
                    "completion artifact does not declare the required final validator command/result; "
                    + fixed["summary"]
                )
                next_action = "Create or correct the completion artifact after recording the final validator result; native trace review must verify its earlier execution."
            elif fixed["status"] == "failed":
                status = "failed"
                summary = fixed["summary"]
                next_action = "Rerun the exact fixed validator after resolving its reported issue; do not auto-fix unrelated work."
            elif fixed["status"] in {"passed", "unverified"}:
                status = "unverified"
                summary = fixed["summary"] + "; the artifact's prior truthfulness-validator execution remains native-trace evidence"
                next_action = fixed.get("next_action") or "Use the retained native action trace or a reviewer to verify the fixed truthfulness/links invocation and its prior result."
            else:
                status = "unverified"
                summary = fixed["summary"]
                next_action = "Run the permitted fixed validator and retain native evidence, then rerun the checker."
        elif check_id in {"docs-git-diff-check", "research-git-diff-check"}:
            status = "unverified"
            summary = "this filesystem-only checker does not invoke git; native execution evidence must establish the permitted diff check"
            next_action = "Run the exact permitted git diff --check command before the final completion checker and retain its native exit result."
        elif check_id == "research-development-evaluation":
            status = "unverified"
            summary = "the checker validates the public report schema but never executes or independently attests the development evaluator"
            next_action = "Retain native evidence of the exact permitted development-evaluation command for independent review."
        else:
            status = "passed" if not blockers and not artifact_problems else "failed"
            summary = "declared inputs, exact outputs, and public artifact checks passed" if status == "passed" else "completion contract or artifact checks have blockers"
            next_action = None if status == "passed" else "Resolve the listed missing, unsafe, malformed, or unexpected items, then rerun the checker."
        checks.append({"id": check_id, "status": status, "summary": summary, "next_action": next_action})

    result["checks"] = checks
    result["artifact_problems"] = artifact_problems
    result["input_results"].extend(
        {"path": path, "status": "present and path-safe"}
        for path in inputs if path not in result["missing_inputs"] and path not in result["unsafe_paths"]
    )
    result["missing_inputs"] = sorted(set(result["missing_inputs"]))
    result["unsafe_paths"] = sorted(set(result["unsafe_paths"]))
    result["unexpected_deliverables"] = sorted(set(result["unexpected_deliverables"]))
    failed_checks = [item for item in checks if item["status"] == "failed"]
    if not blockers and not artifact_problems and not failed_checks:
        result["status"] = "pass"
        result["next_steps"].append("No repair was performed. Review the unresolved-work note and retain native execution evidence for unverified checks.")
    else:
        result["status"] = "fail"
        if result["missing_inputs"]:
            result["next_steps"].append("Restore the listed missing paths from the public packet or produce each required deliverable, then rerun the check.")
        if result["unsafe_paths"]:
            result["next_steps"].append("Remove the path hazard only with user authorization; do not follow or inspect the symlink target, then rerun the check.")
        if result["unexpected_deliverables"]:
            result["next_steps"].append("Review unexpected trial-output files manually; the checker will not delete, accept, or hash them as authorized work.")
        if artifact_problems:
            result["next_steps"].append("Correct malformed public artifacts using their disclosed schemas, then rerun the check; the checker never edits them.")
        if not result["next_steps"]:
            result["next_steps"].append("Resolve failed fixed checks without changing unrelated work, then rerun the check.")
    return result


def render_completion_text(result: dict[str, Any]) -> str:
    lines = [f"Completion check: {result.get('status', 'fail').upper()}"]
    contract = result.get("contract")
    if isinstance(contract, dict):
        lines.extend(("", "Available inputs:"))
        lines.extend(f"- {item}" for item in contract.get("available_input_paths", []))
        lines.append("Selected deliverables:")
        lines.extend(f"- {item}" for item in contract.get("deliverables", []))
        lines.append("Optional scratch (not a deliverable or artifact hash input):")
        lines.extend(f"- {item}" for item in contract.get("scratch_paths", []))
    for title, key in (
        ("Checks", "checks"),
        ("Missing inputs/deliverables", "missing_inputs"),
        ("Unsafe paths", "unsafe_paths"),
        ("Malformed artifacts/contract", "artifact_problems"),
        ("Unexpected deliverables", "unexpected_deliverables"),
        ("Manual review", "manual_review"),
        ("Next steps", "next_steps"),
    ):
        values = result.get(key, [])
        if not values:
            continue
        lines.extend(("", f"{title}:"))
        if key == "checks":
            for item in values:
                lines.append(f"- {item['id']}: {item['status']} — {item['summary']}")
                if item.get("next_action"):
                    lines.append(f"  Next: {item['next_action']}")
        else:
            lines.extend(f"- {item}" for item in values)
    return "\n".join(lines) + "\n"


def _schema_example(schema: dict, root_schema: dict, depth: int = 0) -> Any:
    if depth > MAX_SCHEMA_DEPTH:
        raise CompletionError("selfcheck schema example exceeded its nesting bound")
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], root_schema)
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    expected = schema.get("type", "object")
    if isinstance(expected, list):
        expected = next((item for item in expected if item != "null"), expected[0])
    if expected == "object":
        properties = schema.get("properties", {})
        return {
            key: _schema_example(properties[key], root_schema, depth + 1)
            for key in schema.get("required", [])
            if isinstance(properties, dict) and key in properties
        }
    if expected == "array":
        count = schema.get("minItems", 0)
        item_schema = schema.get("items", {})
        return [_schema_example(item_schema, root_schema, depth + 1) for _ in range(count)]
    if expected == "string":
        pattern = schema.get("pattern", "")
        if "0-9a-f" in pattern:
            return "0" * 64
        if "a-z0-9" in pattern and "63" in pattern:
            return "a00"
        if schema.get("format") == "uri":
            return "https://example.invalid/source"
        return "x" * max(1, schema.get("minLength", 1))
    if expected == "integer":
        return schema.get("minimum", 0)
    if expected == "number":
        return schema.get("minimum", 0)
    if expected == "boolean":
        return False
    if expected == "null":
        return None
    raise CompletionError("selfcheck cannot synthesize an example for the public schema type")


def _selfcheck_tree(root: Path) -> dict[str, str]:
    result = {}
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            if (current_path / name).is_symlink():
                result[(current_path / name).relative_to(root).as_posix()] = "symlink"
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            result[relative] = _hash(path.read_bytes()) if not path.is_symlink() else "symlink"
    return result


def _copy_contract_inputs(source: Path, target: Path, contract: dict[str, Any]) -> None:
    paths = list(contract["available_input_paths"])
    if ONBOARDING_REL not in paths:
        paths.append(ONBOARDING_REL)
    for relative in paths:
        path = target.joinpath(*_safe_parts(relative))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_read_safe_bytes(source, relative))


def _write_test_json(root: Path, relative: str, value: Any) -> None:
    path = root.joinpath(*_safe_parts(relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_test_bytes(root: Path, relative: str, value: bytes) -> None:
    path = root.joinpath(*_safe_parts(relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _docs_sample(contract: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    command = contract["permitted_finish_commands"]["docs-final-validation"]
    public_outputs = [path for path in contract["deliverables"] if not path.startswith("trial-output/")]
    return {
        "schema_version": 2,
        "case_id": fixture_id,
        "route": {
            "selected_task": "contribute",
            "offline_contribution_access": "not_required",
            "live_campaign_authorization": "explicit_user_authorization_required",
            "authoritative_sources": ["README.md", "docs/agents/first-contribution.md"],
        },
        "contribution": {"kind": "documentation_correction", "changed_paths": public_outputs},
        "validation": {
            "baseline": {"command": command, "modes": ["truthfulness", "links"], "exit_code": 1, "result": "failed"},
            "final": {"command": command, "modes": ["truthfulness", "links"], "exit_code": 0, "result": "passed"},
        },
        "human_review": {"summary": "Selfcheck only.", "rationale": "Synthetic public artifact shape."},
    }


def _populate_research_sample(root: Path, contract: dict[str, Any], fixture_id: str) -> str:
    code_path = _by_basename(contract, "challenger.py")
    report_path = _by_basename(contract, "comparison-report.json")
    result_path = _by_basename(contract, "research-result.json")
    report_schema_path = next(path for path in contract["available_input_paths"] if PurePosixPath(path).name == "comparison-report.schema.json")
    result_schema_path = next(path for path in contract["available_input_paths"] if PurePosixPath(path).name == "research-result.schema.json")
    excerpt_path = next(path for path in contract["available_input_paths"] if path.endswith("/primary-source-excerpt.md"))
    series_path = f"{series_directory('research')}/completion-selfcheck.json"
    series_id = "completion-selfcheck"
    series_version, schema_version = _selected_manifest_versions(root, "research")
    manifest = {
        "series_id": series_id,
        "series_version": series_version,
        "manifest_schema_version": schema_version,
        "frozen_files": {"README.md": _hash(_read_safe_bytes(root, "README.md"))},
        "freeze_note": "synthetic selfcheck manifest",
    }
    _write_test_json(root, series_path, manifest)
    manifest_digest = _hash(_read_safe_bytes(root, series_path))
    code = b"def challenger(ctx):\n    return iter(())\n"
    code_file = root.joinpath(*_safe_parts(code_path))
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_bytes(code)
    code_digest = _hash(code)
    excerpt_digest = _hash(_read_safe_bytes(root, excerpt_path))

    report_schema = read_json_safe(root, report_schema_path)
    result_schema = read_json_safe(root, result_schema_path)
    report = _schema_example(report_schema, report_schema)
    result = _schema_example(result_schema, result_schema)
    report.update({
        "role_scope": "development_inputs_only",
        "series_id": series_id,
        "series_manifest_sha256": manifest_digest,
        "implementation_sha256": code_digest,
        "source_excerpt_sha256": excerpt_digest,
    })
    for run in report.get("runs", []):
        if isinstance(run, dict):
            run["role"] = "development"
    _write_test_json(root, report_path, report)
    report_digest = _hash(_read_safe_bytes(root, report_path))
    result["source"]["source_excerpt_path"] = excerpt_path
    result["source"]["source_excerpt_sha256"] = excerpt_digest
    claims = result["machine_claims"]
    claims.update({
        "comparison_report_path": report_path,
        "comparison_report_sha256": report_digest,
        "implementation_path": code_path,
        "implementation_sha256": code_digest,
        "series_id": series_id,
        "series_manifest_path": series_path,
        "series_manifest_sha256": manifest_digest,
    })
    _write_test_json(root, result_path, result)
    return series_path


def selfcheck_problems(root: Path) -> list[str]:
    """Run isolated contract/artifact/path regressions without repo writes."""
    import tempfile
    from unittest import mock

    failures: list[str] = []
    try:
        docs_contract = load_fixture_contract(root, "contribute", "first-finished-contribution-primary")
        research_contract = load_fixture_contract(root, "research", "paper-backed-coverage-challenger")
        docs_frozen_path = f"{series_directory('contribute')}/completion-reliability-docs-1.json"
        research_frozen_path = f"{series_directory('research')}/completion-reliability-research-1.json"
        docs_frozen_raw = _read_safe_bytes(root, docs_frozen_path)
        research_frozen_raw = _read_safe_bytes(root, research_frozen_path)
        docs_frozen = _strict_json_loads(docs_frozen_raw)
        research_frozen = _strict_json_loads(research_frozen_raw)
        docs_versions = _selected_manifest_versions(root, "contribute")
        research_versions = _selected_manifest_versions(root, "research")
        if (
            not isinstance(docs_frozen, dict)
            or (docs_frozen.get("series_version"), docs_frozen.get("manifest_schema_version")) != docs_versions
            or docs_versions != (2, 2)
        ):
            failures.append("actual frozen contribution 2/2 manifest did not match its task definition")
        if (
            not isinstance(research_frozen, dict)
            or (research_frozen.get("series_version"), research_frozen.get("manifest_schema_version")) != research_versions
            or research_versions != (1, 1)
        ):
            failures.append("actual frozen research 1/1 manifest did not match its public series schema")
        if _strict_json_loads(json.dumps(docs_contract)) != docs_contract:
            failures.append("strict contract JSON round-trip changed canonical fixture facts")
        try:
            _strict_json_loads('{"contract_version":1,"contract_version":1}')
        except CompletionError:
            pass
        else:
            failures.append("duplicate contract JSON fields were accepted")
        try:
            validate_series_manifest_path("research", "examples/fresh-agent-evals/research/series/../hidden.json")
        except CompletionError:
            pass
        else:
            failures.append("series-manifest traversal was accepted")
        try:
            load_fixture_contract(root, "research", "first-finished-contribution-primary")
        except CompletionError:
            pass
        else:
            failures.append("a fixture selected under the wrong task was accepted")
        malformed_contracts = []
        duplicate_contract = dict(docs_contract)
        duplicate_contract["available_input_paths"] = list(docs_contract["available_input_paths"]) + [docs_contract["available_input_paths"][0]]
        malformed_contracts.append(duplicate_contract)
        escaped_contract = dict(docs_contract)
        escaped_contract["available_input_paths"] = list(docs_contract["available_input_paths"])
        escaped_contract["available_input_paths"][0] = "../outside.md"
        malformed_contracts.append(escaped_contract)
        extra_contract_field = dict(docs_contract)
        extra_contract_field["agent_command"] = "python -c pass"
        malformed_contracts.append(extra_contract_field)
        for contract_candidate in malformed_contracts:
            try:
                _validate_fixture_contract(
                    "contribute", "first-finished-contribution-primary", contract_candidate,
                )
            except CompletionError:
                continue
            failures.append("duplicate, traversal, or command-bearing contract fields passed strict validation")
            break

        for keyword in ("oneOf", "anyOf", "not"):
            unsupported_schema = {keyword: [{"type": "string"}, {"type": "boolean"}]}
            if not any(
                "unsupported JSON Schema keyword" in problem and keyword in problem
                for problem in _schema_problems(7, unsupported_schema, unsupported_schema)
            ):
                failures.append(f"unsupported top-level schema keyword {keyword} was silently accepted")
        nested_schemas = (
            {
                "type": "object",
                "properties": {"nested": {"oneOf": [{"type": "string"}]}},
            },
            {
                "type": "array",
                "items": {"type": "object", "properties": {"value": {"not": {"type": "null"}}}},
            },
            {"type": "object", "$defs": {"unused": {"anyOf": [{"type": "null"}]}}},
        )
        for nested_schema in nested_schemas:
            if not any(
                "unsupported JSON Schema keyword" in problem
                for problem in _schema_problems({}, nested_schema, nested_schema)
            ):
                failures.append("unsupported nested schema constraint was not rejected before instance traversal")
                break
        instance_data = {
            "oneOf": "ordinary instance data",
            "not": {"type": "not a schema here"},
        }
        instance_schema = {
            "type": "object",
            "enum": [instance_data],
            "examples": [{"anyOf": "ordinary example data"}],
        }
        if _schema_problems(instance_data, instance_schema, instance_schema):
            failures.append("instance data inside enum/examples was incorrectly parsed as subschema syntax")

        with tempfile.TemporaryDirectory(prefix="openforage-completion-selfcheck-") as temp:
            sandbox = Path(temp) / "docs-workspace"
            sandbox.mkdir()
            _copy_contract_inputs(root, sandbox, docs_contract)
            completion_path = _contract_artifact_paths(docs_contract)[1]
            if completion_path is None:
                raise CompletionError("docs contract does not identify a completion artifact")
            _write_test_json(sandbox, completion_path, _docs_sample(docs_contract, "first-finished-contribution-primary"))
            docs_selected = f"{series_directory('contribute')}/{docs_frozen['series_id']}.json"
            _write_test_bytes(sandbox, docs_selected, docs_frozen_raw)
            try:
                loaded_docs, _loaded_raw, _loaded_digest = _load_selected_manifest(
                    sandbox, "contribute", docs_selected,
                )
                if loaded_docs != docs_frozen:
                    failures.append("actual frozen contribution manifest changed during task-specific validation")
            except CompletionError:
                failures.append("actual frozen contribution 2/2 manifest was rejected by its task gate")
            docs_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", docs_selected,
                run_fixed_checks=False,
            )
            if docs_result["status"] != "pass":
                failures.append("actual docs 2/2 manifest did not pass the active completion checker")

            original_docs_manifest = _read_safe_bytes(sandbox, docs_selected)
            malformed_docs_candidates = []
            wrong_docs_versions = dict(docs_frozen)
            wrong_docs_versions["series_version"], wrong_docs_versions["manifest_schema_version"] = research_versions
            malformed_docs_candidates.append(("cross-task version", wrong_docs_versions))
            wrong_docs_identity = dict(docs_frozen)
            wrong_docs_identity["series_id"] = "other-docs-series"
            malformed_docs_candidates.append(("manifest identity", wrong_docs_identity))
            missing_docs_field = dict(docs_frozen)
            missing_docs_field.pop("freeze_note", None)
            malformed_docs_candidates.append(("missing field", missing_docs_field))
            malformed_docs_digest = dict(docs_frozen)
            malformed_docs_digest["frozen_files"] = dict(docs_frozen["frozen_files"])
            malformed_docs_digest["frozen_files"][next(iter(malformed_docs_digest["frozen_files"]))] = "bad-digest"
            malformed_docs_candidates.append(("malformed digest", malformed_docs_digest))
            malformed_docs_path = dict(docs_frozen)
            malformed_docs_path["frozen_files"] = {"../outside.json": "0" * 64}
            malformed_docs_candidates.append(("unsafe frozen path", malformed_docs_path))
            for label, candidate in malformed_docs_candidates:
                _write_test_json(sandbox, docs_selected, candidate)
                try:
                    _load_selected_manifest(sandbox, "contribute", docs_selected)
                except _UnsafePath:
                    failures.append(f"safe selected docs manifest was mislabeled as a path hazard for {label}")
                except CompletionError:
                    pass
                else:
                    failures.append(f"selected docs manifest accepted {label}")
            _write_test_bytes(sandbox, docs_selected, original_docs_manifest)

            research_in_docs_path = f"{series_directory('contribute')}/{research_frozen['series_id']}.json"
            _write_test_bytes(sandbox, research_in_docs_path, research_frozen_raw)
            try:
                _load_selected_manifest(sandbox, "contribute", research_in_docs_path)
            except CompletionError:
                pass
            else:
                failures.append("research 1/1 manifest was accepted by the contribution 2/2 gate")

            scratch_path = sandbox.joinpath(*_safe_parts(SCRATCH_REL))
            scratch_path.parent.mkdir(parents=True, exist_ok=True)
            scratch_path.write_text("optional selfcheck notes", encoding="utf-8")
            unrelated = sandbox / "docs" / "unrelated.md"
            unrelated.parent.mkdir(parents=True, exist_ok=True)
            unrelated.write_text("preserve this unrelated file\n", encoding="utf-8")
            before = _selfcheck_tree(sandbox)
            good = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            after = _selfcheck_tree(sandbox)
            if good["status"] != "pass":
                failures.append("valid docs contract/artifact and optional scratch did not pass: " + "; ".join(good["artifact_problems"]))
            if not any(item.get("status") == "unverified" for item in good.get("checks", [])):
                failures.append("native validator evidence was incorrectly claimed by the filesystem-only checker")
            if good.get("manual_review") and "unresolved" not in good["manual_review"][0]:
                failures.append("unrelated work was not marked unresolved for manual review")
            if before != after or unrelated.read_text(encoding="utf-8") != "preserve this unrelated file\n":
                failures.append("completion checking changed scratch, unrelated work, or workspace bytes")
            if any(path.endswith(".pyc") or "__pycache__" in path for path in after):
                failures.append("completion selfcheck created workspace bytecode")

            artifact_file = sandbox.joinpath(*_safe_parts(completion_path))
            original_artifact = artifact_file.read_bytes()
            wrong_paths_record = json.loads(original_artifact.decode("utf-8"))
            wrong_paths_record["contribution"]["changed_paths"] = ["docs/unlisted-public-page.md"]
            _write_test_json(sandbox, completion_path, wrong_paths_record)
            wrong_paths_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            changed_paths_diagnostic = next((
                item for item in wrong_paths_result["artifact_problems"]
                if "contribution.changed_paths" in item
            ), "")
            expected_public_paths = [
                path for path in docs_contract["deliverables"] if not path.startswith("trial-output/")
            ]
            if (
                wrong_paths_result["status"] != "fail"
                or not changed_paths_diagnostic
                or "completion record and optional scratch" not in changed_paths_diagnostic
                or any(path not in changed_paths_diagnostic for path in expected_public_paths)
            ):
                failures.append("changed_paths diagnostic did not state the declared public deliverable paths and exclusions")
            artifact_file.write_bytes(original_artifact)

            escaped_link_page = sandbox / "README.md"
            escaped_link_page.write_text("[outside](../../../../outside-secret.md)\n", encoding="utf-8")
            if "README.md" not in _markdown_link_escape_paths(sandbox, {"README.md"}):
                failures.append("fixed link-validator preflight did not reject an escaping Markdown target")

            preflight_sandbox = Path(temp) / "markdown-scope"
            preflight_sandbox.mkdir()
            (preflight_sandbox / "README.md").write_text(
                "[guide](docs/unlisted-guide.md)\n", encoding="utf-8",
            )
            unlisted = preflight_sandbox / "docs" / "unlisted-guide.md"
            unlisted.parent.mkdir()
            unlisted.write_text("[hidden](elsewhere.md)\n", encoding="utf-8")
            preflight_reads: list[str] = []
            original_safe_read = _read_safe_bytes

            def tracked_safe_read(workspace: Path, relative: str, **kwargs):
                preflight_reads.append(relative)
                return original_safe_read(workspace, relative, **kwargs)

            with mock.patch.object(
                sys.modules[__name__], "_read_safe_bytes", side_effect=tracked_safe_read,
            ):
                scoped_problems = _markdown_link_escape_paths(preflight_sandbox, {"README.md"})
            if scoped_problems or preflight_reads != ["README.md"]:
                failures.append("Markdown preflight opened an undeclared target or skipped a disclosed page")

            (preflight_sandbox / "README.md").write_text(
                "[missing](docs/missing.md)\n", encoding="utf-8",
            )
            if "README.md" not in _markdown_link_escape_paths(preflight_sandbox, {"README.md"}):
                failures.append("Markdown preflight did not reject a missing local link target")
            external_target = Path(temp) / "outside-link-target.md"
            external_target.write_text("not opened\n", encoding="utf-8")
            (preflight_sandbox / "docs" / "linked.md").symlink_to(external_target)
            (preflight_sandbox / "README.md").write_text(
                "[linked](docs/linked.md)\n", encoding="utf-8",
            )
            if "README.md" not in _markdown_link_escape_paths(preflight_sandbox, {"README.md"}):
                failures.append("Markdown preflight did not reject a symlinked local link target")

            artifact_file.write_bytes(b"{not-json")
            malformed_artifact = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            if malformed_artifact["status"] != "fail" or not malformed_artifact["artifact_problems"]:
                failures.append("malformed docs completion JSON was accepted")
            artifact_file.write_bytes(original_artifact)

            extra = sandbox / "trial-output" / "unexpected.txt"
            extra.write_text("not an authorized deliverable", encoding="utf-8")
            extra_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            if extra_result["status"] != "fail" or "trial-output/unexpected.txt" not in extra_result["unexpected_deliverables"]:
                failures.append("unexpected trial-output deliverable was not rejected")
            extra.unlink()

            missing_path = docs_contract["available_input_paths"][0]
            (sandbox / missing_path).unlink()
            missing_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            if missing_path not in missing_result["missing_inputs"]:
                failures.append("missing declared input was not reported")

            outside = Path(temp) / "outside-secret.txt"
            outside.write_text("must not be followed", encoding="utf-8")
            symlink_target = sandbox / "README.md"
            symlink_target.symlink_to(outside)
            symlink_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            if "README.md" not in symlink_result["unsafe_paths"]:
                failures.append("symlinked declared input was not rejected safely")
            artifact_file.unlink()
            artifact_file.symlink_to(outside)
            output_symlink_result = check_completion(
                sandbox, "contribute", "first-finished-contribution-primary", run_fixed_checks=False,
            )
            if completion_path not in output_symlink_result["unsafe_paths"]:
                failures.append("symlinked deliverable was not rejected before reading its target")
            try:
                _read_safe_bytes(sandbox, "../outside-secret.txt")
            except CompletionError:
                pass
            else:
                failures.append("direct traversal read was accepted")

        with tempfile.TemporaryDirectory(prefix="openforage-research-completion-selfcheck-") as temp:
            sandbox = Path(temp) / "research-workspace"
            sandbox.mkdir()
            _copy_contract_inputs(root, sandbox, research_contract)
            selected = _populate_research_sample(sandbox, research_contract, "paper-backed-coverage-challenger")
            actual_research_selected = f"{series_directory('research')}/{research_frozen['series_id']}.json"
            _write_test_bytes(sandbox, actual_research_selected, research_frozen_raw)
            try:
                loaded_research, _loaded_raw, _loaded_digest = _load_selected_manifest(
                    sandbox, "research", actual_research_selected,
                )
                if loaded_research != research_frozen:
                    failures.append("actual frozen research manifest changed during task-specific validation")
            except CompletionError:
                failures.append("actual frozen research 1/1 manifest was rejected by its public schema")

            docs_in_research_path = f"{series_directory('research')}/{docs_frozen['series_id']}.json"
            _write_test_bytes(sandbox, docs_in_research_path, docs_frozen_raw)
            try:
                _load_selected_manifest(sandbox, "research", docs_in_research_path)
            except CompletionError:
                pass
            else:
                failures.append("docs 2/2 manifest was accepted by the research 1/1 gate")

            good = check_completion(
                sandbox, "research", "paper-backed-coverage-challenger", selected,
                run_fixed_checks=False,
            )
            if good["status"] != "pass":
                failures.append("valid research schemas and linked hashes did not pass: " + "; ".join(good["artifact_problems"]))
            if not any(item["id"] == "research-development-evaluation" and item["status"] == "unverified" for item in good["checks"]):
                failures.append("research evaluator provenance was not left unverified")
            selected_file = sandbox.joinpath(*_safe_parts(selected))
            selected_bytes = selected_file.read_bytes()
            selected_data = _strict_json_loads(selected_bytes)
            selected_data["series_version"] = 2
            selected_data["manifest_schema_version"] = 2
            _write_test_json(sandbox, selected, selected_data)
            wrong_schema_result = check_completion(
                sandbox, "research", "paper-backed-coverage-challenger", selected,
                run_fixed_checks=False,
            )
            if (
                selected in wrong_schema_result["unsafe_paths"]
                or not any("task-specific manifest schema" in item for item in wrong_schema_result["artifact_problems"])
                or any("Remove the path hazard" in item for item in wrong_schema_result["next_steps"])
            ):
                failures.append("safe wrong-schema research manifest was reported as a path hazard")
            selected_file.write_bytes(selected_bytes)
            report_path = _by_basename(research_contract, "comparison-report.json")
            report_file = sandbox.joinpath(*_safe_parts(report_path))
            report_file.write_text("{malformed", encoding="utf-8")
            malformed = check_completion(
                sandbox, "research", "paper-backed-coverage-challenger", selected,
                run_fixed_checks=False,
            )
            if malformed["status"] != "fail" or not malformed["artifact_problems"]:
                failures.append("malformed research report JSON was accepted")
            outside = Path(temp) / "outside-series.json"
            outside.write_text("{}", encoding="utf-8")
            manifest_link = sandbox / selected
            manifest_link.unlink()
            manifest_link.symlink_to(outside)
            linked = check_completion(
                sandbox, "research", "paper-backed-coverage-challenger", selected,
                run_fixed_checks=False,
            )
            if selected not in linked["unsafe_paths"]:
                failures.append("symlinked selected manifest was not rejected")
            escaped = check_completion(
                sandbox, "research", "paper-backed-coverage-challenger",
                "../outside-series.json", run_fixed_checks=False,
            )
            if (
                escaped["status"] != "fail"
                or "../outside-series.json" not in escaped["unsafe_paths"]
                or escaped["artifact_problems"]
            ):
                failures.append("traversing selected manifest was not reported as a path hazard")
    except (CompletionError, OSError, KeyError, TypeError, ValueError) as exc:
        failures.append(f"completion contract selfcheck setup failed: {type(exc).__name__}: {exc}")
    return failures
