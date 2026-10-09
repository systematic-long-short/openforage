"""Checks for the portable, generated onboarding route and its offline behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .common import read_text

ONBOARDING_REL = "docs/agents/references/onboarding.json"
SCRIPT_REL = "scripts/start_here.py"
REQUIRED_TASKS = {"research", "contribute", "maintenance", "diagnose", "adapter", "campaign"}
GENERATED_REL = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agents/start-here.md",
)


def _run_check(root: Path, args: list[str], report, label: str) -> None:
    script = root / SCRIPT_REL
    if not script.is_file():
        report.fail(f"onboarding: missing {SCRIPT_REL}")
        return
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=45,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.fail(f"onboarding: {label} could not run: {type(exc).__name__}: {exc}")
        return
    if proc.returncode:
        detail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
        report.fail(f"onboarding: {label} failed (exit {proc.returncode}): {detail[0][:240]}")
    else:
        report.note(f"onboarding: {label} passed")


def check_onboarding(report, root: Path) -> None:
    path = root / ONBOARDING_REL
    if not path.is_file():
        report.fail(f"onboarding: missing canonical route data {ONBOARDING_REL}")
        return
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.fail(f"{ONBOARDING_REL}: invalid JSON: {exc}")
        return
    if not isinstance(data, dict):
        report.fail(f"{ONBOARDING_REL}: expected a JSON object")
        return
    if data.get("default_task") != "research":
        report.fail(f"{ONBOARDING_REL}: no-argument onboarding must default to offline research")
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        report.fail(f"{ONBOARDING_REL}: tasks must be an object")
        return
    missing = REQUIRED_TASKS - set(tasks)
    if missing:
        report.fail(f"{ONBOARDING_REL}: missing task selectors {sorted(missing)}")
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            report.fail(f"{ONBOARDING_REL}: task {task_id!r} must be an object")
            continue
        for key in ("objective", "authoritative_paths", "immediate_action", "required_tools", "verification_commands", "completion_criteria"):
            value = task.get(key)
            if not value:
                report.fail(f"{ONBOARDING_REL}: task {task_id!r} is missing {key}")
    runtime_rules = data.get("runtime_rules")
    if not isinstance(runtime_rules, dict):
        report.fail(f"{ONBOARDING_REL}: runtime_rules must be an object")
    else:
        continuous = str(runtime_rules.get("continuous_search", "")).casefold()
        if "same harness" not in continuous or "distinct session" not in continuous or "never fall back" not in continuous:
            report.fail(f"{ONBOARDING_REL}: continuous search must require a separate same-harness identity and reject main-session fallback")
    if "Task | Authoritative files | Validate with" in read_text(path):
        report.fail(f"{ONBOARDING_REL}: do not copy the first-contribution file/validation table into route data")

    generated = data.get("generated_entrypoints")
    if not isinstance(generated, dict):
        report.fail(f"{ONBOARDING_REL}: generated_entrypoints must be an object")
    else:
        for key, relpath in generated.items():
            if not isinstance(relpath, str) or not (root / relpath).is_file():
                report.fail(f"{ONBOARDING_REL}: generated entrypoint {key!r} is missing: {relpath!r}")
    for relpath in GENERATED_REL:
        if not (root / relpath).is_file():
            report.fail(f"onboarding: missing generated entrypoint {relpath}")

    # Route/version/capability facts remain in their existing authorities.
    for key in ("compatibility", "harness_registry", "research_opportunities", "campaign_template"):
        source = data.get("source_paths", {}).get(key) if isinstance(data.get("source_paths"), dict) else None
        if not isinstance(source, str) or not (root / source).is_file():
            report.fail(f"{ONBOARDING_REL}: required authoritative source {key!r} is missing: {source!r}")

    _run_check(root, ["--check"], report, "generated-doc drift check")
    _run_check(root, ["--selfcheck"], report, "deterministic offline routing selfcheck")
