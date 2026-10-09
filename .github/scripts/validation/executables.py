"""Deterministic executable example checks: lifecycle fixtures and challenger smoke."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .evals import run_research_selfcheck

BRIDGE_REL = "examples/opencode-lifecycle/opencode_bridge.py"
CHALLENGER_REL = "examples/agent-campaign/coverage_challenger.py"
RUN_FIXTURE_REL = "examples/opencode-lifecycle/run_fixture.py"
RUN_TRIAL_REL = "examples/fresh-agent-evals/run_trial.py"
CODEX_FIXTURE_REL = "examples/codex-lifecycle/run_fixture.py"
NEXT_ACTION_REL = "scripts/research_next.py"
RESEARCH_RUNNER_REL = "examples/fresh-agent-evals/research/runner.py"
START_HERE_REL = "scripts/start_here.py"
BACKGROUND_AGENT_REL = "scripts/background_agent.py"
BACKGROUND_ADAPTER_SELFCHECK_REL = "scripts/background_adapters/cli.py"
BACKGROUND_CONFORMANCE_REL = "examples/background-agent/conformance.py"
BACKGROUND_PROOF_REL = "examples/background-agent/proof.py"
FIRST_CONTRIBUTION_SMOKE_REL = ".github/scripts/smoke_first_contribution.py"


def _run(argv: list[str], timeout: int = 600, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)


def check_executables(report, root: Path) -> None:
    contribution_smoke = root / FIRST_CONTRIBUTION_SMOKE_REL
    if not contribution_smoke.is_file():
        report.fail(f"executables: missing {FIRST_CONTRIBUTION_SMOKE_REL}")
    else:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = _run(
            [sys.executable, str(contribution_smoke)],
            timeout=300,
            env=env,
        )
        if proc.returncode != 0:
            report.fail("executables: isolated first-contribution smoke failed")
        else:
            try:
                smoke = json.loads(proc.stdout)
            except json.JSONDecodeError:
                report.fail("executables: isolated first-contribution smoke did not emit JSON")
            else:
                process_labels = [
                    item.get("label") for item in smoke.get("processes", [])
                    if isinstance(item, dict)
                ]
                tracked_ignore = smoke.get("tracked_ignored_regression")
                if (
                    smoke.get("status") != "passed"
                    or smoke.get("source_unchanged") is not True
                    or smoke.get("source_git_index_unchanged") is not True
                    or smoke.get("copy_is_outside_source") is not True
                    or smoke.get("route_loaded_from_copy") is not True
                    or smoke.get("task_inputs_loaded_from_copy") is not True
                    or smoke.get("incomplete_contribution_rejected") is not True
                    or not isinstance(tracked_ignore, dict)
                    or tracked_ignore.get("status") != "passed"
                    or tracked_ignore.get("source_links_exit") != 0
                    or tracked_ignore.get("ignore_rule_exit") != 0
                    or tracked_ignore.get("tracked_index_exit") != 0
                    or tracked_ignore.get("full_smoke_exit") != 0
                    or tracked_ignore.get("baseline_validator_exit") != 0
                    or tracked_ignore.get("final_validator_exit") != 0
                    or tracked_ignore.get("source_index_unchanged") is not True
                    or tracked_ignore.get("full_completion_smoke_passed") is not True
                    or not process_labels
                    or process_labels[-1] != "successful completion checker"
                ):
                    report.fail("executables: isolated first-contribution smoke evidence is incomplete")
                else:
                    report.note(
                        "executables: public-only first-contribution copy passed its route, validators, "
                        "incomplete rejection, tracked-ignore regression, and final completion checker"
                    )

    start_here = root / START_HERE_REL
    if not start_here.is_file():
        report.fail(f"executables: missing {START_HERE_REL}")
    else:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = _run([sys.executable, str(start_here), "--selfcheck"], timeout=180, env=env)
        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
            report.fail(f"executables: {START_HERE_REL} completion/route selfcheck failed: {detail[0][:240]}")
        else:
            report.note("executables: startup fixture contracts and read-only completion checker passed offline")

    research_runner = root / RESEARCH_RUNNER_REL
    if not research_runner.is_file():
        report.fail(f"executables: missing {RESEARCH_RUNNER_REL}")
    else:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = _run([sys.executable, str(research_runner), "--help"], timeout=60, env=env)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
            report.fail(f"executables: {RESEARCH_RUNNER_REL} --help failed: {tail[0][:200]}")
        else:
            report.note("executables: paper-backed research CLI help loaded offline; no harness launched")
    run_research_selfcheck(report, root, caller="executables")

    for relpath in (RUN_FIXTURE_REL, RUN_TRIAL_REL, CODEX_FIXTURE_REL):
        runner = root / relpath
        if not runner.is_file():
            report.fail(f"executables: missing {relpath}")
            continue
        proc = _run([sys.executable, str(runner), "--help"], timeout=60)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
            report.fail(f"executables: {relpath} --help failed (exit {proc.returncode}): {tail[0][:200]}")
        else:
            report.note(f"executables: {relpath} --help loaded offline (no agent launched)")

    next_action = root / NEXT_ACTION_REL
    if not next_action.is_file():
        report.fail(f"executables: missing {NEXT_ACTION_REL}")
    else:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = _run([sys.executable, str(next_action), "--selfcheck"], timeout=120, env=env)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
            report.fail(f"executables: {NEXT_ACTION_REL} selfcheck failed (exit {proc.returncode}): {tail[0][:240]}")
        else:
            report.note("executables: research next-action deterministic offline selfcheck passed (no package, network, worker, or model used)")

    codex_fixture = root / CODEX_FIXTURE_REL
    if not codex_fixture.is_file():
        report.fail(f"executables: missing {CODEX_FIXTURE_REL}")
    else:
        proc = _run([sys.executable, str(codex_fixture), "--selfcheck"], timeout=120)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
            report.fail(
                f"executables: {CODEX_FIXTURE_REL} inert selfcheck failed (exit {proc.returncode}): {tail[0][:200]}"
            )
        else:
            report.note(
                "executables: Codex lifecycle fixture inert-synthetic-selfcheck passed (no Codex process or model launched)"
            )

    bridge = root / BRIDGE_REL
    if not bridge.is_file():
        report.fail(f"executables: missing {BRIDGE_REL}")
    else:
        proc = _run([sys.executable, str(bridge), "selfcheck"])
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
            report.fail(
                f"executables: {BRIDGE_REL} selfcheck failed (exit {proc.returncode}): {tail[0][:200]}"
            )
        else:
            report.note("executables: opencode lifecycle bridge selfcheck passed with inert fixtures")

    scratch = root / ".tmp"
    if scratch.is_symlink():
        report.fail("executables: background-adapter scratch root must not be a symlink")
    else:
        try:
            scratch.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            report.fail("executables: background-adapter .tmp scratch root is unavailable")
        else:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(scratch)}
            for relpath, command, timeout, label in (
                (BACKGROUND_AGENT_REL, "selfcheck", 300, "inert background-agent runtime"),
                (BACKGROUND_ADAPTER_SELFCHECK_REL, "selfcheck", 120, "synthetic adapter contract"),
            ):
                runner = root / relpath
                if not runner.is_file():
                    report.fail(f"executables: missing {relpath}")
                    continue
                proc = _run([sys.executable, str(runner), command], timeout=timeout, env=env)
                if proc.returncode != 0:
                    detail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
                    report.fail(f"executables: {relpath} {command} failed: {detail[0][:240]}")
                else:
                    report.note(f"executables: {label} passed; synthetic/inert only, no harness/model launched")

            conformance = root / BACKGROUND_CONFORMANCE_REL
            if not conformance.is_file():
                report.fail(f"executables: missing {BACKGROUND_CONFORMANCE_REL}")
            else:
                proc = _run(
                    [sys.executable, str(conformance), "--selfcheck"],
                    timeout=120,
                    env=env,
                )
                if proc.returncode != 0:
                    detail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
                    report.fail(f"executables: background conformance selfcheck failed: {detail[0][:240]}")
                else:
                    report.note("executables: background lifecycle conformance edges passed with synthetic fixtures only")

            proof = root / BACKGROUND_PROOF_REL
            if not proof.is_file():
                report.fail(f"executables: missing {BACKGROUND_PROOF_REL}")
            else:
                proc = _run([sys.executable, str(proof), "--help"], timeout=60, env=env)
                if proc.returncode != 0:
                    detail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no output"]
                    report.fail(f"executables: background proof --help failed: {detail[0][:240]}")
                else:
                    report.note("executables: background proof CLI help loaded offline; no native/model session launched")

    challenger = root / CHALLENGER_REL
    if not challenger.is_file():
        report.fail(f"executables: missing {CHALLENGER_REL}")
        return
    stub_proc = _run([sys.executable, str(challenger), "--stub-check"], timeout=120)
    if stub_proc.returncode != 0:
        tail = (stub_proc.stdout + stub_proc.stderr).strip().splitlines()[-1:] or [""]
        report.fail(f"executables: challenger stub-check failed (exit {stub_proc.returncode}): {tail[0][:200]}")
    else:
        try:
            stub = json.loads(stub_proc.stdout)
        except json.JSONDecodeError as exc:
            report.fail(f"executables: challenger stub-check did not emit JSON: {exc}")
            stub = None
        if stub is not None:
            if stub.get("all_passed") is not True:
                report.fail(f"executables: challenger stub-check failed: {stub.get('checks')}")
            if stub.get("claim") != "stub-stream-integration-not-a-library-evaluation":
                report.fail("executables: stub-check must be labeled a stub-stream integration test")
            if not report.errors:
                report.note("executables: challenger stub-check exercised shared accounting with controlled streams")
    with tempfile.TemporaryDirectory(prefix="of-challenger-validate-") as tmp:
        proc = _run(
            [
                sys.executable,
                str(challenger),
                "--smoke",
                "--target-evaluations",
                "400",
                "--checkpoint-dir",
                tmp,
            ]
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
            report.fail(
                f"executables: {CHALLENGER_REL} smoke failed (exit {proc.returncode}): {tail[0][:200]}"
            )
            return
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            report.fail(f"executables: challenger smoke did not emit JSON: {exc}")
            return

    problems = []
    if data.get("data_origin") != "synthetic-fixture" or data.get("claim") != "not-a-measured-result":
        problems.append("smoke report must stay labeled synthetic-fixture / not-a-measured-result")
    comparison = data.get("comparison") or {}
    if comparison.get("equal_completed_evaluations") is not True:
        problems.append("baseline and challenger must compare at equal completed evaluations")
    if comparison.get("baseline_evaluations") != comparison.get("challenger_evaluations"):
        problems.append("equal-count comparison counts differ")
    resume = data.get("checkpoint_resume") or {}
    if resume.get("performed") is not True or resume.get("stream_reproduced") is not True:
        problems.append("checkpoint/resume did not reproduce the uninterrupted stream")
    if resume.get("cumulative_totals_match") is not True:
        problems.append("checkpoint/resume did not reconcile cumulative totals after resume")
    proposals = data.get("proposals_vs_evaluations") or {}
    if (proposals.get("baseline_accepted_proposals") is None
            or proposals.get("baseline_completed_evaluations") is None):
        problems.append("proposal/evaluation accounting is missing")
    dedup = data.get("deduplication") or {}
    if proposals.get("challenger_accepted_proposals") != dedup.get("challenger_history_hashes"):
        problems.append("accepted-proposal count and persisted history disagree")
    bookkeeping = data.get("dependency_bookkeeping") or {}
    cell_counts = bookkeeping.get("challenger_target_cell_counts") or {}
    if sum(cell_counts.values()) != proposals.get("challenger_accepted_proposals"):
        problems.append("per-target-cell counts do not sum to accepted proposals")
    if "exhausted_cells" not in bookkeeping and "challenger_exhausted_cells" not in bookkeeping:
        problems.append("exhausted-cell scheduling state is not reported separately")
    if "challenger_stopped_reason" not in bookkeeping:
        problems.append("policy stop reason is not reported")
    if "100000" in json.dumps(comparison) or data.get("reference_target_note") is None:
        problems.append("smoke must not claim the 100000 completion target")
    ga_status = data.get("ga_status") or ""
    if "callable templates.random_weighted" not in ga_status or "no full GA benchmark has run" not in ga_status:
        problems.append("smoke must report the installed GA callable and the unbenchmarked GA status honestly")
    policy_core = data.get("policy_core") or {}
    if policy_core.get("shared_by_smoke_and_live") is not True:
        problems.append("smoke and live must share the stratified policy core")

    for problem in problems:
        report.fail(f"executables: challenger smoke: {problem}")
    if not problems:
        report.note("executables: challenger smoke, dependency bookkeeping, resume, and equal-count report passed")
