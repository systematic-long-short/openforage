"""Runtime OpenCode permission profile for the file-tools-only eval cases.

The admitted permission keys and native action-event names are separate: the
OpenCode ``edit`` permission covers edit/write/patch operations, while a native
patch call is emitted as ``apply_patch``. Keep the normalized event identity
for grading and map it to the permission key only for admission checks.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROFILE_ID = "opencode-file-tools-only-v2"
AGENT_NAME = "entry-files-only"
PROFILE_CONFIG_REL = "examples/fresh-agent-evals/trial-inputs/opencode-file-tools-only.json"
PERMISSION_TOOL_KEYS = ("read", "list", "glob", "grep", "edit")
# OpenCode's `edit` permission admits the equivalent emitted file-action names
# `edit`, `write`, and `apply_patch`. Keep these action labels intact in traces.
FILE_ACTION_PERMISSION_KEYS = {
    "read": "read",
    "list": "list",
    "glob": "glob",
    "grep": "grep",
    "edit": "edit",
    "write": "edit",
    "apply_patch": "edit",
}
ARTIFACT_WRITE_TOOLS = frozenset({"edit", "write", "apply_patch"})
DENIED_TOOLS = ("*", "external_directory")
DEFAULT_TOOL_PERMISSION = "deny"
DEBUG_OUTPUT_LIMIT = 500_000

# Dict insertion order is intentional: the default deny appears before the
# narrow file-tool allowlist in the reviewable runtime JSON as well.
PROFILE_CONFIG = {
    "agent": {
        AGENT_NAME: {
            "description": "Restrict entrypoint discovery trials to workspace file tools; edit also admits write and patch operations.",
            "mode": "primary",
            "permission": {
                "*": "deny",
                "read": "allow",
                "list": "allow",
                "glob": "allow",
                "grep": "allow",
                "edit": "allow",
                "external_directory": "deny",
            },
        }
    }
}


class ToolProfileError(RuntimeError):
    """The expected runtime agent profile or isolated workspace is invalid."""


def special_project_directories(workspace: Path) -> dict[str, bool]:
    """Report project config/dependency trees without retaining absolute paths."""
    found = {"opencode": False, "node_modules": False}
    for current, directories, _files in os.walk(workspace, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        found["opencode"] = found["opencode"] or ".opencode" in directories
        found["node_modules"] = found["node_modules"] or "node_modules" in directories
        if all(found.values()):
            break
    return found


def permission_for_tool(permission: dict, tool: str) -> str | None:
    """Resolve an explicit tool rule, then the wildcard default."""
    value = permission.get(tool, permission.get("*"))
    return value if isinstance(value, str) else None


def check_profile_fixture(repo: Path, workspace: Path) -> str:
    """Return the reviewed JSON config after checking it and the workspace."""
    profile_path = repo / PROFILE_CONFIG_REL
    try:
        raw = profile_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolProfileError("reviewable runtime JSON permission profile is missing or invalid") from exc
    if data != PROFILE_CONFIG:
        raise ToolProfileError("reviewable runtime JSON permission profile differs from the admitted profile")
    permission = data["agent"][AGENT_NAME]["permission"]
    if list(permission)[:1] != ["*"]:
        raise ToolProfileError("wildcard default deny must be the first permission entry")
    if (workspace / "AGENTS.md").exists() or (workspace / "CLAUDE.md").exists():
        raise ToolProfileError("file-tools-only cold-start fixture must omit root AGENTS.md and CLAUDE.md")
    special = special_project_directories(workspace)
    if special["opencode"] or special["node_modules"]:
        raise ToolProfileError("prepared file-tools-only workspace must omit .opencode and node_modules")
    return raw


def _permission_from_node(value: object) -> dict | None:
    """Find only the named agent's permission map in debug-agent JSON."""
    if isinstance(value, dict):
        if value.get("name") == AGENT_NAME:
            permission = value.get("permission")
            if isinstance(permission, dict):
                return permission
            if isinstance(permission, list):
                return _permission_from_rule_list(permission)
        named = value.get(AGENT_NAME)
        if isinstance(named, dict):
            permission = named.get("permission")
            if isinstance(permission, dict):
                return permission
            if isinstance(permission, list):
                return _permission_from_rule_list(permission)
        for child in value.values():
            result = _permission_from_node(child)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _permission_from_node(child)
            if result is not None:
                return result
    return None


def _permission_from_rule_list(rules: list) -> dict | None:
    """Reduce OpenCode's debug-agent permission rules to wildcard tool decisions."""
    recognized = {"*", "external_directory", *PERMISSION_TOOL_KEYS, "bash"}
    values: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        tool = rule.get("permission")
        action = rule.get("action")
        pattern = rule.get("pattern")
        if tool in recognized and pattern == "*" and action in {"allow", "deny"}:
            values[tool] = action
    return values or None


def _permission_from_text(output: str) -> dict | None:
    """Fallback for the human-readable debug-agent table, never raw passthrough."""
    start = output.find(AGENT_NAME)
    if start < 0:
        return None
    section = output[start:start + DEBUG_OUTPUT_LIMIT]
    values: dict[str, str] = {}
    for line in section.splitlines():
        for match in re.finditer(
            r"(?:^|[,{\s])['\"]?(\*|[A-Za-z_][A-Za-z0-9_-]*)['\"]?\s*[:=]\s*['\"]?(allow|deny)\b",
            line,
        ):
            values[match.group(1)] = match.group(2)
    return values or None


def effective_permission_summary(output: str) -> dict | None:
    """Extract an allowlisted matrix from `opencode debug agent` output."""
    if not isinstance(output, str) or len(output) > DEBUG_OUTPUT_LIMIT:
        return None
    values: dict | None = None
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        values = _permission_from_node(parsed)
        if values is not None:
            break
    if values is None:
        values = _permission_from_text(output)
    if values is None:
        return None

    summary = {
        "*": permission_for_tool(values, "__unknown_fixture_tool__"),
        "external_directory": permission_for_tool(values, "external_directory"),
        "bash": permission_for_tool(values, "bash"),
    }
    summary.update({tool: permission_for_tool(values, tool) for tool in PERMISSION_TOOL_KEYS})
    expected = {tool: "allow" for tool in PERMISSION_TOOL_KEYS}
    expected.update({"*": "deny", "external_directory": "deny", "bash": "deny"})
    if any(summary.get(tool) != action for tool, action in expected.items()):
        return None
    summary["matches_admitted_matrix"] = True
    return summary


def profile_selfcheck() -> list[str]:
    """Offline checks for wildcard fallback and sanitized debug-agent parsing."""
    failures: list[str] = []
    permission = PROFILE_CONFIG["agent"][AGENT_NAME]["permission"]
    expected_file_actions = {
        "read": "read",
        "list": "list",
        "glob": "glob",
        "grep": "grep",
        "edit": "edit",
        "write": "edit",
        "apply_patch": "edit",
    }
    if FILE_ACTION_PERMISSION_KEYS != expected_file_actions:
        failures.append("native file-action labels do not map only to their admitted permission keys")
    if set(FILE_ACTION_PERMISSION_KEYS) & {"bash", "shell", "task", "webfetch", "websearch"}:
        failures.append("a shell, task, or network tool entered the native file-action allowlist")
    if list(permission)[:1] != ["*"]:
        failures.append("wildcard default deny is not the first permission entry")
    if permission_for_tool(permission, "future_unlisted_tool") != "deny":
        failures.append("an unknown tool did not fall through to the wildcard default deny")
    if permission_for_tool(permission, "bash") != "deny":
        failures.append("an unlisted shell tool did not inherit the wildcard deny")
    if any(permission_for_tool(permission, tool) != "allow" for tool in PERMISSION_TOOL_KEYS):
        failures.append("the explicit file-tool allowlist is incomplete")
    if permission_for_tool(permission, "external_directory") != "deny":
        failures.append("external_directory is not explicitly denied")

    debug_rules = [
        {"permission": tool, "pattern": "*", "action": action}
        for tool, action in permission.items()
    ]
    debug = json.dumps({"name": AGENT_NAME, "permission": debug_rules})
    summary = effective_permission_summary(debug)
    if not isinstance(summary, dict) or summary.get("matches_admitted_matrix") is not True:
        failures.append("synthetic debug-agent rule-list output was not reduced to the admitted matrix")
    if effective_permission_summary(json.dumps({"other-agent": {"permission": permission}})) is not None:
        failures.append("permissions from an unrelated debug-agent entry were accepted")
    if effective_permission_summary("unrelated output without an agent matrix") is not None:
        failures.append("unstructured debug-agent output was treated as verification")
    return failures


def profile_record(
    *,
    configured: bool,
    applied: bool,
    replay: bool,
    permission_probe: dict | None = None,
) -> dict:
    """Sanitized metadata; old retained records remain valid without new keys."""
    return {
        "profile_id": PROFILE_ID,
        "agent_name": AGENT_NAME,
        "mechanism": "OPENCODE_CONFIG_CONTENT runtime JSON agent selected with --agent",
        "configured": configured,
        "applied_to_child_invocation": applied,
        "replay_not_enforced": replay,
        "default_tool_permission": DEFAULT_TOOL_PERMISSION,
        "wildcard_default_deny": True,
        "allowed_tools": list(PERMISSION_TOOL_KEYS),
        "denied_tools": list(DENIED_TOOLS),
        "effective_permission_probe": permission_probe or {"status": "not_performed"},
        "host_python_binary_absence_claimed": False,
        "note": "Tool permissions are checked through OpenCode's effective debug-agent matrix; the prompt alone is not enforcement and no host-Python absence is claimed.",
    }
