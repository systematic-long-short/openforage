"""Compatibility expectations schema and deterministic probe-engine selfcheck."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from .common import is_calendar_date, is_real_string, read_text

EXPECTATIONS_REL = "docs/agents/references/compatibility.json"
TOOL_REL = ".github/scripts/check_compatibility.py"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def check_compatibility(report, root: Path) -> None:
    path = root / EXPECTATIONS_REL
    tool = root / TOOL_REL
    if not path.is_file():
        report.fail(f"compatibility: missing {EXPECTATIONS_REL}")
        return
    if not tool.is_file():
        report.fail(f"compatibility: missing {TOOL_REL}")
        return
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.fail(f"{EXPECTATIONS_REL}: invalid JSON: {exc}")
        return

    supported = data.get("supported")
    if not isinstance(supported, dict) or "openforage" not in supported:
        report.fail(f"{EXPECTATIONS_REL}: supported.openforage is required as the single tested version source")
    else:
        entry = supported["openforage"]
        if not isinstance(entry.get("version"), str) or not SEMVER_RE.match(entry.get("version", "")):
            report.fail(f"{EXPECTATIONS_REL}: supported.openforage.version must be a semver string")
        if not entry.get("python_minors") or not all(isinstance(v, str) for v in entry["python_minors"]):
            report.fail(f"{EXPECTATIONS_REL}: supported.openforage.python_minors must be a non-empty list of strings")
        if not entry.get("platforms"):
            report.fail(f"{EXPECTATIONS_REL}: supported.openforage.platforms must be non-empty")
        if entry.get("promise") != "tested-supported":
            report.fail(f"{EXPECTATIONS_REL}: supported.openforage.promise must be 'tested-supported'")
        if not is_calendar_date(entry.get("verified_on")):
            report.fail(f"{EXPECTATIONS_REL}: supported.openforage.verified_on must be a real calendar date")

    observation = data.get("latest_observation")
    if not isinstance(observation, dict) or observation.get("promise") != "observation-only":
        report.fail(
            f"{EXPECTATIONS_REL}: latest_observation.promise must be 'observation-only'; "
            "a latest-version observation is never a support promise"
        )

    surface = data.get("expected_surface")
    if not isinstance(surface, dict):
        report.fail(f"{EXPECTATIONS_REL}: expected_surface must be an object")
    else:
        cli = surface.get("cli")
        if not isinstance(cli, dict):
            report.fail(f"{EXPECTATIONS_REL}: expected_surface.cli must describe the actual CLI probe")
        else:
            invocation = cli.get("invocation")
            if not isinstance(invocation, list) or not invocation or not all(isinstance(v, str) for v in invocation):
                report.fail(f"{EXPECTATIONS_REL}: expected_surface.cli.invocation must be a non-empty argv list")
            commands = cli.get("commands")
            if not isinstance(commands, list) or not commands:
                report.fail(f"{EXPECTATIONS_REL}: expected_surface.cli.commands must be non-empty")
            choices = cli.get("start_algorithm_choices")
            if not isinstance(choices, list) or "random_weighted" not in choices:
                report.fail(
                    f"{EXPECTATIONS_REL}: expected_surface.cli.start_algorithm_choices must include random_weighted"
                )
            documented_default = cli.get("documented_default_algorithm")
            if not isinstance(documented_default, dict) or not documented_default.get("value"):
                report.fail(
                    f"{EXPECTATIONS_REL}: expected_surface.cli.documented_default_algorithm must name the "
                    "documented default separately from the observed CLI choices"
                )
            elif documented_default.get("label") == documented_default.get("cli_label"):
                report.fail(
                    f"{EXPECTATIONS_REL}: documented-default and observed-CLI labels must stay distinct"
                )
        required = surface.get("required_callables")
        if not isinstance(required, dict) or not required:
            report.fail(f"{EXPECTATIONS_REL}: expected_surface.required_callables must be non-empty")
        for dotted, spec in (required or {}).items():
            if not dotted.startswith("openforage."):
                report.fail(f"{EXPECTATIONS_REL}: required callable {dotted!r} must live under openforage.")
            if not isinstance(spec, dict):
                report.fail(f"{EXPECTATIONS_REL}: required callable {dotted!r} spec must be an object")
        for dotted, spec in (surface.get("observed_only_callables") or {}).items():
            if not isinstance(spec, dict) or spec.get("contract") not in {
                "signature-observed-only",
                "requires a live runtime vocabulary",
                "namespace observed",
            }:
                report.fail(
                    f"{EXPECTATIONS_REL}: observed-only callable {dotted!r} must declare an observed contract, "
                    "not a documented one"
                )
        unavailable = surface.get("unavailable_or_unidentified")
        if not isinstance(unavailable, list) or not unavailable:
            report.fail(f"{EXPECTATIONS_REL}: expected_surface.unavailable_or_unidentified must be non-empty")

    harness_versions = data.get("harness_versions") or {}
    for product, entry in harness_versions.items():
        if not isinstance(entry, dict):
            report.fail(f"{EXPECTATIONS_REL}: harness_versions.{product} must be an object")
            continue
        if entry.get("registry_status") == "verified":
            report.fail(
                f"{EXPECTATIONS_REL}: harness_versions.{product} must not claim verified; "
                "verified entries are evidence-gated in harnesses.json"
            )
        if entry.get("recheck") is None:
            report.fail(f"{EXPECTATIONS_REL}: harness_versions.{product} must state the re-verification rule")

    proc = subprocess.run(
        [sys.executable, str(tool), "--selfcheck"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    if proc.returncode != 0:
        report.fail(
            f"compatibility: probe selfcheck failed (exit {proc.returncode}): {tail[0][:200]}"
        )
    else:
        report.note("compatibility: probe-engine selfcheck passed")
