"""Named OpenCode build-agent permissions for local contribution trials."""

from __future__ import annotations

import json
import os
from pathlib import Path

from processes import run_bounded

AGENT_NAME = "build"
ALLOWED = {"read", "list", "glob", "grep", "edit", "bash", "todowrite"}
DENIED = {
    "*", "external_directory", "webfetch", "websearch", "network", "task",
    "question", "plan_enter", "plan_exit",
}
EXPECTED = {name: "allow" for name in ALLOWED} | {name: "deny" for name in DENIED}
PERMISSION = {
    "*": "deny",
    **{name: "allow" for name in sorted(ALLOWED - {"todowrite"})},
    "todowrite": "allow",
    "external_directory": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "network": "deny",
    "task": "deny",
    "question": "deny",
    "plan_enter": "deny",
    "plan_exit": "deny",
}
PROFILE_CONFIG = {
    "permission": dict(PERMISSION),
    "agent": {
        AGENT_NAME: {
            "description": "Local documentation contribution; no external, web, or nested-agent tools.",
            "mode": "primary",
            "permission": dict(PERMISSION),
        }
    },
}


class ProfileError(RuntimeError):
    """The effective named-agent permission matrix was not verified."""


def config_text() -> str:
    return json.dumps(PROFILE_CONFIG, separators=(",", ":"))


def special_project_dirs(workspace: Path) -> dict[str, bool]:
    found = {"opencode": False, "node_modules": False}
    for current, directories, _files in os.walk(workspace, followlinks=False):
        found["opencode"] |= ".opencode" in directories
        found["node_modules"] |= "node_modules" in directories
        directories[:] = [name for name in directories if name not in {".git", ".opencode", "node_modules"}]
    return found


def _rules(permission) -> dict[str, str] | None:
    if isinstance(permission, dict):
        return {
            key: value for key, value in permission.items()
            if isinstance(key, str) and isinstance(value, str) and value in {"allow", "deny"}
        }
    if isinstance(permission, list):
        values = {}
        for item in permission:
            if not isinstance(item, dict) or item.get("pattern") != "*":
                continue
            tool, action = item.get("permission"), item.get("action")
            if isinstance(tool, str) and action in {"allow", "deny"}:
                values[tool] = action
        return values or None
    return None


def _find_agent(value):
    if isinstance(value, dict):
        if value.get("name") == AGENT_NAME and "permission" in value:
            return _rules(value["permission"])
        named = value.get(AGENT_NAME)
        if isinstance(named, dict) and "permission" in named:
            return _rules(named["permission"])
        for child in value.values():
            result = _find_agent(child)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _find_agent(child)
            if result is not None:
                return result
    return None


def effective_agent_matrix(output: str) -> dict[str, str] | None:
    """Extract only build-agent decisions from native debug output."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "{[":
            continue
        try:
            parsed, _end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        result = _find_agent(parsed)
        if result is not None:
            return result
    return None


def verify_effective_profile(binary: str, workspace: Path, env: dict) -> dict:
    before = special_project_dirs(workspace)
    probe_env = dict(env)
    probe_env["OPENCODE_CONFIG_CONTENT"] = config_text()
    try:
        proc = run_bounded([binary, "debug", "agent", AGENT_NAME], cwd=workspace, env=probe_env, timeout=45)
    except OSError as exc:
        raise ProfileError(f"OpenCode profile probe could not start: {type(exc).__name__}") from exc
    output = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    matrix = effective_agent_matrix(output)
    after = special_project_dirs(workspace)
    matrix_ok = matrix == EXPECTED
    clean_tree = not any(before.values()) and not any(after.values())
    result = {
        "method": "non-model opencode debug agent build with OPENCODE_CONFIG_CONTENT",
        "exit_code": proc.returncode,
        "timed_out": proc.timed_out,
        "process_group_remaining": proc.process_group_remaining,
        "effective_matrix_verified": matrix_ok,
        "project_runtime_trees_absent_before": not any(before.values()),
        "project_runtime_trees_absent_after": not any(after.values()),
        "model_invocation": False,
    }
    if not proc.timed_out and not proc.process_group_remaining and proc.returncode == 0 and matrix_ok and clean_tree:
        return result
    raise ProfileError("effective named-agent permissions or clean project tree did not verify")
