#!/usr/bin/env python3
"""Offline synthetic regressions; never launch a harness or model."""

from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "opencode-lifecycle"))

import manifest  # noqa: E402
from commands import (  # noqa: E402
    RESERVED_NAMES, classify_shell_segment, command_semantic_key, direct_suite_access, filename_discovery_scope,
    is_completion_json_helper,
    is_completion_checker_segment,
    validator_modes,
)
from onboarding.completion import (  # noqa: E402
    SCRATCH_REL,
    load_fixture_contract,
    render_finish_commands,
    selfcheck_problems as completion_selfcheck_problems,
)
from comparison import ComparisonError, assert_same_prepared_input, assert_snapshot_pin  # noqa: E402
from evidence import (  # noqa: E402
    access_observations_equivalent, command_sha256_matches,
    content_read_observations, entry_route_evidence,
    explicit_exit_code, observed_route_sources,
    normalize_access_observations, suite_accesses, validator_observations,
)
from diagnostics import (  # noqa: E402
    build_failure_diagnostics,
    research_comparison_diagnostic,
    runtime_unavailable_diagnostic,
)
from grader import (  # noqa: E402
    COMPLETION_REL, GradeError, _check_completion_claims, _check_validation_claim,
    _check_final_completion_sequence,
    _direct_access_audit,
    _direct_access_audit_complete,
    _find_action_by_declared_command, _path_write_sequences, _setup_metrics,
    _fixture_content_access_problems, _forbidden_action_problems,
    _validation_outcome_for_action, _write_scope_problems,
    grade_trial,
)
from native_events import _native_directory_frame, normalize_codex, normalize_opencode  # noqa: E402
from native_arguments import validate_native_arguments  # noqa: E402
from opencode_profile import (  # noqa: E402
    DENIED as OPENCODE_DENIED,
    EXPECTED as OPENCODE_EXPECTED,
    PERMISSION as OPENCODE_PERMISSION,
    PROFILE_CONFIG as OPENCODE_PROFILE_CONFIG,
    effective_agent_matrix,
)
from paths import (  # noqa: E402
    PathSafetyError, exclusive_json, make_exclusive_directory,
    mkdir_chain_no_symlinks, raw_file_exclusive, safe_relative,
)
from processes import run_bounded  # noqa: E402
from schema import read_json, validate_completion  # noqa: E402
from semantic import canonical_primary_seed_source, fixture_correction_problem  # noqa: E402
from shell_parser import MAX_COMMAND_BYTES, parse_shell_command  # noqa: E402
from snapshot import (  # noqa: E402
    SnapshotError,
    copy_public_tree,
    file_map,
    initialize_public_workspace,
    prepare_snapshot,
)
from runner import render_prompt as render_docs_prompt  # noqa: E402


def _valid_completion(case_id: str, target: str, command: str) -> dict:
    modes, _error = validator_modes(command, REPO, "opencode")
    modes = modes or ["truthfulness"]
    return {
        "schema_version": 2,
        "case_id": case_id,
        "route": {
            "selected_task": "contribute",
            "offline_contribution_access": "not_required",
            "live_campaign_authorization": "explicit_user_authorization_required",
            "authoritative_sources": ["README.md", "docs/agents/first-contribution.md"],
        },
        "contribution": {"kind": "documentation_correction", "changed_paths": [target]},
        "validation": {
            "baseline": {"command": command, "modes": modes, "exit_code": 1, "result": "failed"},
            "final": {"command": command, "modes": modes, "exit_code": 0, "result": "passed"},
        },
        "human_review": {"summary": "A precise page correction.", "rationale": "Restored the documented contract."},
    }


def _event(tool: str, call_id: str, *, path: str | None = None, command: str | None = None,
           exit_code: int | None = None, status: str = "completed", stamp: float = 1_800_000_000.0,
           output: object | None = None) -> dict:
    input_data = {"filePath": path} if path is not None else {"command": command}
    state = {"status": status, "input": input_data, "timestamp": stamp}
    if tool in {"read", "grep"}:
        if output is None and tool == "read" and path is not None:
            output = (
                f"<path>{path}</path>\n<type>file</type>\n<content>\n"
                "1: synthetic bounded content output\n"
                "(End of file - total 1 lines)\n</content>"
            )
        state["output"] = output if output is not None else "synthetic bounded content output"
    elif tool in {"bash", "shell"}:
        state["output"] = output if output is not None else "synthetic bounded command output"
    if exit_code is not None:
        state["metadata"] = {"exit": exit_code}
    return {"type": "tool_use", "timestamp": stamp,
            "part": {"type": "tool", "tool": tool, "callID": call_id, "state": state}}


def _structured_event(
    tool: str,
    call_id: str,
    arguments: dict,
    *,
    status: str = "completed",
    output: object = "synthetic bounded output",
) -> dict:
    return {
        "type": "tool_use",
        "part": {
            "type": "tool", "tool": tool, "callID": call_id,
            "state": {"status": status, "input": arguments, "output": output},
        },
    }


def _shell_trace(
    script: str, harness: str, call_id: str, workspace: Path,
    *, output: object | None = None, exit_code: int = 0,
) -> tuple[dict, str]:
    if harness == "opencode":
        command = script
        trace = normalize_opencode(
            (json.dumps(_event("bash", call_id, command=command, exit_code=exit_code, output=output)) + "\n").encode(),
            workspace, REPO,
        )
    else:
        command = f"/bin/bash -lc {shlex.quote(script)}"
        item = {
            "id": call_id, "type": "command_execution", "command": command,
            "status": "completed", "exit_code": exit_code,
        }
        if output is not None:
            item["aggregated_output"] = output
        event = {
            "type": "item.completed",
            "item": item,
        }
        trace = normalize_codex((json.dumps(event) + "\n").encode(), workspace, REPO)
    return trace, command


def _unclassified_counts_are_zero(trace: dict) -> bool:
    return all(trace.get(name) in (None, 0) for name in ("unclassified_item_count", "unclassified_tool_count"))


def _parser_regressions(failures: list[str]) -> None:
    valid = "/bin/bash -lc 'python .github/scripts/validate_agent_docs.py truthfulness'"
    modes, error = validator_modes(valid, Path("/tmp/v2-workspace"), "codex")
    if modes != ["truthfulness"] or error:
        failures.append("Codex exact bash -lc validator wrapper was not normalized")
    workspace = Path("/tmp/v2-workspace")
    for harness in ("opencode", "codex"):
        for script in (
            "ls -la examples/fresh-agent-evals/research",
            "ls -lAh -- examples/fresh-agent-evals/research",
            "ls -- -la",
        ):
            command = script if harness == "opencode" else f"/bin/bash -lc {shlex.quote(script)}"
            parsed_ls = parse_shell_command(command, harness)
            scope = (
                filename_discovery_scope(parsed_ls.segments[0], workspace)
                if parsed_ls is not None and len(parsed_ls.segments) == 1 else None
            )
            if (
                parsed_ls is None
                or len(parsed_ls.segments) != 1
                or classify_shell_segment(parsed_ls.segments[0], workspace) != "inspection"
                or scope is None
            ):
                failures.append(f"bounded combined no-argument ls flags were rejected: {harness}: {script}")
                break
            if script == "ls -- -la" and scope.get("paths") != ["-la"]:
                failures.append("ls -- did not preserve a dash-prefixed path as a positional operand")
                break
    for malformed_ls in (
        "ls -lZ examples/fresh-agent-evals",
        "ls '-I*.md' examples/fresh-agent-evals",
        "ls --color=always examples/fresh-agent-evals",
        "ls -w 80 examples/fresh-agent-evals",
    ):
        parsed_ls = parse_shell_command(malformed_ls, "opencode")
        if (
            parsed_ls is None
            or filename_discovery_scope(parsed_ls.segments[0], workspace) is not None
            or classify_shell_segment(parsed_ls.segments[0], workspace) is not None
        ):
            failures.append(f"unknown or value-taking ls option escaped the bounded grammar: {malformed_ls}")
            break
    if not direct_suite_access({"tool": "bash", "command": "cat oracle.json"}, "opencode", workspace):
        failures.append("direct oracle filename access was not audited")
    if direct_suite_access(
        {"tool": "bash", "command": "ls examples/fresh-agent-evals/v2/oracle.json"},
        "opencode", workspace,
    ):
        failures.append("filename-only shell listing was treated as reading protected contents")
    if direct_suite_access(
        {"tool": "bash", "command": "python .github/scripts/validate_agent_docs.py all"},
        "opencode", workspace,
    ):
        failures.append("legitimate full-validator execution was misclassified as direct suite access")

    joined_reserved_commands = (
        "cat o''racle.json",
        "pwd && cat o''racle.json",
        r"cat ora\cle.json",
        r"pwd && cat ora\cle.json",
    )
    for harness in ("opencode", "codex"):
        for index, script in enumerate(joined_reserved_commands):
            trace, command = _shell_trace(script, harness, f"joined-oracle-{harness}-{index}", workspace)
            action = trace["actions"][0]
            parsed = parse_shell_command(command, harness)
            expected_segments = 2 if "&&" in script else 1
            if (
                parsed is None
                or len(parsed.segments) != expected_segments
                or not any("oracle.json" in segment.argv for segment in parsed.segments)
                or not trace.get("trace_complete")
                or not _unclassified_counts_are_zero(trace)
                or action.get("command_truncated") is not False
                or action.get("command_bytes") != len(command.encode())
                or not command_sha256_matches(action)
                or not _direct_access_audit_complete(trace, trace["actions"], harness, workspace)
                or not direct_suite_access(action, harness, workspace)
            ):
                failures.append(
                    f"parsed reserved path hidden by shell quote concatenation/escape was not detected: {harness}: {script}"
                )

    joined_suite_commands = (
        "cat examples/fresh-agent-eva''ls/v2/commands.py",
        r"cat examples/fresh-agent-eva\ls/v2/commands.py",
        "pwd && cat examples/fresh-agent-eva''ls/v2/commands.py",
        r"pwd && cat examples/fresh-agent-eva\ls/v2/commands.py",
    )
    for harness in ("opencode", "codex"):
        for index, script in enumerate(joined_suite_commands):
            trace, command = _shell_trace(script, harness, f"joined-suite-{harness}-{index}", workspace)
            action = trace["actions"][0]
            parsed = parse_shell_command(command, harness)
            expected_segments = 2 if "&&" in script else 1
            if (
                parsed is None
                or len(parsed.segments) != expected_segments
                or not any("examples/fresh-agent-evals/v2/commands.py" in segment.argv for segment in parsed.segments)
                or not _direct_access_audit_complete(trace, trace["actions"], harness, workspace)
                or not direct_suite_access(action, harness, workspace)
            ):
                failures.append(
                    f"parsed suite path hidden by shell quote concatenation/escape was not detected: {harness}: {script}"
                )

    computed_reader = "awk 'BEGIN {p=\"ora\" \"cle.json\"; getline x < p; print x}'"
    for harness in ("opencode", "codex"):
        trace, command = _shell_trace(computed_reader, harness, f"computed-oracle-{harness}", workspace)
        action = trace["actions"][0]
        if (
            parse_shell_command(command, harness) is None
            or not trace.get("trace_complete")
            or not _unclassified_counts_are_zero(trace)
            or action.get("command_truncated") is not False
            or action.get("command_bytes") != len(command.encode())
            or not command_sha256_matches(action)
            or explicit_exit_code(action, harness) != 0
            or direct_suite_access(action, harness, workspace)
            or _direct_access_audit_complete(trace, trace["actions"], harness, workspace)
        ):
            failures.append(
                f"dynamically computed path in an unclassified interpreter did not leave audit unknown: {harness}"
            )

    for harness in ("opencode", "codex"):
        for index, (script, expected_segments) in enumerate((
            ("grep 'left && right; ||| literal' README.md", 1),
            ("grep 'left && right; ||| literal' README.md && pwd", 2),
            ("rg -- 'left && right; ||| literal' README.md", 1),
            ("rg -- 'left && right; ||| literal' README.md && pwd", 2),
        )):
            trace, command = _shell_trace(script, harness, f"quoted-operators-{harness}-{index}", workspace)
            parsed = parse_shell_command(command, harness)
            if (
                parsed is None
                or len(parsed.segments) != expected_segments
                or not trace.get("trace_complete")
                or not _direct_access_audit_complete(trace, trace["actions"], harness, workspace)
                or direct_suite_access(trace["actions"][0], harness, workspace)
            ):
                failures.append(f"quoted literal control operators were not preserved as harmless argv: {harness}")

    local_helpers = (
        "python -m json.tool trial-output/completion.json",
        "python3 -m json.tool trial-output/completion.json",
        "PYTHONDONTWRITEBYTECODE=1 python -m json.tool trial-output/completion.json",
        "git branch --show-current",
        "python scripts/start_here.py --task contribute",
        "python scripts/start_here.py --task=contribute --format=json",
        "python scripts/start_here.py --task contribute --fixture first-finished-contribution-primary",
        "python scripts/start_here.py --task contribute --fixture first-finished-contribution-primary --check-completion",
        "python scripts/start_here.py --task research --fixture paper-backed-coverage-challenger --series-manifest examples/fresh-agent-evals/research/series/selfcheck-only.json --check-completion",
    )
    for index, helper in enumerate(local_helpers):
        trace = normalize_opencode(
            (json.dumps(_event("bash", f"local-helper-{index}", command=helper, exit_code=0)) + "\n").encode(),
            workspace, REPO,
        )
        if not _direct_access_audit_complete(trace, trace["actions"], "opencode", workspace):
            failures.append(f"exact harmless local helper was not admitted: {helper}")
        if _setup_metrics(trace["actions"], "opencode", workspace, True, 0)["unnecessary_setup_attempts"]["status"] != "observed_complete":
            failures.append(f"exact harmless local helper left setup observations unknown: {helper}")
        if validator_modes(helper, workspace, "opencode")[0] is not None:
            failures.append(f"local inspection helper was misclassified as repository validation: {helper}")
        if "--check-completion" in helper:
            segments = parse_shell_command(helper, "opencode")
            if segments is None or len(segments.segments) != 1 or not is_completion_checker_segment(segments.segments[0]):
                failures.append("exact task-bound completion checker was not recognized by the native command grammar")
        if "json.tool" in helper:
            if not is_completion_json_helper(helper, "opencode"):
                failures.append("exact prefixed JSON-artifact read was not recognized for OpenCode")
            if not is_completion_json_helper(f'/bin/bash -lc "{helper}"', "codex"):
                failures.append("exact prefixed JSON-artifact read was not recognized through Codex bash -lc")
        codex_event = {
            "type": "item.completed",
            "item": {
                "id": f"codex-local-helper-{index}", "type": "command_execution",
                "command": f'/bin/bash -lc "{helper}"', "status": "completed", "exit_code": 0,
            },
        }
        codex_trace = normalize_codex((json.dumps(codex_event) + "\n").encode(), workspace, REPO)
        if not _direct_access_audit_complete(codex_trace, codex_trace["actions"], "codex", workspace):
            failures.append(f"exact Codex harmless local helper was not admitted: {helper}")

    unadmitted_helpers = (
        "python -m json.tool trial-output/other.json",
        "python -m json.tool trial-output/completion.json output.json",
        "python -m json.tool --sort-keys trial-output/completion.json",
        "python -m json.tool trial-output/completion.json > /dev/null",
        "PYTHONPATH=. python -m json.tool trial-output/completion.json",
        "PYTHONHOME=/tmp python -m json.tool trial-output/completion.json",
        "CUSTOM_FLAG=1 python -m json.tool trial-output/completion.json",
        "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m json.tool trial-output/completion.json",
        "env -i PYTHONDONTWRITEBYTECODE=1 python -m json.tool trial-output/completion.json",
        "python -c 'print(1)'",
        computed_reader,
        "less README.md",
        "more README.md",
        "rg --pre cat README.md",
        "rg --pre=cat README.md",
        "sort --compress-program=cat README.md",
        "sort --compress-prog=cat README.md",
        "git branch",
        "git branch --show-current --all",
        "git branch --delete old-branch",
        "git checkout other-branch",
        "git switch other-branch",
        "python scripts/start_here.py --generate",
        "python scripts/start_here.py --task",
        "python scripts/start_here.py --task unrecognized",
        "python scripts/start_here.py --task contribute --repo ../outside",
        "python scripts/start_here.py --task research --fixture first-finished-contribution-primary --check-completion",
        "python scripts/start_here.py --task research --fixture paper-backed-coverage-challenger --check-completion",
        "python scripts/start_here.py --task research --fixture paper-backed-coverage-challenger --series-manifest ../outside.json --check-completion",
        "python scripts/start_here.py --task contribute --fixture first-finished-contribution-primary --series-manifest examples/fresh-agent-evals/research/series/any.json --check-completion",
        "git status -- ../outside",
        "git ls-files -- ../outside",
        "git diff -- ../outside",
        "git diff --output=report.txt",
        "sort -o report.txt README.md",
    )
    for index, helper in enumerate(unadmitted_helpers):
        trace = normalize_opencode(
            (json.dumps(_event("bash", f"unadmitted-helper-{index}", command=helper, exit_code=0)) + "\n").encode(),
            workspace, REPO,
        )
        if _direct_access_audit_complete(trace, trace["actions"], "opencode", workspace):
            failures.append(f"neighboring or mutating helper form was admitted: {helper}")
    for masked in (
        "/bin/bash -lc 'python .github/scripts/validate_agent_docs.py truthfulness; true'",
        "/bin/bash -lc 'python .github/scripts/validate_agent_docs.py truthfulness | true'",
        "/bin/bash -lc 'python .github/scripts/validate_agent_docs.py truthfulness'&&true",
        "python .github/scripts/validate_agent_docs.py truthfulness || true",
        "python .github/scripts/validate_agent_docs.py truthfulness | true",
        "python .github/scripts/validate_agent_docs.py truthfulness > /dev/null",
        "python .github/scripts/validate_agent_docs.py truthfulness & true",
        "python .github/scripts/validate_agent_docs.py truthfulness && $(true)",
        "python .github/scripts/validate_agent_docs.py truthfulness && bash -c true",
        "python .github/scripts/validate_agent_docs.py truthfulness && cat <REDACTED>",
        "python .github/scripts/validate_agent_docs.py truthfulness && 'unterminated",
    ):
        if validator_modes(masked, Path("/tmp/v2-workspace"), "codex")[0] is not None:
            failures.append("compound, piped, or masked validator command was accepted")

    direct_chain = (
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py "
        "truthfulness links && git diff --check"
    )
    direct_modes, direct_error = validator_modes(direct_chain, workspace, "opencode")
    if direct_modes != ["truthfulness", "links"] or direct_error:
        failures.append("harmless OpenCode environment prefix and exact AND chain were not recognized")
    codex_chain = f"/bin/sh -lc '{direct_chain}'"
    codex_modes, codex_error = validator_modes(codex_chain, workspace, "codex")
    if codex_modes != ["truthfulness", "links"] or codex_error:
        failures.append("exact Codex sh -lc wrapper did not admit the bounded validator AND chain")
    if validator_modes(direct_chain, workspace, "codex")[0] != ["truthfulness", "links"]:
        failures.append("Codex completion declaration did not accept its full inner AND command")
    if validator_modes(
        "python -c 'print(\".github/scripts/validate_agent_docs.py truthfulness\")'",
        workspace, "opencode",
    )[0] is not None:
        failures.append("validator text inside an arbitrary Python script was substring-matched")

    quoted = parse_shell_command("grep 'left && right; ||| literal' README.md", "opencode")
    if quoted is None or len(quoted.segments) != 1 or quoted.segments[0].argv != (
        "grep", "left && right; ||| literal", "README.md",
    ):
        failures.append("quoted shell operators were split or lost their literal meaning")
    if validator_modes("eval 'python .github/scripts/validate_agent_docs.py truthfulness'", workspace, "opencode")[0] is not None:
        failures.append("eval or an unclassified nested command was admitted as validator execution")
    for malformed in (None, 1, {}, ["python", "validator"]):
        if validator_modes(malformed, workspace, "opencode")[0] is not None:
            failures.append("a malformed command type was admitted by validator recognition")
            break

    for hidden_access in (
        "python .github/scripts/validate_agent_docs.py truthfulness && cat oracle.json",
        "python -c 'open(\"examples/fresh-agent-evals/v2/oracle.json\")' && true",
    ):
        if not direct_suite_access({"tool": "bash", "command": hidden_access}, "opencode", workspace):
            failures.append("direct suite access hidden in a compound command was not audited")
    if validator_modes(
        "python .github/scripts/validate_agent_docs.py truthfulness && cat o''racle.json",
        workspace, "opencode",
    )[0] is not None:
        failures.append("validator recognizer accepted quote-joined direct suite access")

    opencode_chain_trace = normalize_opencode(
        (json.dumps(_event("bash", "opencode-chain", command=direct_chain, exit_code=0)) + "\n").encode(),
        workspace, REPO,
    )
    opencode_chain_action = opencode_chain_trace["actions"][0]
    opencode_chain_observations = validator_observations(
        [opencode_chain_action], "opencode", workspace,
    )
    if (
        not _direct_access_audit_complete(opencode_chain_trace, opencode_chain_trace["actions"], "opencode", workspace)
        or len(opencode_chain_observations) != 1
        or opencode_chain_observations[0]["exit_code"] != 0
        or opencode_chain_observations[0]["compound_shell_exit_code"] != 0
        or opencode_chain_observations[0]["inference_basis"] != "explicit_native_exit_zero_for_fully_admitted_and_chain"
        or not command_semantic_key(direct_chain, "opencode")
        or not command_sha256_matches(opencode_chain_action)
    ):
        failures.append("complete OpenCode chain did not preserve full command and inferred segment outcome provenance")

    codex_chain_event = {
        "type": "item.completed",
        "item": {
            "id": "codex-chain-1", "type": "command_execution",
            "command": codex_chain, "status": "completed", "exit_code": 0,
        },
    }
    codex_chain_trace = normalize_codex((json.dumps(codex_chain_event) + "\n").encode(), workspace, REPO)
    codex_chain_action = codex_chain_trace["actions"][0]
    codex_chain_observations = validator_observations([codex_chain_action], "codex", workspace)
    if (
        not _direct_access_audit_complete(codex_chain_trace, codex_chain_trace["actions"], "codex", workspace)
        or len(codex_chain_observations) != 1
        or codex_chain_observations[0]["exit_code"] != 0
        or codex_chain_observations[0]["compound_shell_exit_code"] != 0
        or codex_chain_action.get("command_sha256") is None
        or codex_chain_action.get("native_item_id") != "codex-chain-1"
        or codex_chain_action.get("completion_sequence") is None
        or not command_sha256_matches(codex_chain_action)
        or codex_chain_action.get("command_sha256") != hashlib.sha256(codex_chain.encode()).hexdigest()
        or codex_chain_action.get("command_bytes") != len(codex_chain.encode())
    ):
        failures.append("Codex wrapper lost full command digest, native lifecycle identity, or chain inference")

    former_multi_validator_chain = (
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py truthfulness && "
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py links && "
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py evals && "
        "git diff --check"
    )
    former_modes, former_error = validator_modes(former_multi_validator_chain, workspace, "opencode")
    former_trace, _former_command = _shell_trace(
        former_multi_validator_chain, "opencode", "former-multi-validator-chain", workspace,
    )
    former_observations = validator_observations(former_trace["actions"], "opencode", workspace)
    if (
        former_modes != ["truthfulness", "links", "evals"]
        or former_error
        or len(former_observations) != 1
        or former_observations[0].get("validator_segment_indices") != [0, 1, 2]
        or former_observations[0].get("exit_code") != 0
        or former_observations[0].get("inference_basis") != "explicit_native_exit_zero_for_fully_admitted_and_chain"
    ):
        failures.append("the exact former three-validator AND chain did not preserve ordered segment evidence")

    nested_inner = f"/bin/bash -lc {shlex.quote(former_multi_validator_chain)}"
    nested_native = f"/bin/bash -lc {shlex.quote(nested_inner)}"
    nested_parsed = parse_shell_command(nested_native, "codex")
    nested_event = {
        "type": "item.completed",
        "item": {
            "id": "codex-observed-nested-chain", "type": "command_execution",
            "command": nested_native, "status": "completed", "exit_code": 0,
        },
    }
    nested_trace = normalize_codex((json.dumps(nested_event) + "\n").encode(), workspace, REPO)
    nested_action = nested_trace["actions"][0]
    nested_observations = validator_observations([nested_action], "codex", workspace)
    if (
        nested_parsed is None
        or nested_parsed.wrapper_depth != 2
        or nested_parsed.wrapper != "exact-codex-nested-login-shell"
        or validator_modes(former_multi_validator_chain, workspace, "codex")[0] != ["truthfulness", "links", "evals"]
        or len(nested_observations) != 1
        or nested_observations[0].get("validator_segment_indices") != [0, 1, 2]
        or nested_observations[0].get("exit_code") != 0
        or not command_sha256_matches(nested_action)
        or nested_action.get("command") != nested_native
    ):
        failures.append("observed nested Codex bash -lc wrapper lost full command or validator semantics")

    reserved_nested_script = (
        "python .github/scripts/validate_agent_docs.py truthfulness && cat o''racle.json"
    )
    reserved_nested = f"/bin/bash -lc {shlex.quote('/bin/bash -lc ' + shlex.quote(reserved_nested_script))}"
    if (
        parse_shell_command(reserved_nested, "codex") is None
        or not direct_suite_access({"tool": "command_execution", "command": reserved_nested}, "codex", workspace)
        or validator_modes(reserved_nested, workspace, "codex")[0] is not None
    ):
        failures.append("reserved-source access hidden in nested wrappers/quote concatenation was not preserved")

    too_deep = f"/bin/bash -lc {shlex.quote('bash -lc ' + shlex.quote('bash -lc ' + shlex.quote('pwd')))}"
    if parse_shell_command(too_deep, "codex") is not None:
        failures.append("Codex wrapper nesting beyond depth two was admitted")
    for unadmitted_chain in (
        "python .github/scripts/validate_agent_docs.py truthfulness && git diff --output=report.txt",
        "python .github/scripts/validate_agent_docs.py truthfulness && sort -o report.txt README.md",
        " && ".join(["true"] * 8 + ["python .github/scripts/validate_agent_docs.py truthfulness"]),
    ):
        if validator_modes(unadmitted_chain, workspace, "opencode")[0] is not None:
            failures.append("mutating or over-segment validator evidence escaped the finite shell grammar")
            break
    nonzero_former = normalize_opencode(
        (json.dumps(_event(
            "bash", "former-chain-nonzero", command=former_multi_validator_chain,
            exit_code=1,
        )) + "\n").encode(), workspace, REPO,
    )
    nonzero_observation = validator_observations(nonzero_former["actions"], "opencode", workspace)
    if (
        len(nonzero_observation) != 1
        or nonzero_observation[0].get("exit_code") is not None
        or nonzero_observation[0].get("inference_basis") != "and_chain_nonzero_exit_does_not_locate_validator_outcome"
    ):
        failures.append("nonzero multi-validator chain invented a failure for every validator")

    def codex_lifecycle_trace(identifier: str, native_command: str) -> dict:
        command_item = {
            "id": identifier,
            "type": "command_execution",
            "command": native_command,
        }
        lifecycle = [
            {"type": "thread.started", "thread_id": "thread-long-command"},
            {"type": "item.started", "item": {**command_item, "status": "in_progress"}},
            {"type": "item.completed", "item": {**command_item, "status": "completed", "exit_code": 0}},
        ]
        raw = b"".join((json.dumps(event) + "\n").encode() for event in lifecycle)
        return normalize_codex(raw, workspace, REPO)

    long_codex_inner = f"grep {shlex.quote('x' * 400)} README.md"
    long_codex_command = f"/bin/bash -lc {shlex.quote(long_codex_inner)}"
    long_codex_trace = codex_lifecycle_trace("codex-long-command", long_codex_command)
    long_codex_action = long_codex_trace["actions"][0]
    if (
        len(long_codex_command.encode("utf-8")) <= 300
        or len(long_codex_command.encode("utf-8")) > MAX_COMMAND_BYTES
        or long_codex_action.get("command") != long_codex_command
        or long_codex_action.get("command_truncated") is not False
        or long_codex_action.get("command_bytes") != len(long_codex_command.encode("utf-8"))
        or long_codex_action.get("command_sha256") != hashlib.sha256(long_codex_command.encode("utf-8")).hexdigest()
        or not command_sha256_matches(long_codex_action)
        or explicit_exit_code(long_codex_action, "codex") != 0
        or long_codex_action.get("event_sequence") != 1
        or long_codex_action.get("completion_sequence") != 2
        or long_codex_action.get("native_item_id") != "codex-long-command"
        or long_codex_action.get("normalized_command") != long_codex_inner
    ):
        failures.append("safely bounded Codex command over 300 bytes lost full UTF-8 text, digest, or lifecycle evidence")

    unicode_inner = f"grep {shlex.quote('é' * 160)} README.md"
    unicode_command = f"/bin/bash -lc {shlex.quote(unicode_inner)}"
    unicode_encoded = unicode_command.encode("utf-8")
    unicode_trace = codex_lifecycle_trace("codex-unicode-command", unicode_command)
    unicode_action = unicode_trace["actions"][0]
    if (
        len(unicode_command) >= MAX_COMMAND_BYTES
        or len(unicode_encoded) <= 300
        or len(unicode_encoded) > MAX_COMMAND_BYTES
        or unicode_action.get("command") != unicode_command
        or unicode_action.get("command_truncated") is not False
        or unicode_action.get("command_bytes") != len(unicode_encoded)
        or unicode_action.get("command_sha256") != hashlib.sha256(unicode_encoded).hexdigest()
        or not command_sha256_matches(unicode_action)
        or unicode_action.get("normalized_command") != unicode_inner
    ):
        failures.append("Codex command evidence used character length instead of bounded UTF-8 byte length")

    oversized_unicode_inner = f"grep {shlex.quote('é' * (MAX_COMMAND_BYTES // 2 + 100))} README.md"
    oversized_unicode_command = f"/bin/bash -lc {shlex.quote(oversized_unicode_inner)}"
    oversized_unicode_encoded = oversized_unicode_command.encode("utf-8")
    oversized_unicode_trace = codex_lifecycle_trace("codex-oversized-unicode", oversized_unicode_command)
    oversized_unicode_action = oversized_unicode_trace["actions"][0]
    if (
        len(oversized_unicode_command) >= MAX_COMMAND_BYTES
        or len(oversized_unicode_encoded) <= MAX_COMMAND_BYTES
        or oversized_unicode_action.get("command") is not None
        or oversized_unicode_action.get("command_truncated") is not True
        or oversized_unicode_action.get("command_bytes") != len(oversized_unicode_encoded)
        or oversized_unicode_action.get("command_sha256") != hashlib.sha256(oversized_unicode_encoded).hexdigest()
        or oversized_unicode_action.get("normalized_command") is not None
        or command_sha256_matches(oversized_unicode_action)
        or validator_observations([oversized_unicode_action], "codex", workspace)
    ):
        failures.append("over-bound Codex text measured in UTF-8 bytes was not left unknown")

    codex_ok = {
        "type": "item.completed",
        "item": {"id": "cmd-1", "type": "command_execution", "command": valid,
                 "status": "completed", "exit_code": 0},
    }
    normalized = normalize_codex((json.dumps(codex_ok) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)
    action = normalized["actions"][0]
    if action.get("normalized_command") != "python .github/scripts/validate_agent_docs.py truthfulness":
        failures.append("Codex wrapper did not retain the exact inner standalone command")
    if action.get("command_sha256") is None or action.get("command_bytes") is None or action.get("native_item_id") != "cmd-1":
        failures.append("Codex normalization lost native item identity or full command digest/length")
    if explicit_exit_code(action, "codex") != 0:
        failures.append("Codex explicit command_execution.exit_code was not retained")

    sed_command = "sed -n '1,160p' README.md"
    opencode_sed = normalize_opencode(
        (json.dumps(_event("bash", "sed-read", command=sed_command, exit_code=0)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    opencode_reads, _opencode_selector, opencode_read_complete = observed_route_sources(
        opencode_sed["actions"], "opencode", workspace
    )
    if (
        "README.md" in opencode_reads
        or not opencode_read_complete
        or not any(
            item.get("kind") == "shell_partial_content_read"
            for item in content_read_observations(opencode_sed["actions"], "opencode", workspace)
        )
    ):
        failures.append("partial OpenCode sed output was not kept separate from whole-source route reads")
    if not _direct_access_audit_complete(opencode_sed, opencode_sed["actions"], "opencode", workspace):
        failures.append("valid OpenCode sed range command was not classified by the access audit")
    with tempfile.TemporaryDirectory(prefix="of-v2-workspace-path-read-") as temporary:
        workspace_read_root = Path(temporary)
        (workspace_read_root / "README.md").write_text("workspace source\n", encoding="utf-8")
        workspace_cat = normalize_opencode(
            (json.dumps(_event(
                "bash", "workspace-path-read",
                command=f"cat {workspace_read_root.resolve()}/README.md", exit_code=0,
            )) + "\n").encode(),
            workspace_read_root, REPO,
        )
        workspace_reads, _workspace_selector, workspace_read_complete = observed_route_sources(
            workspace_cat["actions"], "opencode", workspace_read_root,
        )
        if (
            "README.md" not in workspace_reads
            or not workspace_read_complete
            or not _direct_access_audit_complete(
                workspace_cat, workspace_cat["actions"], "opencode", workspace_read_root,
            )
        ):
            failures.append("normalized <WORKSPACE> read path did not retain its safe workspace-relative meaning")
    failed_sed = normalize_opencode(
        (json.dumps(_event("bash", "sed-failed", command=sed_command, exit_code=1)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    failed_reads, _failed_selector, _ = observed_route_sources(
        failed_sed["actions"], "opencode", Path("/tmp/v2-workspace")
    )
    if "README.md" in failed_reads:
        failures.append("nonzero OpenCode shell read was counted as completed route evidence")
    codex_sed_event = {
        "type": "item.completed",
        "item": {"id": "sed-1", "type": "command_execution",
                 "command": "/bin/bash -lc \"sed -n '1,160p' README.md\"",
                 "status": "completed", "exit_code": 0,
                 "aggregated_output": "synthetic partial source output"},
    }
    codex_sed = normalize_codex((json.dumps(codex_sed_event) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)
    codex_reads, _codex_selector, codex_read_complete = observed_route_sources(
        codex_sed["actions"], "codex", workspace
    )
    if (
        "README.md" in codex_reads
        or not codex_read_complete
        or not any(
            item.get("kind") == "shell_partial_content_read"
            for item in content_read_observations(codex_sed["actions"], "codex", workspace)
        )
    ):
        failures.append("partial Codex sed output was not kept separate from whole-source route reads")
    if not _direct_access_audit_complete(codex_sed, codex_sed["actions"], "codex", workspace):
        failures.append("valid Codex sed range command was not classified by the access audit")
    unknown_sed_event = {
        "type": "item.completed",
        "item": {"id": "sed-2", "type": "command_execution",
                 "command": "/bin/bash -lc \"sed -n '/title/p' README.md\"",
                 "status": "completed", "exit_code": 0},
    }
    unknown_sed = normalize_codex((json.dumps(unknown_sed_event) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)
    unknown_reads, _unknown_selector, unknown_read_complete = observed_route_sources(
        unknown_sed["actions"], "codex", Path("/tmp/v2-workspace")
    )
    if "README.md" in unknown_reads or unknown_read_complete:
        failures.append("unsupported sed expression was fabricated as route-read evidence")
    if _direct_access_audit_complete(unknown_sed, unknown_sed["actions"], "codex", workspace):
        failures.append("unsupported sed expression yielded a complete direct-access audit")

    todo_event = {
        "type": "tool_use",
        "part": {"type": "tool", "tool": "todowrite", "callID": "todo-native",
                 "state": {"status": "completed", "input": {"todos": [{"content": "review"}]}}},
    }
    todo_actions = normalize_opencode((json.dumps(todo_event) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)["actions"]
    if _forbidden_action_problems(todo_actions, "opencode"):
        failures.append("verified OpenCode todowrite planning event was treated as forbidden execution")

    inherited_override = {
        "permission": {"*": "allow", "task": "allow", "webfetch": "allow"},
        "agent": {"build": {"name": "build", "permission": dict(OPENCODE_PERMISSION)}},
    }
    effective = effective_agent_matrix(json.dumps(inherited_override))
    if effective != OPENCODE_EXPECTED:
        failures.append("named OpenCode build-agent deny rules did not override inherited global allows")
    explicit_fixture_denials = {"question", "plan_enter", "plan_exit"}
    top_level_permission = OPENCODE_PROFILE_CONFIG["permission"]
    build_permission = OPENCODE_PROFILE_CONFIG["agent"]["build"]["permission"]
    for name in sorted(explicit_fixture_denials):
        if name not in OPENCODE_DENIED or OPENCODE_PERMISSION.get(name) != "deny":
            failures.append(f"OpenCode fixture policy omitted explicit {name} denial")
        if top_level_permission.get(name) != "deny" or build_permission.get(name) != "deny":
            failures.append(f"OpenCode {name} denial was not shared by top-level and build-agent config")
        missing_override = dict(OPENCODE_PERMISSION)
        missing_override.pop(name, None)
        missing_matrix = effective_agent_matrix(json.dumps({
            "agent": {"build": {"permission": missing_override}},
        }))
        if missing_matrix == OPENCODE_EXPECTED:
            failures.append(f"OpenCode exact matrix check did not detect a missing {name} override")

    from runner import main as runner_main, requested_argument
    requested_model = requested_argument("openrouter/openai/gpt-6-luna", "model")
    requested_variant = requested_argument("max", "variant")
    requested_codex_model = requested_argument("gpt-6-astra", "model")
    requested_reasoning = requested_argument("medium", "reasoning")
    if (
        requested_model.get("value") != "openrouter/openai/gpt-6-luna"
        or requested_variant.get("value") != "max"
        or requested_codex_model.get("value") != "gpt-6-astra"
        or requested_reasoning.get("value") != "medium"
        or any(item.get("backend_served_value") != "not_observed" for item in (
            requested_model, requested_variant, requested_codex_model, requested_reasoning
        ))
    ):
        failures.append("sanitized requested model/variant/reasoning observations were not retained accurately")
    unsafe_selector = requested_argument("https://provider.invalid/?api_key=secret", "model")
    if unsafe_selector.get("status") != "unknown" or "value" in unsafe_selector or not isinstance(unsafe_selector.get("sha256"), str):
        failures.append("unsafe model selector was archived raw or omitted without an unknown reason/digest")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            runner_main([
                "--harness", "codex", "--case", "first-finished-contribution-heldout",
                "--model", "synthetic-model", "--reasoning-effort", "medium",
            ])
        except SystemExit as exc:
            if exc.code != 2:
                failures.append("held-out case refusal used an unexpected exit code")
        else:
            failures.append("held-out case launched without explicit independent-check opt-in")

    missing = {"type": "item.completed", "item": {"id": "cmd-2", "type": "command_execution", "command": valid}}
    missing_action = normalize_codex((json.dumps(missing) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)["actions"][0]
    if explicit_exit_code(missing_action, "codex") is not None:
        failures.append("Codex item.completed without exit_code was treated as exit zero")

    codex_file = {
        "type": "item.completed",
        "item": {"id": "file-1", "type": "file_change", "status": "completed",
                 "changes": [{"path": "/outside/private.md", "kind": "update"}]},
    }
    file_action = normalize_codex((json.dumps(codex_file) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)["actions"][0]
    if file_action.get("path_status") != "outside_workspace" or file_action.get("path") is not None:
        failures.append("Codex file_change did not mark an outside path without retaining it")
    unverified_plan = {"type": "item.completed", "item": {"id": "plan-1", "type": "plan", "text": "local plan"}}
    plan_trace = normalize_codex((json.dumps(unverified_plan) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)
    if plan_trace["trace_complete"] or plan_trace["actions"]:
        failures.append("unverified Codex planning payload was fabricated as a completed tool action")

    todo_item = {
        "id": "item_plan",
        "type": "todo_list",
        "items": [{"text": "Read the contributor route", "completed": True}],
    }
    todo_lifecycle_events = [{"type": "thread.started", "thread_id": "thread-plan"}]
    todo_lifecycle_events.extend(
        {"type": event_type, "item": dict(todo_item)}
        for event_type in ("item.started", "item.updated", "item.completed")
    )
    todo_trace = normalize_codex(
        b"".join((json.dumps(event) + "\n").encode() for event in todo_lifecycle_events),
        Path("/tmp/v2-workspace"), REPO,
    )
    metadata = todo_trace["planning_metadata"]
    if (
        not todo_trace["trace_complete"]
        or todo_trace["unclassified_item_count"] != 0
        or todo_trace["actions"]
        or [entry.get("lifecycle_event") for entry in metadata]
        != ["item.started", "item.updated", "item.completed"]
        or any(entry.get("classification") != "planning_metadata" for entry in metadata)
    ):
        failures.append("valid Codex todo_list lifecycle was not retained only as planning metadata")

    malformed_todo = {
        "type": "item.completed",
        "item": {
            **todo_item,
            "items": [{"text": "Read the contributor route", "completed": "true"}],
        },
    }
    malformed_todo_trace = normalize_codex(
        (json.dumps(malformed_todo) + "\n").encode(), Path("/tmp/v2-workspace"), REPO,
    )
    if (
        malformed_todo_trace["trace_complete"]
        or malformed_todo_trace["unclassified_item_count"] != 1
        or malformed_todo_trace["planning_metadata"]
        or malformed_todo_trace["actions"]
    ):
        failures.append("malformed Codex todo_list fields were not left unclassified")

    unknown_todo_lifecycle = {"type": "item.failed", "item": dict(todo_item)}
    unknown_todo_trace = normalize_codex(
        (json.dumps(unknown_todo_lifecycle) + "\n").encode(), Path("/tmp/v2-workspace"), REPO,
    )
    if unknown_todo_trace["trace_complete"] or unknown_todo_trace["unclassified_item_count"] != 1:
        failures.append("unsupported Codex todo_list lifecycle event was treated as known")

    unknown_execution_item = {
        "type": "item.completed",
        "item": {"id": "mcp-1", "type": "mcp_tool_call", "name": "browser", "arguments": {}},
    }
    unknown_execution_trace = normalize_codex(
        (json.dumps(unknown_execution_item) + "\n").encode(), Path("/tmp/v2-workspace"), REPO,
    )
    if unknown_execution_trace["trace_complete"] or unknown_execution_trace["unclassified_item_count"] != 1:
        failures.append("execution-capable unknown Codex item was not left unclassified")

    opencode_denied = normalize_opencode(
        (json.dumps(_event("bash", "denied-call", command="python .github/scripts/validate_agent_docs.py truthfulness",
                           exit_code=0, status="denied")) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    if explicit_exit_code(opencode_denied["actions"][0], "opencode") is not None:
        failures.append("denied OpenCode tool with exit metadata was treated as executed validation")
    opencode_missing = normalize_opencode(
        (json.dumps(_event("bash", "missing-call", command="python .github/scripts/validate_agent_docs.py truthfulness")) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    if explicit_exit_code(opencode_missing["actions"][0], "opencode") is not None:
        failures.append("OpenCode completed tool without state.metadata.exit was treated as success")
    success_text_event = _event(
        "bash", "success-text-only",
        command="python .github/scripts/validate_agent_docs.py truthfulness",
    )
    success_text_event["part"]["state"]["output"] = "all validators passed"
    success_text_action = normalize_opencode(
        (json.dumps(success_text_event) + "\n").encode(), Path("/tmp/v2-workspace"), REPO,
    )["actions"][0]
    success_text_observations = validator_observations(
        [success_text_action], "opencode", Path("/tmp/v2-workspace"),
    )
    if explicit_exit_code(success_text_action, "opencode") is not None or (
        success_text_observations and success_text_observations[0]["exit_code"] is not None
    ):
        failures.append("validator success text without native exit metadata was treated as execution proof")
    errored_zero = normalize_opencode(
        (json.dumps(_event(
            "bash", "error-zero-validator",
            command="python .github/scripts/validate_agent_docs.py truthfulness",
            exit_code=0, status="error",
        )) + "\n").encode(), Path("/tmp/v2-workspace"), REPO,
    )["actions"][0]
    errored_zero_observations = validator_observations(
        [errored_zero], "opencode", Path("/tmp/v2-workspace"),
    )
    if errored_zero_observations and errored_zero_observations[0]["exit_code"] is not None:
        failures.append("zero exit with a native error lifecycle was treated as a completed validator pass")
    complete_audit = normalize_opencode(
        (json.dumps(_event("bash", "audit-complete", command="pwd", exit_code=0)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    complete_details = _direct_access_audit(
        complete_audit, complete_audit["actions"], "opencode", Path("/tmp/v2-workspace"),
    )
    if (
        not complete_details["complete"]
        or not _direct_access_audit_complete(
            complete_audit, complete_audit["actions"], "opencode", Path("/tmp/v2-workspace"),
        )
    ):
        failures.append("complete, hashed native command evidence did not yield a complete access audit")
    unsupported_ls = normalize_opencode(
        (json.dumps(_event("bash", "audit-unsupported-ls", command="ls -lZ", exit_code=0)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    unsupported_details = _direct_access_audit(
        unsupported_ls, unsupported_ls["actions"], "opencode", Path("/tmp/v2-workspace"),
    )
    if (
        unsupported_details["complete"]
        or len(unsupported_details["issues"]) != 1
        or unsupported_details["issues"][0].get("scope") != "action"
        or unsupported_details["issues"][0].get("action_index") != 0
        or "bounded ls grammar" not in unsupported_details["issues"][0].get("reason", "")
        or _direct_access_audit_complete(
            unsupported_ls, unsupported_ls["actions"], "opencode", Path("/tmp/v2-workspace"),
        )
    ):
        failures.append("unsupported shell grammar did not retain one action-local audit cause")
    long_command = "pwd " + "x" * 400
    truncated_audit = normalize_opencode(
        (json.dumps(_event("bash", "audit-truncated", command=long_command, exit_code=0)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    if _direct_access_audit_complete(truncated_audit, truncated_audit["actions"], "opencode", Path("/tmp/v2-workspace")):
        failures.append("truncated native command payload was reported as a complete no-access audit")
    truncated_validator = normalize_opencode(
        (json.dumps(_event(
            "bash", "truncated-validator",
            command="python .github/scripts/validate_agent_docs.py truthfulness " + "x" * 400,
            exit_code=0,
        )) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    if validator_observations(truncated_validator["actions"], "opencode", Path("/tmp/v2-workspace")):
        failures.append("truncated validator command yielded an inferred outcome")
    truncated_codex = normalize_codex((json.dumps({
        "type": "item.completed",
        "item": {
            "id": "truncated-codex-validator", "type": "command_execution",
            "command": "/bin/bash -lc 'python .github/scripts/validate_agent_docs.py truthfulness " + "x" * MAX_COMMAND_BYTES + "'",
            "status": "completed", "exit_code": 0,
        },
    }) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)
    if validator_observations(truncated_codex["actions"], "codex", Path("/tmp/v2-workspace")):
        failures.append("truncated Codex validator command yielded an inferred outcome")
    redacted_audit = normalize_opencode(
        (json.dumps(_event("bash", "audit-redacted", command="cat <REPO>/examples/fresh-agent-evals/v2/oracle.json", exit_code=0)) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )
    redacted_details = _direct_access_audit(
        redacted_audit, redacted_audit["actions"], "opencode", Path("/tmp/v2-workspace"),
    )
    if (
        redacted_details["complete"]
        or redacted_details["issues"][0].get("scope") != "action"
        or suite_accesses(redacted_audit["actions"], "opencode", Path("/tmp/v2-workspace"))
    ):
        failures.append("redacted direct-access payload was credited as complete or observed protected content")
    missing_trace = {"trace_complete": False}
    missing_details = _direct_access_audit(missing_trace, [], "opencode", Path("/tmp/v2-workspace"))
    tracewide_diagnostic = build_failure_diagnostics(
        ["direct evaluation-suite access audit is unknown; absence is not established"],
        [], "opencode", scope="docs", repo=REPO,
    )[0]
    if (
        missing_details["complete"]
        or not missing_details["issues"]
        or any(issue.get("scope") != "trace" for issue in missing_details["issues"])
        or tracewide_diagnostic.get("native_action") is not None
        or "Keep the access result unknown" not in tracewide_diagnostic.get("next_step", "")
        or "Stop accessing reserved" in tracewide_diagnostic.get("next_step", "")
    ):
        failures.append("missing trace-wide evidence was attributed to an action or given reserved-access remediation")
    incomplete_metrics = _setup_metrics(
        [{"tool": "bash", "command": "python -c 'open(\\\"README.md\\\")'", "command_truncated": False}],
        "opencode", Path("/tmp/v2-workspace"), False, None,
    )
    if incomplete_metrics["unnecessary_setup_attempts"]["count"] is not None or incomplete_metrics["clarification_observations"]["count"] is not None:
        failures.append("incomplete tool extraction claimed complete low setup/clarification totals")


def _schema_and_path_regressions(failures: list[str]) -> None:
    case_id = "first-finished-contribution-primary"
    good = _valid_completion(case_id, "docs/example.md", "python .github/scripts/validate_agent_docs.py truthfulness")
    if validate_completion(good, case_id):
        failures.append("well-typed completion schema fixture was rejected")
    wrong_type = json.loads(json.dumps(good))
    wrong_type["validation"]["final"]["exit_code"] = True
    if not any("integer" in item for item in validate_completion(wrong_type, case_id)):
        failures.append("boolean exit code passed strict schema typing")
    wrong_enum = json.loads(json.dumps(good))
    wrong_enum["route"]["selected_task"] = "first contribution"
    if not validate_completion(wrong_enum, case_id):
        failures.append("free-form prose passed a canonical task enum")
    same_input = {
        "source_tree_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "initial_file_sha256": {"README.md": "c" * 64},
    }
    pinned_codex = dict(same_input, expected_snapshot_sha256=same_input["snapshot_sha256"])
    try:
        assert_same_prepared_input(same_input, pinned_codex)
        assert_snapshot_pin(same_input, pinned_codex)
    except ComparisonError as exc:
        failures.append(f"identical prepared inputs were not comparable: {exc}")
    different_input = json.loads(json.dumps(same_input))
    different_input["snapshot_sha256"] = "d" * 64
    try:
        assert_same_prepared_input(same_input, different_input)
    except ComparisonError:
        pass
    else:
        failures.append("comparison accepted different prepared workspace snapshots")
    try:
        assert_snapshot_pin(same_input, dict(pinned_codex, expected_snapshot_sha256="e" * 64))
    except ComparisonError:
        pass
    else:
        failures.append("comparison accepted a Codex run without the prior OpenCode snapshot pin")
    malformed_paths = json.loads(json.dumps(good))
    malformed_paths["contribution"]["changed_paths"] = [7]
    if not validate_completion(malformed_paths, case_id):
        failures.append("non-string changed_paths value passed strict schema validation")
    malformed_route = json.loads(json.dumps(good))
    malformed_route["route"] = ["contribute"]
    if not validate_completion(malformed_route, case_id):
        failures.append("non-object route value passed strict schema validation")
    malformed_validation = json.loads(json.dumps(good))
    malformed_validation["validation"]["baseline"] = ["failed"]
    if not validate_completion(malformed_validation, case_id):
        failures.append("non-object nested validation value passed strict schema validation")

    with tempfile.TemporaryDirectory(prefix="of-v2-paths-") as temporary:
        root = Path(temporary)
        try:
            safe_relative("../escape")
            failures.append("parent traversal was accepted as a workspace-relative path")
        except PathSafetyError:
            pass
        parent = root / "runs"
        parent.mkdir()
        destination = parent / "exclusive"
        make_exclusive_directory(destination, parent)
        try:
            make_exclusive_directory(destination, parent)
            failures.append("existing run directory was replaced")
        except PathSafetyError:
            pass
        original = destination / "verdict.json"
        exclusive_json(original, {"verdict": "original"})
        before = original.read_bytes()
        try:
            exclusive_json(original, {"verdict": "regrade"})
            failures.append("original verdict evidence was overwritten")
        except PathSafetyError:
            pass
        if original.read_bytes() != before:
            failures.append("refused verdict overwrite changed original evidence bytes")
        outside = root / "outside"
        outside.mkdir()
        link = root / "linked"
        link.symlink_to(outside, target_is_directory=True)
        try:
            make_exclusive_directory(link / "child", link)
            failures.append("symlink escape was accepted for a trial directory")
        except PathSafetyError:
            pass


def _native_argument_regressions(failures: list[str]) -> None:
    workspace = Path("/tmp/v2-workspace")

    def normalize(events: list[dict], root: Path = workspace) -> dict:
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in events)
        return normalize_opencode(raw, root, REPO)

    read_trace = normalize([_structured_event(
        "read", "structured-read", {"filePath": "README.md", "offset": 0, "limit": 80},
    )])
    read_action = read_trace["actions"][0]
    read_arguments, read_problem = validate_native_arguments(read_action, workspace)
    if (
        read_problem is not None
        or read_arguments != {"tool": "read", "path": "README.md", "offset": 0, "limit": 80}
        or read_action.get("native_arguments_identity_sha256") != read_action.get("native_tool_call_id_sha256")
        or read_action.get("native_arguments_event_sequence") != read_action.get("event_sequence")
        or read_action.get("native_arguments_completion_sequence") != read_action.get("completion_sequence")
        or read_action.get("native_arguments_completion_status") != "completed"
        or not command_sha256_matches(read_action)
    ):
        failures.append("structured native read lost bounded path/range, identity, digest, or completion evidence")

    absolute_workspace_trace = normalize([_structured_event(
        "read", "absolute-workspace-read",
        {"filePath": f"{workspace.resolve()}/README.md"},
    )])
    absolute_arguments, absolute_problem = validate_native_arguments(
        absolute_workspace_trace["actions"][0], workspace,
    )
    if absolute_problem is not None or not isinstance(absolute_arguments, dict) or absolute_arguments.get("path") != "README.md":
        failures.append("safe absolute workspace path was not normalized without retaining the host path")

    discovery_events = [
        _structured_event("list", "directory-list", {"path": "examples/fresh-agent-evals/v2"}),
        _structured_event("glob", "filename-glob", {
            "path": "examples/fresh-agent-evals/v2", "pattern": "oracle.json",
        }),
    ]
    discovery = normalize(discovery_events)
    if (
        not discovery.get("trace_complete")
        or any(direct_suite_access(action, "opencode", workspace) for action in discovery["actions"])
        or any(content_read_observations([action], "opencode", workspace) for action in discovery["actions"])
        or not _direct_access_audit_complete(discovery, discovery["actions"], "opencode", workspace)
    ):
        failures.append("bounded list/glob filename discovery was confused with content access")

    protected_reads = normalize([
        _structured_event("read", "protected-read", {"filePath": "examples/fresh-agent-evals/v2/oracle.json"}),
        _structured_event("grep", "protected-search", {
            "path": "examples/fresh-agent-evals/v2/oracle.json", "pattern": "answer",
        }),
    ])
    if not all(direct_suite_access(action, "opencode", workspace) for action in protected_reads["actions"]):
        failures.append("native content read/search over a protected source was not detected")

    unreadable_cases = (
        ("read", {"offset": 0}, "missing_path"),
        ("read", {"path": "../README.md"}, "escaping"),
        ("read", {"path": "/etc/passwd"}, "outside_workspace"),
        ("read", {"path": "docs/$PUBLIC_PAGE"}, "dynamic_path"),
        ("read", {"path": "README.md", "offset": True}, "invalid_offset"),
        ("read", {"path": "README.md", "unexpected": "value"}, "unsupported_argument_keys"),
        ("glob", {"path": "docs", "pattern": "../**/*.md"}, "escaping"),
        ("glob", {"path": "docs"}, "missing_pattern"),
        ("grep", {"path": "README.md", "pattern": "sk-" + ("a" * 32)}, "redacted"),
    )
    for index, (tool, arguments, _expected_problem) in enumerate(unreadable_cases):
        trace = normalize([_structured_event(tool, f"malformed-native-argument-{index}", arguments)])
        action = trace["actions"][0]
        if (
            action.get("native_arguments_status") == "observed"
            or _direct_access_audit_complete(trace, trace["actions"], "opencode", workspace)
        ):
            failures.append(f"malformed, missing, redacted, or escaping native arguments were admitted: {tool}")
            break

    repeated = [
        _structured_event("read", "conflicting-args", {"path": "README.md"}, status="running"),
        _structured_event("read", "conflicting-args", {"path": "AGENTS.md"}, status="completed"),
    ]
    conflict_trace = normalize(repeated)
    if (
        conflict_trace["actions"][0].get("native_arguments_status") == "observed"
        or _direct_access_audit_complete(conflict_trace, conflict_trace["actions"], "opencode", workspace)
    ):
        failures.append("conflicting native input updates were treated as one structured file action")

    incomplete_trace = normalize([_structured_event(
        "read", "unfinished-args", {"path": "README.md"}, status="running",
    )])
    incomplete_args, incomplete_problem = validate_native_arguments(incomplete_trace["actions"][0], workspace)
    if incomplete_args is not None or incomplete_problem != "missing_explicit_completion":
        failures.append("running native read without explicit completion was accepted as completed evidence")

    private_path = "/home/" + "private-" + "user/private-input.txt"
    private_shell_trace = normalize([
        _event("bash", "private-path-shell", command="cat " + private_path, exit_code=0),
    ])
    private_shell_action = private_shell_trace["actions"][0]
    if (
        private_shell_action.get("command") is not None
        or private_shell_action.get("command_preview") is not None
        or private_shell_action.get("command_redaction_status") != "private_path"
        or private_path in json.dumps(private_shell_action)
    ):
        failures.append("absolute private path leaked through sanitized native shell evidence")

    private_codex_command = f"/bin/bash -lc {shlex.quote('cat ' + private_path)}"
    private_codex_event = {
        "type": "item.completed",
        "item": {
            "id": "private-path-codex", "type": "command_execution",
            "command": private_codex_command, "status": "completed", "exit_code": 0,
        },
    }
    private_codex = normalize_codex(
        (json.dumps(private_codex_event) + "\n").encode(), workspace, REPO,
    )["actions"][0]
    if (
        private_codex.get("command") is not None
        or private_codex.get("command_preview") is not None
        or private_codex.get("command_redaction_status") != "private_path"
        or private_path in json.dumps(private_codex)
    ):
        failures.append("absolute private path leaked through sanitized nested-shell evidence")

    with tempfile.TemporaryDirectory(prefix="of-v2-native-args-") as temporary:
        root = Path(temporary)
        real = root / "real.md"
        real.write_text("safe content", encoding="utf-8")
        (root / "link.md").symlink_to(real)
        linked = normalize([_structured_event("read", "symlink-read", {"path": "link.md"})], root)
        _arguments, symlink_problem = validate_native_arguments(linked["actions"][0], root)
        if symlink_problem != "symlink_path" or _direct_access_audit_complete(
            linked, linked["actions"], "opencode", root,
        ):
            failures.append("symlink native path did not remain refused/unknown")


def _native_read_envelope_regressions(failures: list[str]) -> None:
    def read_event(
        root: Path,
        call_id: str,
        relative: str,
        offset: int,
        limit: int,
        output: str,
    ) -> dict:
        return _structured_event(
            "read", call_id,
            {"filePath": str((root / relative).resolve()), "offset": offset, "limit": limit},
            output=output,
        )

    def trace_for(root: Path, events: list[dict]) -> dict:
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in events)
        return normalize_opencode(raw, root, REPO)

    with tempfile.TemporaryDirectory(prefix="of-v2-native-read-envelopes-") as temporary:
        root = Path(temporary)
        source = root / "README.md"
        short_lines = ["first source line", "second source line", "third source line"]
        source.write_text("\n".join(short_lines) + "\n", encoding="utf-8")
        native_path = str(source.resolve())
        full_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n"
            "1: first source line\n2: second source line\n3: third source line\n\n"
            "(End of file - total 3 lines)\n</content>"
        )
        full_trace = trace_for(root, [read_event(root, "native-full-file", "README.md", 1, 2000, full_output)])
        full_action = full_trace["actions"][0]
        full_credit = content_read_observations(full_trace["actions"], "opencode", root)
        full_route, _selector, full_route_complete = observed_route_sources(
            full_trace["actions"], "opencode", root,
        )
        if (
            full_action.get("native_output_file_envelope_status") != "observed"
            or full_action.get("native_output_file_footer") != "eof"
            or len(full_credit) != 1
            or full_credit[0].get("path") != "README.md"
            or full_route != {"README.md"}
            or not full_route_complete
        ):
            failures.append("exact native path/type/content/EOF envelope did not establish a full file read")

        paginated_lines = [f"line {number:04d} " + "x" * 42 for number in range(1, 1201)]
        source.write_text("\n".join(paginated_lines) + "\n", encoding="utf-8")
        page_one_body = "\n".join(f"{number}: {paginated_lines[number - 1]}" for number in range(1, 1060))
        page_two_body = "\n".join(f"{number}: {paginated_lines[number - 1]}" for number in range(1060, 1201))
        page_one_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n{page_one_body}\n\n"
            "(Output capped at 50 KB. Showing lines 1-1059. Use offset=1060 to continue.)\n</content>"
        )
        page_two_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n{page_two_body}\n\n"
            "(End of file - total 1200 lines)\n</content>"
        )
        if len(page_one_output.encode("utf-8")) < 50_000:
            failures.append("native output-capped regression did not reach the observed 50 KB envelope boundary")
        page_one = read_event(root, "native-read-page-one", "README.md", 1, 2000, page_one_output)
        page_two = read_event(root, "native-read-page-two", "README.md", 1060, 2000, page_two_output)
        paginated_trace = trace_for(root, [page_one, page_two])
        paginated_actions = paginated_trace["actions"]
        paginated_credit = content_read_observations(paginated_actions, "opencode", root)
        paginated_route, _selector, paginated_route_complete = observed_route_sources(
            paginated_actions, "opencode", root,
        )
        page_observations = normalize_access_observations(paginated_actions, "opencode", root)
        if (
            len(paginated_credit) != 1
            or paginated_credit[0].get("path") != "README.md"
            or [page.get("first_line") for page in paginated_credit[0].get("read_pages", [])] != [1, 1060]
            or [page.get("footer") for page in paginated_credit[0].get("read_pages", [])] != ["capped", "eof"]
            or [page.get("identity_sha256") for page in paginated_credit[0].get("read_pages", [])]
            != [action.get("native_tool_call_id_sha256") for action in paginated_actions]
            or paginated_credit[0].get("action", {}).get("identity_sha256")
            != paginated_actions[-1].get("native_tool_call_id_sha256")
            or paginated_route != {"README.md"}
            or not paginated_route_complete
            or [item.get("scope", {}).get("content_range") for item in page_observations] != ["partial", "full"]
        ):
            failures.append("contiguous capped README page plus EOF continuation did not credit only the final causal page")
        first_page_only = content_read_observations([paginated_actions[0]], "opencode", root)
        first_page_route, _selector, first_page_complete = observed_route_sources(
            [paginated_actions[0]], "opencode", root,
        )
        if first_page_only or first_page_route or first_page_complete:
            failures.append("a capped first page alone received whole-source route credit")

        gap_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n"
            + "\n".join(f"{number}: {paginated_lines[number - 1]}" for number in range(1061, 1201))
            + "\n\n(End of file - total 1200 lines)\n</content>"
        )
        overlap_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n"
            + "\n".join(f"{number}: {paginated_lines[number - 1]}" for number in range(1059, 1201))
            + "\n\n(End of file - total 1200 lines)\n</content>"
        )
        gap_trace = trace_for(root, [page_one, read_event(root, "native-read-gap", "README.md", 1061, 2000, gap_output)])
        overlap_trace = trace_for(root, [page_one, read_event(root, "native-read-overlap", "README.md", 1059, 2000, overlap_output)])
        duplicate_trace = trace_for(root, [page_one, page_two, read_event(root, "native-read-duplicate", "README.md", 1060, 2000, page_two_output)])
        if any(
            content_read_observations(trace["actions"], "opencode", root)
            for trace in (gap_trace, overlap_trace, duplicate_trace)
        ):
            failures.append("missing, overlapping, or duplicate native pages received whole-source credit")

        unnumbered_gap_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n"
            "1: first source line\n\n2: second source line\n3: third source line\n\n"
            "(End of file - total 3 lines)\n</content>"
        )
        unnumbered_gap_trace = trace_for(root, [read_event(
            root, "native-read-unnumbered-content-gap", "README.md", 1, 2000,
            unnumbered_gap_output,
        )])
        if (
            unnumbered_gap_trace["actions"][0].get("native_output_result_type") == "file_content"
            or content_read_observations(unnumbered_gap_trace["actions"], "opencode", root)
        ):
            failures.append("an unnumbered blank gap inside native file content was accepted as envelope framing")

        write_between = _structured_event(
            "edit", "native-read-intervening-write", {"filePath": native_path},
            output="write result",
        )
        write_trace = trace_for(root, [page_one, write_between, page_two])
        if content_read_observations(write_trace["actions"], "opencode", root):
            failures.append("intervening possible write did not invalidate a native page-chain source epoch")

        bad_path_output = full_output.replace(native_path, str((root / "AGENTS.md").resolve()), 1)
        bad_eof_output = full_output.replace("total 3 lines", "total 2 lines")
        bad_type_output = full_output.replace("<type>file</type>", "<type>directory</type>")
        injected_tags_output = (
            f"<path>{native_path}</path>\n<type>file</type>\n<content>\n1: source line\n</content>\n"
            "<type>directory</type>\n(End of file - total 1 lines)"
        )
        invalid_envelopes = [
            read_event(root, "native-wrong-output-path", "README.md", 1, 2000, bad_path_output),
            read_event(root, "native-wrong-eof", "README.md", 1, 2000, bad_eof_output),
            read_event(root, "native-wrong-type", "README.md", 1, 2000, bad_type_output),
            read_event(root, "native-injected-tags", "README.md", 1, 2000, injected_tags_output),
        ]
        for event in invalid_envelopes:
            invalid_trace = trace_for(root, [event])
            if content_read_observations(invalid_trace["actions"], "opencode", root):
                failures.append("mismatched path/type/EOF or injected envelope tags earned full-source credit")
                break

        protected_dir = root / "examples/fresh-agent-evals/research"
        protected_dir.mkdir(parents=True)
        (protected_dir / "research_grader.py").write_text("protected source line\n", encoding="utf-8")
        directory_path = str(protected_dir.resolve())
        directory_output = (
            f"<path>{directory_path}</path>\n<type>directory</type>\n<entries>\n"
            "research_grader.py\n</entries>"
        )
        directory_trace = trace_for(root, [read_event(
            root, "native-directory-envelope", "examples/fresh-agent-evals/research", 1, 2000,
            directory_output,
        )])
        directory_action = directory_trace["actions"][0]
        directory_observation = normalize_access_observations(
            [directory_action], "opencode", root,
        )[0]
        if (
            directory_action.get("native_output_result_type") != "directory_listing"
            or directory_observation.get("operation") != "directory_listing"
            or directory_observation.get("returned_evidence") != "names_observed"
            or content_read_observations([directory_action], "opencode", root)
            or suite_accesses([directory_action], "opencode", root)
        ):
            failures.append("native directory envelope exposed more than names-only discovery")

        empty_directory = root / "empty-directory"
        empty_directory.mkdir()
        empty_directory_path = str(empty_directory.resolve())
        empty_directory_output = (
            f"<path>{empty_directory_path}</path>\n<type>directory</type>\n<entries>\n\n\n"
            "(0 entries)\n</entries>"
        )
        empty_directory_frame = _native_directory_frame(
            empty_directory_output, "empty-directory", root,
        )
        empty_directory_trace = trace_for(root, [read_event(
            root, "native-empty-directory-envelope", "empty-directory", 1, 2000,
            empty_directory_output,
        )])
        empty_directory_action = empty_directory_trace["actions"][0]
        empty_directory_observation = normalize_access_observations(
            [empty_directory_action], "opencode", root,
        )[0]
        empty_directory_routes, _selector, empty_directory_route_complete = observed_route_sources(
            [empty_directory_action], "opencode", root,
        )
        if (
            empty_directory_frame is None
            or empty_directory_frame.get("entries") != []
            or empty_directory_action.get("native_output_result_type") != "directory_listing"
            or empty_directory_action.get("native_output_content_status") == "observed"
            or empty_directory_observation.get("operation") != "directory_listing"
            or empty_directory_observation.get("returned_evidence") not in {"names_observed", "empty_observed"}
            or content_read_observations([empty_directory_action], "opencode", root)
            or suite_accesses([empty_directory_action], "opencode", root)
            or empty_directory_routes
            or empty_directory_route_complete
        ):
            failures.append("exact empty native directory marker did not remain explicitly empty, names-only discovery")

        protected_content = "<path>{}</path>\n<type>file</type>\n<content>\n1: protected source line\n(End of file - total 1 lines)\n</content>".format(
            str((protected_dir / "research_grader.py").resolve()),
        )
        protected_trace = trace_for(root, [read_event(
            root, "native-protected-content", "examples/fresh-agent-evals/research/research_grader.py",
            1, 2000, protected_content,
        )])
        if suite_accesses(protected_trace["actions"], "opencode", root) != [0]:
            failures.append("actual protected content returned in a native file envelope was not rejected by the active suite gate")


def _access_observation_regressions(failures: list[str]) -> None:
    workspace = Path("/tmp/v2-access-observation-workspace")
    exclusion_scripts = (
        "rg --files -g AGENTS.md -g '!examples/fresh-agent-evals/**' -g '!trial-output/**'",
        "rg --files -g '!examples/fresh-agent-evals/**'",
    )
    for harness in ("opencode", "codex"):
        for index, script in enumerate(exclusion_scripts):
            trace, _command = _shell_trace(script, harness, f"rg-files-exclusion-{harness}-{index}", workspace)
            action = trace["actions"][0]
            observations = normalize_access_observations([action], harness, workspace)
            if (
                direct_suite_access(action, harness, workspace)
                or not _direct_access_audit_complete(trace, trace["actions"], harness, workspace)
                or len(observations) != 1
                or observations[0].get("operation") != "filename_discovery"
                or observations[0].get("returned_evidence") == "content_observed"
                or "examples/fresh-agent-evals/**" not in observations[0].get("scope", {}).get("excluded_globs", [])
            ):
                failures.append(f"exclusion-only rg --files was treated as protected content access: {harness}")
                break

    positive_trace, _ = _shell_trace(
        "rg --files -g examples/fresh-agent-evals/v2/oracle.json",
        "opencode", "positive-protected-filename-discovery", workspace,
    )
    positive_discovery = normalize_access_observations(
        positive_trace["actions"], "opencode", workspace,
    )
    if (
        len(positive_discovery) != 1
        or positive_discovery[0].get("operation") != "filename_discovery"
        or "examples/fresh-agent-evals/v2/oracle.json"
        not in positive_discovery[0].get("scope", {}).get("positive_globs", [])
        or positive_discovery[0].get("returned_evidence") == "content_observed"
        or content_read_observations(positive_trace["actions"], "opencode", workspace)
        or suite_accesses(positive_trace["actions"], "opencode", workspace)
    ):
        failures.append("positive protected rg --files scope was not preserved as auditable filename discovery")

    with tempfile.TemporaryDirectory(prefix="of-v2-access-equivalence-") as temporary:
        root = Path(temporary)
        source = root / "README.md"
        source.write_text("first source line\nsecond source line\n", encoding="utf-8")
        native_trace = normalize_opencode(
            (json.dumps(_structured_event(
                "read", "equivalent-native-read",
                {"filePath": "README.md", "offset": 1, "limit": 3},
                output=(
                    "<path>README.md</path>\n<type>file</type>\n<content>\n"
                    "1: first source line\n2: second source line\n"
                    "(End of file - total 2 lines)\n</content>"
                ),
            ), separators=(",", ":")) + "\n").encode(),
            root, REPO,
        )
        shell_trace, _ = _shell_trace("cat README.md", "opencode", "equivalent-shell-read", root)
        native_observations = normalize_access_observations(native_trace["actions"], "opencode", root)
        shell_observations = normalize_access_observations(shell_trace["actions"], "opencode", root)
        if (
            len(native_observations) != 1
            or len(shell_observations) != 1
            or native_observations[0].get("operation") != "file_content_read"
            or not access_observations_equivalent(native_observations[0], shell_observations[0])
        ):
            failures.append("matching native read and successful shell cat did not normalize to equivalent observed effects")

        failed_trace, _ = _shell_trace("cat README.md", "opencode", "failed-shell-read", root)
        failed_trace["actions"][0]["outcome"]["shell_exit_code"] = 1
        failed_trace["actions"][0]["outcome"]["native_tool_status"] = "failed"
        failed_observation = normalize_access_observations(failed_trace["actions"], "opencode", root)[0]
        if (
            failed_observation.get("attempt_status") != "observed"
            or failed_observation.get("outcome_status") != "failed"
            or failed_observation.get("returned_evidence") != "unknown"
            or access_observations_equivalent(native_observations[0], failed_observation)
        ):
            failures.append("failed shell read was credited as returned content or equated with a completed native read")

        directory = root / "trial-output"
        directory.mkdir()
        (directory / "research-result.json").write_text("{}\n", encoding="utf-8")
        directory_trace = normalize_opencode(
            (json.dumps(_structured_event(
                "read", "directory-read", {"filePath": "trial-output"},
                output={
                    "type": "directory", "path": "trial-output",
                    "entries": ["research-result.json"],
                },
            ), separators=(",", ":")) + "\n").encode(), root, REPO,
        )
        directory_action = directory_trace["actions"][0]
        directory_observation = normalize_access_observations(
            [directory_action], "opencode", root,
        )[0]
        if (
            directory_action.get("native_output_result_type") != "directory_listing"
            or directory_observation.get("operation") != "directory_listing"
            or directory_observation.get("returned_evidence") != "names_observed"
            or content_read_observations([directory_action], "opencode", root)
        ):
            failures.append("native read of trial-output directory earned file-content credit or lost evidenced listing type")

        prose_trace = normalize_opencode(
            (json.dumps(_structured_event(
                "read", "directory-prose", {"filePath": "trial-output"},
                output={
                    "type": "directory", "path": "trial-output",
                    "entries": ["Permission denied: oracle.json"],
                },
            ), separators=(",", ":")) + "\n").encode(), root, REPO,
        )
        prose_observation = normalize_access_observations(
            prose_trace["actions"], "opencode", root,
        )[0]
        if (
            prose_observation.get("operation") != "directory_listing"
            or prose_observation.get("returned_evidence") != "unknown"
            or prose_trace["actions"][0]["outcome"].get("native_tool_status") != "completed"
        ):
            failures.append("untrusted directory-read prose was used to infer names or permission denial")

        missing_type = normalize_opencode(
            (json.dumps({
                "type": "tool_use",
                "part": {"type": "tool", "tool": "read", "callID": "directory-no-output-type",
                         "state": {"status": "completed", "input": {"filePath": "trial-output"}}},
            }, separators=(",", ":")) + "\n").encode(), root, REPO,
        )
        if normalize_access_observations(missing_type["actions"], "opencode", root)[0].get("operation") != "unknown":
            failures.append("native directory read without evidenced result framing was not left unknown")

        protected_directory = root / "examples/fresh-agent-evals/research"
        protected_directory.mkdir(parents=True)
        (protected_directory / "research_grader.py").write_text("protected filename\n", encoding="utf-8")
        listing_path = "examples/fresh-agent-evals/research"
        native_listing_trace = normalize_opencode(
            (json.dumps(_structured_event(
                "list", "protected-native-directory-listing",
                {"path": listing_path},
                output={
                    "type": "directory",
                    "path": listing_path,
                    "entries": ["research_grader.py"],
                },
            ), separators=(",", ":")) + "\n").encode(), root, REPO,
        )
        native_listing = normalize_access_observations(
            native_listing_trace["actions"], "opencode", root,
        )[0]
        long_listing = (
            "total 0\n"
            "drwxr-xr-x 2 owner group 4096 Sep 25 00:00 .\n"
            "drwxr-xr-x 2 owner group 4096 Sep 25 00:00 ..\n"
            "-rw-r--r-- 1 owner group 1 Sep 25 00:00 research_grader.py\n"
        )
        for harness in ("opencode", "codex"):
            shell_listing_trace, _listing_command = _shell_trace(
                f"ls -la {listing_path}", harness,
                f"protected-shell-directory-listing-{harness}", root,
                output=long_listing,
            )
            shell_listing = normalize_access_observations(
                shell_listing_trace["actions"], harness, root,
            )[0]
            different_scope_trace, _different_command = _shell_trace(
                "ls -la docs/agents", harness,
                f"different-scope-directory-listing-{harness}", root,
                output=long_listing,
            )
            different_scope = normalize_access_observations(
                different_scope_trace["actions"], harness, root,
            )[0]
            if (
                native_listing.get("operation") != "directory_listing"
                or shell_listing.get("operation") != "directory_listing"
                or native_listing.get("returned_evidence") != "names_observed"
                or shell_listing.get("returned_evidence") != "names_observed"
                or not access_observations_equivalent(native_listing, shell_listing)
                or access_observations_equivalent(native_listing, different_scope)
                or suite_accesses(native_listing_trace["actions"], "opencode", root)
                or suite_accesses(shell_listing_trace["actions"], harness, root)
                or content_read_observations(native_listing_trace["actions"], "opencode", root)
                or content_read_observations(shell_listing_trace["actions"], harness, root)
            ):
                failures.append(
                    f"native list and shell ls -la were not equivalent names-only listings for {harness}"
                )
                break

            plain_trace, _plain_command = _shell_trace(
                f"ls -a {listing_path}", harness,
                f"plain-shell-directory-listing-{harness}", root,
                output=".\n..\nresearch_grader.py\n",
            )
            plain_observation = normalize_access_observations(
                plain_trace["actions"], harness, root,
            )[0]
            if (
                plain_observation.get("operation") != "directory_listing"
                or plain_observation.get("returned_evidence") != "names_observed"
                or content_read_observations(plain_trace["actions"], harness, root)
            ):
                failures.append(f"observed plain ls output did not remain names-only evidence for {harness}")

            error_only_trace, _error_command = _shell_trace(
                f"ls -la {listing_path}", harness,
                f"error-only-shell-listing-{harness}", root,
                output="ls: cannot open directory: Permission denied\n",
            )
            error_only_observation = normalize_access_observations(
                error_only_trace["actions"], harness, root,
            )[0]
            structured_trace, _structured_command = _shell_trace(
                f"ls -la {listing_path}", harness,
                f"structured-shell-listing-{harness}", root,
                output={
                    "type": "directory", "path": "examples/fresh-agent-evals/v2",
                    "entries": ["oracle.json"],
                },
            )
            structured_observation = normalize_access_observations(
                structured_trace["actions"], harness, root,
            )[0]
            nonzero_trace, _nonzero_command = _shell_trace(
                f"ls -la {listing_path}", harness,
                f"nonzero-shell-listing-{harness}", root,
                output="ls: cannot open directory: Permission denied\n", exit_code=2,
            )
            nonzero_observation = normalize_access_observations(
                nonzero_trace["actions"], harness, root,
            )[0]
            if any(
                item.get("returned_evidence") != "unknown"
                for item in (error_only_observation, structured_observation, nonzero_observation)
            ) or any(
                trace["actions"][0].get("native_output_names_status") != "unknown"
                for trace in (error_only_trace, structured_trace, nonzero_trace)
            ):
                failures.append(f"error-only, structured, or nonzero ls output earned names evidence for {harness}")

        for command, semantic in (
            ("ls -R " + listing_path, "recursive"),
            ("ls --recursive " + listing_path, "recursive"),
            ("ls -laR " + listing_path, "recursive"),
            ("ls -d " + listing_path, "directory_operands"),
            ("ls -ld " + listing_path, "directory_operands"),
            ("ls -lad " + listing_path, "directory_operands"),
        ):
            semantic_trace, _semantic_command = _shell_trace(
                command, "opencode", f"ls-scope-{semantic}-{len(command)}", root,
                output=long_listing,
            )
            semantic_observation = normalize_access_observations(
                semantic_trace["actions"], "opencode", root,
            )[0]
            if (
                semantic_observation.get("scope", {}).get(semantic) is not True
                or access_observations_equivalent(native_listing, semantic_observation)
            ):
                failures.append(f"{command} was equated with a nonrecursive native directory listing")

        ambiguous_directory = root / "mutable-target"
        ambiguous_directory.mkdir()
        (ambiguous_directory / "entry.txt").write_text("entry\n", encoding="utf-8")
        ambiguous_event = _structured_event(
            "read", "mutable-target-type", {"filePath": "mutable-target", "offset": 1, "limit": 2},
            output="entry.txt",
        )
        ambiguous_raw = (json.dumps(ambiguous_event, separators=(",", ":")) + "\n").encode()
        directory_target_trace = normalize_opencode(ambiguous_raw, root, REPO)
        directory_target_action = directory_target_trace["actions"][0]
        directory_target_observation = normalize_access_observations(
            [directory_target_action], "opencode", root,
        )[0]
        shutil.rmtree(ambiguous_directory)
        ambiguous_directory.write_text("entry.txt\n", encoding="utf-8")
        file_target_trace = normalize_opencode(ambiguous_raw, root, REPO)
        file_target_action = file_target_trace["actions"][0]
        file_target_observation = normalize_access_observations(
            [file_target_action], "opencode", root,
        )[0]
        if (
            directory_target_action.get("native_tool_call_id_sha256")
            != file_target_action.get("native_tool_call_id_sha256")
            or directory_target_action.get("native_output_result_type") != "unknown"
            or file_target_action.get("native_output_result_type") != "unknown"
            or directory_target_observation != file_target_observation
            or content_read_observations([directory_target_action], "opencode", root)
            or content_read_observations([file_target_action], "opencode", root)
        ):
            failures.append("identical ambiguous native output changed meaning when only the final target changed type")

    with tempfile.TemporaryDirectory(prefix="of-v2-event-bound-file-read-") as temporary:
        root = Path(temporary)
        source = root / "README.md"
        source.write_text("first source line\nsecond source line\n", encoding="utf-8")
        framed_event = _structured_event(
            "read", "event-bound-framed-file",
            {"filePath": "README.md", "offset": 1, "limit": 3},
            output=(
                "<path>README.md</path>\n<type>file</type>\n<content>\n"
                "1: first source line\n2: second source line\n"
                "(End of file - total 2 lines)\n</content>"
            ),
        )
        raw = (json.dumps(framed_event, separators=(",", ":")) + "\n").encode()
        file_trace = normalize_opencode(raw, root, REPO)
        file_action = file_trace["actions"][0]
        file_observation = normalize_access_observations([file_action], "opencode", root)[0]
        file_credit = content_read_observations([file_action], "opencode", root)
        source.unlink()
        source.mkdir()
        (source / "entry.txt").write_text("later directory entry\n", encoding="utf-8")
        changed_trace = normalize_opencode(raw, root, REPO)
        changed_action = changed_trace["actions"][0]
        changed_observation = normalize_access_observations([changed_action], "opencode", root)[0]
        changed_credit = content_read_observations([changed_action], "opencode", root)
        if (
            file_action.get("native_output_result_type") != "file_content"
            or changed_action.get("native_output_result_type") != "file_content"
            or file_observation != changed_observation
            or file_credit != changed_credit
            or len(file_credit) != 1
            or file_credit[0].get("kind") != "native_content_read"
        ):
            failures.append("successful framed native file output lost or changed its read credit after the target changed type")

    protected = normalize_opencode(
        (json.dumps(_structured_event(
            "read", "denied-protected-read",
            {"filePath": "examples/fresh-agent-evals/v2/oracle.json"},
            status="denied", output="Permission denied",
        ), separators=(",", ":")) + "\n").encode(), workspace, REPO,
    )
    protected_action = protected["actions"][0]
    protected_observation = normalize_access_observations([protected_action], "opencode", workspace)[0]
    if (
        not direct_suite_access(protected_action, "opencode", workspace)
        or protected_observation.get("attempt_status") != "observed"
        or protected_observation.get("outcome_status") != "denied"
        or protected_observation.get("returned_evidence") != "unknown"
        or content_read_observations([protected_action], "opencode", workspace)
        or suite_accesses([protected_action], "opencode", workspace) != [0]
    ):
        failures.append("denied protected native read was not audited distinctly from returned content")


def _entry_route_regressions(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="of-v2-entry-route-") as temporary:
        workspace = Path(temporary)
        for relative in (
            "README.md", "AGENTS.md", "CLAUDE.md", "docs/agents/start-here.md",
            "docs/agents/first-contribution.md", "docs/agents/research-security.md",
        ):
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"observed source: {relative}\nsecond source line: {relative}\n",
                encoding="utf-8",
            )

        def trace_for(events: list[dict]) -> dict:
            raw = b"".join(
                (json.dumps(event, separators=(",", ":")) + "\n").encode()
                for event in events
            )
            return normalize_opencode(raw, workspace, REPO)

        def native_read(
            call_id: str,
            relative: str,
            *,
            offset: int | None = None,
            limit: int | None = None,
            output: str | None = None,
            status: str = "completed",
            identity: bool = True,
        ) -> dict:
            arguments = {"filePath": relative}
            if offset is not None:
                arguments["offset"] = offset
            if limit is not None:
                arguments["limit"] = limit
            if output is None:
                lines = (workspace / relative).read_text(encoding="utf-8").splitlines()
                first_line = 1 if offset in {None, 0} else offset
                start_index = first_line - 1
                selected = lines[start_index:start_index + limit] if limit is not None else lines[start_index:]
                body = "\n".join(
                    f"{start_index + index + 1}: {line}"
                    for index, line in enumerate(selected)
                )
                last_line = start_index + len(selected)
                if last_line == len(lines):
                    footer = f"(End of file - total {len(lines)} lines)"
                else:
                    footer = (
                        f"(Output capped at 50 KB. Showing lines {first_line}-{last_line}. "
                        f"Use offset={last_line + 1} to continue.)"
                    )
                output = (
                    f"<path>{relative}</path>\n<type>file</type>\n<content>\n"
                    f"{body}\n{footer}\n</content>"
                )
            event = _structured_event(
                "read", call_id, arguments, status=status, output=output,
            )
            if not identity:
                event["part"].pop("callID", None)
            return event

        def line_count(relative: str) -> int:
            return len((workspace / relative).read_bytes().splitlines())

        read_and_select = [
            _event("read", "entry-read-readme", path="README.md"),
            _event("read", "entry-read-agents", path="AGENTS.md"),
            _event("bash", "entry-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            _event("read", "entry-read-task-route", path="docs/agents/first-contribution.md"),
        ]
        trace = trace_for(read_and_select)
        valid = entry_route_evidence(
            trace["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        if valid.get("status") != "pass" or valid.get("entry_route_observation", {}).get("kind") != "start_here_cli_selector":
            failures.append("valid root-first CLI selector route did not satisfy measured entry criteria")

        explicit_offset_route = trace_for([
            native_read("offset-one-readme", "README.md", offset=1, limit=line_count("README.md") + 1),
            native_read("offset-one-agents", "AGENTS.md", offset=1, limit=line_count("AGENTS.md") + 1),
            _event("bash", "offset-one-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            native_read(
                "offset-one-task-route", "docs/agents/first-contribution.md",
                offset=1, limit=line_count("docs/agents/first-contribution.md") + 1,
            ),
        ])
        explicit_offset_evidence = entry_route_evidence(
            explicit_offset_route["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        if explicit_offset_evidence.get("status") != "pass":
            failures.append("one-based offset=1 full-source reads did not satisfy the ordinary native route")

        offset_zero = trace_for([native_read(
            "offset-zero-boundary", "README.md", offset=0, limit=line_count("README.md") + 1,
        )])
        omitted_offset = trace_for([native_read("omitted-offset-default", "README.md")])
        if (
            [item["path"] for item in content_read_observations(offset_zero["actions"], "opencode", workspace)]
            != ["README.md"]
            or [item["path"] for item in content_read_observations(omitted_offset["actions"], "opencode", workspace)]
            != ["README.md"]
        ):
            failures.append("observed offset=0 boundary or omitted one-based default lost first-line read credit")

        combined_route_command = (
            "cat README.md AGENTS.md && "
            "python scripts/start_here.py --task contribute && "
            "cat docs/agents/first-contribution.md"
        )
        combined_route = trace_for([_event(
            "bash", "combined-positive-route", command=combined_route_command, exit_code=0,
        )])
        combined_evidence = entry_route_evidence(
            combined_route["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        combined_observations = content_read_observations(
            combined_route["actions"], "opencode", workspace,
        )
        if (
            combined_evidence.get("status") != "pass"
            or [item["path"] for item in combined_observations]
            != ["README.md", "AGENTS.md", "docs/agents/first-contribution.md"]
            or [(item.get("segment_index"), item.get("operand_index")) for item in combined_observations]
            != [(0, 0), (0, 1), (2, 0)]
            or len({item["action"]["identity_sha256"] for item in combined_observations}) != 1
        ):
            failures.append("ordered root reads, selector, and task read in one successful native command did not pass with local positions")

        reversed_route_command = (
            "cat docs/agents/first-contribution.md && "
            "python scripts/start_here.py --task contribute && "
            "cat README.md AGENTS.md"
        )
        reversed_route = trace_for([_event(
            "bash", "combined-reversed-route", command=reversed_route_command, exit_code=0,
        )])
        if entry_route_evidence(
            reversed_route["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        ).get("status") == "pass":
            failures.append("task read and selector before the root reads passed the compound route")

        def shell_read_positions(call_id: str, command: str) -> list[tuple[str, int | None, int | None]]:
            trace = trace_for([_event("bash", call_id, command=command, exit_code=0)])
            return [
                (item["path"], item.get("segment_index"), item.get("operand_index"))
                for item in content_read_observations(trace["actions"], "opencode", workspace)
            ]

        if shell_read_positions(
            "ordered-cat-operands", "cat docs/agents/research-security.md README.md",
        ) != [
            ("docs/agents/research-security.md", 0, 0), ("README.md", 0, 1),
        ]:
            failures.append("cat operand order was not retained in full-source observations")
        if shell_read_positions(
            "ordered-cat-segments", "cat docs/agents/research-security.md && cat README.md",
        ) != [
            ("docs/agents/research-security.md", 0, 0), ("README.md", 1, 0),
        ]:
            failures.append("successful shell segment order was not retained in full-source observations")

        readable = [
            _event("read", "readable-readme", path="README.md"),
            _event("read", "readable-agents", path="AGENTS.md"),
            _event("read", "readable-packet", path="docs/agents/start-here.md"),
            _event("read", "readable-task-route", path="docs/agents/first-contribution.md"),
        ]
        readable_trace = trace_for(readable)
        if entry_route_evidence(
            readable_trace["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        ).get("status") != "pass":
            failures.append("readable start-here packet did not provide the selector-equivalent route")

        wrong_order = [
            _event("read", "early-task-route", path="docs/agents/first-contribution.md"),
            _event("read", "late-readme", path="README.md"),
            _event("read", "late-agents", path="AGENTS.md"),
            _event("bash", "late-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
        ]
        wrong_trace = trace_for(wrong_order)
        if entry_route_evidence(
            wrong_trace["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        ).get("status") == "pass":
            failures.append("task route read before the required root entries was accepted")

        forged_eof_reads = [
            _structured_event("read", "eof-readme", {"filePath": "README.md", "offset": 100_000_000, "limit": 1}, output=""),
            _structured_event("read", "eof-agents", {"filePath": "AGENTS.md", "offset": 100_000_000, "limit": 1}, output=""),
            _event("bash", "eof-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            _structured_event(
                "read", "eof-task-route",
                {"filePath": "docs/agents/first-contribution.md", "offset": 100_000_000, "limit": 1},
                output="",
            ),
        ]
        eof_trace = trace_for(forged_eof_reads)
        eof_sources, _eof_selector, _eof_complete = observed_route_sources(
            eof_trace["actions"], "opencode", workspace,
        )
        eof_route = entry_route_evidence(
            eof_trace["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        if eof_sources or eof_route.get("status") == "pass":
            failures.append("empty far-past-EOF reads earned authoritative-source or first-read route credit")
        claimed_output_eof = trace_for([_structured_event(
            "read", "eof-with-output",
            {"filePath": "README.md", "offset": 100_000_000, "limit": 1},
            output="synthetic nonempty output",
        )])
        if content_read_observations(claimed_output_eof["actions"], "opencode", workspace):
            failures.append("a nonempty claimed read output over a far-past-EOF range was credited")

        partial_read = trace_for([native_read(
            "partial-range-read", "README.md", offset=1, limit=1, output="partial first line",
        )])
        empty_read = trace_for([native_read(
            "empty-range-read", "README.md", offset=1, limit=line_count("README.md"), output="",
        )])
        denied_read = trace_for([native_read(
            "denied-range-read", "README.md", offset=1, limit=line_count("README.md"),
            output="synthetic content", status="denied",
        )])
        unknown_read = trace_for([native_read(
            "missing-identity-read", "README.md", offset=1, limit=line_count("README.md"),
            identity=False,
        )])
        invalid_read = trace_for([native_read(
            "invalid-offset-read", "README.md", offset=True, limit=line_count("README.md"),
        )])
        zero_limit_read = trace_for([native_read(
            "invalid-zero-limit-read", "README.md", offset=1, limit=0,
        )])
        if any(content_read_observations(trace["actions"], "opencode", workspace) for trace in (
            partial_read, empty_read, denied_read, unknown_read, invalid_read, zero_limit_read,
        )):
            failures.append("partial, empty, denied, unknown-identity, or invalid native reads earned whole-source credit")

        partial_route = trace_for([
            native_read("partial-route-readme", "README.md", offset=1, limit=1, output="partial first line"),
            native_read("partial-route-agents", "AGENTS.md", offset=1, limit=line_count("AGENTS.md")),
            _event("bash", "partial-route-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            native_read(
                "partial-route-task", "docs/agents/first-contribution.md",
                offset=1, limit=line_count("docs/agents/first-contribution.md"),
            ),
        ])
        empty_route = trace_for([
            native_read("empty-route-readme", "README.md", offset=1, limit=line_count("README.md"), output=""),
            native_read("empty-route-agents", "AGENTS.md", offset=1, limit=line_count("AGENTS.md")),
            _event("bash", "empty-route-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            native_read(
                "empty-route-task", "docs/agents/first-contribution.md",
                offset=1, limit=line_count("docs/agents/first-contribution.md"),
            ),
        ])
        for label, route_trace in (("partial", partial_route), ("empty", empty_route)):
            route_result = entry_route_evidence(
                route_trace["actions"], "opencode", workspace,
                task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
            )
            if route_result.get("status") == "pass":
                failures.append(f"{label} structured root read satisfied the entry route")

        search_only = trace_for([
            _structured_event("grep", "grep-readme", {"path": "README.md", "pattern": "source"}),
            _structured_event("grep", "grep-agents", {"path": "AGENTS.md", "pattern": "source"}),
            _event("bash", "grep-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            _structured_event(
                "grep", "grep-task-route",
                {"path": "docs/agents/first-contribution.md", "pattern": "source"},
            ),
        ])
        search_route = entry_route_evidence(
            search_only["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        search_kinds = {item["kind"] for item in search_route["content_read_observations"]}
        if search_route.get("status") == "pass" or "native_content_search" not in search_kinds:
            failures.append("native grep search was conflated with whole-source route reads")

        search_before_roots = trace_for([
            _structured_event(
                "grep", "early-task-search",
                {"path": "docs/agents/first-contribution.md", "pattern": "route"},
            ),
            _event("read", "after-search-readme", path="README.md"),
            _event("read", "after-search-agents", path="AGENTS.md"),
            _event("bash", "after-search-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
            _event("read", "after-search-task", path="docs/agents/first-contribution.md"),
        ])
        search_before_route = entry_route_evidence(
            search_before_roots["actions"], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        if search_before_route.get("status") == "pass":
            failures.append("a pre-root content search was ignored when checking first-content-source order")

        injected_only = entry_route_evidence(
            [], "opencode", workspace,
            task="contribute", task_route_paths=("docs/agents/first-contribution.md",),
        )
        if injected_only.get("status") == "pass":
            failures.append("injected task/protocol text was mistaken for observed entry-route evidence")


def _codex_file_change_regressions(failures: list[str]) -> None:
    workspace = Path("/tmp/v2-workspace")
    target = "docs/example.md"

    def file_event(event_type: str, item_id: str | None, changes, *, status: str) -> dict:
        item = {"type": "file_change", "status": status, "changes": changes}
        if item_id is not None:
            item["id"] = item_id
        return {"type": event_type, "item": item}

    def normalize(events: list[dict]) -> dict:
        raw = b"".join(
            (json.dumps(event, separators=(",", ":")) + "\n").encode()
            for event in events
        )
        return normalize_codex(raw, workspace, REPO)

    duplicate_events = [
        file_event("item.started", "duplicate-file-item", [{"path": target, "kind": "update"}], status="in_progress"),
        file_event("item.completed", "duplicate-file-item", [{"path": target, "kind": "update"}], status="completed"),
        file_event("item.completed", "duplicate-file-item", [{"path": target, "kind": "update"}], status="completed"),
    ]
    duplicate_trace = normalize(duplicate_events)
    duplicate_actions = [
        action for action in duplicate_trace["actions"]
        if action.get("native_item_id") == "duplicate-file-item"
    ]
    if (
        len(duplicate_actions) != 1
        or duplicate_actions[0].get("change_index") != 0
        or duplicate_actions[0].get("event_sequence") != 0
        or duplicate_actions[0].get("completion_sequence") != 1
        or duplicate_actions[0].get("terminal_event_count") != 2
        or _path_write_sequences(duplicate_actions, target, "codex", workspace) != [1]
        or _path_write_sequences([*duplicate_actions, *duplicate_actions], target, "codex", workspace) != [1]
    ):
        failures.append("duplicate Codex file_change terminals created multiple normalized writes or boundaries")

    conflicting_trace = normalize([
        file_event("item.completed", "conflicting-file-item", [{"path": target, "kind": "update"}], status="completed"),
        file_event("item.completed", "conflicting-file-item", [{"path": "docs/other.md", "kind": "update"}], status="completed"),
    ])
    conflicting_actions = [
        action for action in conflicting_trace["actions"]
        if action.get("native_item_id") == "conflicting-file-item"
    ]
    if (
        len(conflicting_actions) != 1
        or conflicting_actions[0].get("lifecycle_conflict") is not True
        or conflicting_actions[0].get("completion_sequence") is not None
        or conflicting_actions[0].get("correlation_conflict") is not True
        or _path_write_sequences(conflicting_actions, target, "codex", workspace)
    ):
        failures.append("conflicting Codex file_change terminals retained a usable write attribution")

    unknown_id_trace = normalize([
        file_event("item.completed", None, [{"path": target, "kind": "update"}], status="completed"),
        file_event("item.completed", None, [{"path": target, "kind": "update"}], status="completed"),
    ])
    unknown_id_actions = [action for action in unknown_id_trace["actions"] if action.get("tool") == "file_change"]
    if (
        len(unknown_id_actions) != 2
        or any(action.get("native_item_id_sha256") is not None for action in unknown_id_actions)
        or _path_write_sequences(unknown_id_actions, target, "codex", workspace)
        or _direct_access_audit_complete(
            unknown_id_trace, unknown_id_actions, "codex", workspace,
        )
    ):
        failures.append("Codex file changes without native item identity were inferred from call count")

    missing_index_trace = normalize([
        file_event("item.completed", "missing-change-index", [], status="completed"),
    ])
    missing_index_actions = missing_index_trace["actions"]
    if (
        len(missing_index_actions) != 1
        or missing_index_actions[0].get("change_index") is not None
        or _path_write_sequences(missing_index_actions, target, "codex", workspace)
    ):
        failures.append("Codex file_change without a change index was treated as a completed write")

    multi_file_trace = normalize([
        file_event(
            "item.completed", "multi-file-change",
            [{"path": target, "kind": "update"}, {"path": "docs/other.md", "kind": "add"}],
            status="completed",
        ),
    ])
    multi_file_actions = multi_file_trace["actions"]
    indexes = sorted(action.get("change_index") for action in multi_file_actions)
    if (
        indexes != [0, 1]
        or _path_write_sequences(multi_file_actions, target, "codex", workspace) != [0]
        or _path_write_sequences(multi_file_actions, "docs/other.md", "codex", workspace) != [0]
    ):
        failures.append("distinct files in one Codex file_change item lost their indexed writes")


def _diagnostic_regressions(failures: list[str]) -> None:
    schema_path = HERE / "diagnostic.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        failures.append("shared diagnostic schema is missing or invalid JSON")
        schema = {}
    if set(schema.get("required", [])) != {
        "code", "category", "severity", "actionable", "summary", "native_action",
        "artifact_path", "observed_evidence", "missing_evidence", "next_step",
    }:
        failures.append("shared diagnostic schema omits its typed evidence contract")
    native_contract = schema.get("properties", {}).get("native_action", {}).get("anyOf", [None, {}])[1]
    if not isinstance(native_contract, dict) or not {
        "action_index", "event_sequence", "completion_sequence", "identity_kind",
        "identity_sha256", "tool", "command",
    } <= set(native_contract.get("required", [])):
        failures.append("native diagnostic reference omits action index, tool, or sanitized command")

    denied_identity = hashlib.sha256(b"denied-read").hexdigest()
    denied_action = {
        "tool": "read", "event_sequence": 7, "completion_sequence": 8,
        "native_tool_call_id_sha256": denied_identity,
        "outcome": {"native_tool_status": "denied"},
    }
    denied_diagnostics = build_failure_diagnostics(
        ["required native read was denied"], [denied_action], "opencode",
        scope="docs", repo=REPO,
    )
    permission = next((item for item in denied_diagnostics if item["category"] == "native_permission_denial"), None)
    if (
        permission is None
        or permission.get("code") != "native.permission_denied"
        or permission.get("native_action", {}).get("action_index") != 0
        or permission.get("native_action", {}).get("event_sequence") != 7
        or permission.get("native_action", {}).get("identity_sha256") != denied_identity
        or "permission" not in permission.get("next_step", "").casefold()
        or "bypass" in permission.get("next_step", "").casefold()
    ):
        failures.append("permission-denial diagnostic omitted exact action attribution or a safe bounded next step")

    unsupported_action = {
        "tool": "bash", "event_sequence": 11, "completion_sequence": 12,
        "native_tool_call_id_sha256": hashlib.sha256(b"unsupported-shell").hexdigest(),
        "command_truncated": True,
    }
    unsupported_reason = "native command evidence is truncated and unsupported"
    unsupported = build_failure_diagnostics(
        [unsupported_reason], [unsupported_action],
        "opencode", scope="docs", repo=REPO,
        causal_actions={unsupported_reason: unsupported_action},
    )[0]
    artifact = build_failure_diagnostics(
        ["completion schema does not match the disclosed artifact contract"], [],
        "opencode", scope="docs", artifact_paths=("trial-output/completion.json",), repo=REPO,
    )[0]
    route_identity = hashlib.sha256(b"entry-route-read").hexdigest()
    route_action = {
        "tool": "read", "event_sequence": 2, "completion_sequence": 3,
        "native_item_id_sha256": route_identity,
    }
    route_reason = "entrypoint-first route requirements were not observed"
    protocol = build_failure_diagnostics(
        [route_reason], [route_action],
        "codex", scope="docs", repo=REPO,
    )[0]
    selector_action = {
        "tool": "bash", "event_sequence": 2, "completion_sequence": 3,
        "native_tool_call_id_sha256": hashlib.sha256(b"route-selector").hexdigest(),
    }
    validator_action = {
        "tool": "bash", "event_sequence": 8, "completion_sequence": 9,
        "native_tool_call_id_sha256": hashlib.sha256(b"actual-validator").hexdigest(),
        "command": "python .github/scripts/validate_agent_docs.py truthfulness",
    }
    validation_reason = "validation.baseline does not match the latest exact command result in its causal phase"
    attributed = build_failure_diagnostics(
        [validation_reason], [selector_action, validator_action],
        "opencode", scope="docs", repo=REPO,
        causal_actions={validation_reason: validator_action},
    )[0]
    conflicting_cause = {**validator_action, "completion_sequence": 10}
    ambiguous_attribution = build_failure_diagnostics(
        [validation_reason], [selector_action, validator_action],
        "opencode", scope="docs", repo=REPO,
        causal_actions={validation_reason: [validator_action, conflicting_cause]},
    )[0]
    prose_denial = build_failure_diagnostics(
        ["Native permission denied during baseline validation"], [],
        "opencode", scope="docs", repo=REPO,
    )
    runtime = runtime_unavailable_diagnostic("Selected native runtime executable is unavailable", repo=REPO)
    if (
        unsupported.get("category") != "unsupported_evidence_format"
        or unsupported.get("native_action", {}).get("action_index") != 0
        or unsupported.get("native_action", {}).get("tool") != "bash"
        or unsupported.get("native_action", {}).get("event_sequence") != 11
        or artifact.get("category") != "artifact_schema_error"
        or artifact.get("artifact_path") != "trial-output/completion.json"
        or protocol.get("category") != "protocol_violation"
        or protocol.get("native_action") is not None
        or not any("uniquely identified causal native action" in item for item in protocol.get("missing_evidence", []))
        or attributed.get("native_action", {}).get("event_sequence") != 8
        or attributed.get("native_action", {}).get("action_index") != 1
        or attributed.get("native_action", {}).get("tool") != "bash"
        or attributed.get("native_action", {}).get("command")
        != "python .github/scripts/validate_agent_docs.py truthfulness"
        or attributed.get("native_action", {}).get("identity_sha256") != validator_action["native_tool_call_id_sha256"]
        or ambiguous_attribution.get("native_action") is not None
        or not any("uniquely identified causal native action" in item for item in ambiguous_attribution.get("missing_evidence", []))
        or any(item.get("category") == "native_permission_denial" for item in prose_denial)
        or runtime.get("category") != "runtime_unavailability"
    ):
        failures.append("diagnostic categories did not distinguish evidence, artifact, protocol, and runtime cases")

    identity_missing_denial = build_failure_diagnostics(
        ["required native operation was denied"],
        [{"tool": "read", "event_sequence": 4, "completion_sequence": 5,
          "outcome": {"native_tool_status": "denied"}}],
        "opencode", scope="docs", repo=REPO,
    )
    denied_with_no_identity = next(
        (item for item in identity_missing_denial if item.get("category") == "native_permission_denial"),
        None,
    )
    if (
        denied_with_no_identity is None
        or denied_with_no_identity.get("native_action") is not None
        or not any("uniquely identified causal native action" in item for item in denied_with_no_identity.get("missing_evidence", []))
    ):
        failures.append("permission denial without a hashed action identity was attributed as an exact native action")

    private_path = "/home/" + "private-" + "user/secret.txt"
    secret_token = "sk-" + ("a" * 32)
    private_reason = f"artifact schema failed at {private_path} with Authorization: Bearer {secret_token}"
    private_action = {
        "tool": "bash", "event_sequence": 1, "completion_sequence": 2,
        "native_tool_call_id_sha256": hashlib.sha256(b"private-diagnostic-action").hexdigest(),
        "command": f"cat {private_path} {secret_token}",
    }
    private = build_failure_diagnostics(
        [private_reason], [private_action], "opencode", scope="docs", repo=REPO,
        causal_actions={private_reason: private_action},
    )[0]
    diagnostic_text = json.dumps(private, sort_keys=True)
    if (
        private_path in diagnostic_text
        or secret_token in diagnostic_text
        or "secret.txt" in diagnostic_text
        or private.get("native_action", {}).get("action_index") != 0
        or private.get("native_action", {}).get("tool") != "bash"
        or not isinstance(private.get("native_action", {}).get("command"), str)
    ):
        failures.append("diagnostic text exposed a private host path or secret-shaped string")

    negative_report = {
        "comparison_status": "complete_equal_calls",
        "runs": [{
            "role": "development", "input_id": "development-input-1",
            "paired_comparison": {
                "comparison_status": "complete_equal_calls",
                "unique_valid_cell_difference": -1,
                "synthetic_quality_pass_difference": 0,
            },
        }],
    }
    comparison = research_comparison_diagnostic(negative_report, repo=REPO)
    if (
        comparison is None
        or comparison.get("category") != "research_comparison_outcome"
        or comparison.get("severity") != "info"
        or comparison.get("artifact_path") != "trial-output/comparison-report.json"
        or comparison.get("actionable") is not True
    ):
        failures.append("negative/mixed synthetic comparison was not an informational research outcome diagnostic")


def _drift_regression(failures: list[str]) -> None:
    try:
        draft = manifest.build_manifest(REPO, "selfcheck-series")
        manifest._verify_frozen_files(REPO, draft)
    except manifest.ManifestError as exc:
        failures.append(f"named frozen rubric closure is not self-consistent: {exc}")
        return
    required_closure = {
        ".gitignore",
        ".github/scripts/smoke_first_contribution.py",
        "examples/fresh-agent-evals/v2/shell_parser.py",
        "examples/fresh-agent-evals/v2/native_arguments.py",
        "examples/fresh-agent-evals/v2/diagnostics.py",
        "examples/fresh-agent-evals/v2/diagnostic.schema.json",
        "CONTRIBUTING.md",
        "docs/agents/diagnostics.md",
        "docs/agents/references/compatibility.json",
        "docs/agents/research-opportunities.json",
        "docs/agents/start-here.md",
        "src/openforage/skills/agent_runtime_integration/references/harnesses.json",
        "src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json",
        "src/openforage/skills/agent_runtime_integration/references/evidence/opencode-native-marker-20260922.json",
        "examples/background-agent/native-contribution-wave-20260924/codex-proof.json",
        "examples/background-agent/native-contribution-wave-20260924/opencode-proof.json",
        "scripts/onboarding/__init__.py",
        "scripts/onboarding/capabilities.py",
        "scripts/onboarding/entry_fragments.py",
    }
    if required_closure - draft["frozen_files"].keys():
        failures.append("new frozen manifest omitted a transitive evaluator, smoke, or ignore-rule dependency")

    with tempfile.TemporaryDirectory(prefix="v2-ignore-closure-") as temporary:
        probe_root = Path(temporary)
        probe_inputs = {
            ".gitignore": ".ruff_cache/\n",
            ".github/.gitignore": "github rules\n",
            ".github/scripts/.gitignore": "smoke rules\n",
            ".github/scripts/smoke_first_contribution.py": "pass\n",
            ".github/scripts/validation/common.py": (
                REPO / ".github/scripts/validation/common.py"
            ).read_text(encoding="utf-8"),
            "examples/fresh-agent-evals/v2/.gitignore": "suite rules\n",
            "examples/fresh-agent-evals/v2/nested/.gitignore": "nested rules\n",
            "examples/fresh-agent-evals/v2/nested/validator.py": "pass\n",
            "docs/depositors/faq.md": "# public copy selection input\n",
            ".ruff_cache/.gitignore": "private cache rule\n",
            "src/openforage/skills/agent_runtime_integration/references/harnesses.json": "[]\n",
        }
        for relative, content in probe_inputs.items():
            path = probe_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        before_rule_copy = probe_root.parent / f"{probe_root.name}-before-rule"
        before_rule_selection = set(copy_public_tree(probe_root, before_rule_copy))
        nested_rule = probe_root / "docs/depositors/.gitignore"
        nested_rule.write_text("faq.md\n", encoding="utf-8")
        after_rule_copy = probe_root.parent / f"{probe_root.name}-after-rule"
        after_rule_selection = set(copy_public_tree(probe_root, after_rule_copy))
        probe_closure = set(manifest.frozen_paths(probe_root))
        expected_rules = {
            ".gitignore", ".github/.gitignore", ".github/scripts/.gitignore",
            "examples/fresh-agent-evals/v2/.gitignore",
            "examples/fresh-agent-evals/v2/nested/.gitignore",
            "docs/depositors/.gitignore",
            ".github/scripts/smoke_first_contribution.py",
        }
        if (
            expected_rules - probe_closure
            or ".ruff_cache/.gitignore" in probe_closure
            or "docs/depositors/faq.md" not in before_rule_selection
            or "docs/depositors/faq.md" in after_rule_selection
            or "docs/depositors/.gitignore" not in after_rule_selection
        ):
            failures.append("frozen closure did not follow public-copy ignore rules or exclude private cache rules")

    original = manifest.file_sha256
    for relative in (
        "examples/fresh-agent-evals/v2/cases.json",
        ".gitignore",
        ".github/scripts/smoke_first_contribution.py",
    ):
        target = REPO / relative

        def changed(path: Path, target: Path = target) -> str:
            return "0" * 64 if path.resolve() == target.resolve() else original(path)

        try:
            with mock.patch.object(manifest, "file_sha256", side_effect=changed):
                manifest._verify_frozen_files(REPO, draft)
        except manifest.ManifestError:
            continue
        failures.append(f"frozen manifest accepted drift in {relative}")
        break


def _process_regressions(failures: list[str]) -> None:
    from runner import _child_environment

    child_env = _child_environment()
    python_path = shutil.which("python", path=child_env.get("PATH"))
    if python_path is None:
        failures.append("runner interpreter is absent from the child PATH")
    else:
        probe = run_bounded(
            [python_path, "-B", "-c",
             "import json,os,sys;print(json.dumps({'executable':os.path.abspath(sys.executable),'prefix':sys.prefix,'bytecode':os.environ.get('PYTHONDONTWRITEBYTECODE')}))"],
            cwd=REPO, env=child_env, timeout=10,
        )
        try:
            identity = json.loads(probe.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            identity = {}
        if (
            probe.returncode != 0 or probe.timed_out or probe.process_group_remaining
            or identity.get("prefix") != sys.prefix
            or identity.get("executable") != os.path.abspath(sys.executable)
            or identity.get("bytecode") != "1"
        ):
            failures.append("child python did not preserve the invoked interpreter/sys.prefix and bytecode policy")

    success = run_bounded([sys.executable, "-c", "pass"], cwd=REPO, timeout=5)
    if success.returncode != 0 or success.timed_out or success.process_group_remaining:
        failures.append("bounded subprocess success did not report an empty owned process group")
    spawn_child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
        "time.sleep(30)"
    )
    timed = run_bounded([sys.executable, "-c", spawn_child], cwd=REPO, timeout=0.2)
    if not timed.timed_out or timed.process_group_remaining:
        failures.append("timeout cleanup did not terminate and accurately report the owned process group")


def _synthetic_events(
    target: str,
    stamp: float,
    *,
    completion_exit: int = 0,
    late_diff_check: bool = False,
) -> list[dict]:
    case_id = "first-finished-contribution-primary"
    contract = load_fixture_contract(REPO, "contribute", case_id)
    commands = {
        item["check_id"]: item["command"]
        for item in render_finish_commands(contract, "contribute", None)
    }
    events = [{"type": "session", "sessionID": "synthetic-session-id", "timestamp": stamp - 1}]
    events.extend([
        _event("read", "read-root", path="README.md", stamp=stamp),
        _event("read", "read-agents", path="AGENTS.md", stamp=stamp + 0.05),
        _event("bash", "contribute-selector", command="python scripts/start_here.py --task contribute --fixture first-finished-contribution-primary", exit_code=0, stamp=stamp + 0.08),
        _event("read", "read-guide", path="docs/agents/first-contribution.md", stamp=stamp + 0.1),
        _event(
            "read", "read-disclosed-completion-schema",
            path="examples/fresh-agent-evals/v2/completion.schema.json", stamp=stamp + 0.12,
        ),
        {
            "type": "tool_use",
            "part": {
                "type": "tool", "tool": "read", "callID": "native-protected-directory-listing",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "examples/fresh-agent-evals/research"},
                    "output": {
                        "type": "directory",
                        "path": "examples/fresh-agent-evals/research",
                        "entries": ["research_grader.py"],
                    },
                },
            },
        },
        _event(
            "bash", "shell-protected-directory-listing",
            command="ls examples/fresh-agent-evals/research", exit_code=0,
            stamp=stamp + 0.15, output="research_grader.py\n",
        ),
        _event("bash", "baseline-check", command=commands["docs-baseline-validation"], exit_code=1, stamp=stamp + 0.2),
        _event("edit", "write-page", path=target, stamp=stamp + 0.3),
        _event("bash", "final-check", command=commands["docs-final-validation"], exit_code=0, stamp=stamp + 0.4),
        _event("write", "write-completion", path=COMPLETION_REL, stamp=stamp + 0.5),
        _event("bash", "diff-check", command=commands["docs-git-diff-check"], exit_code=0, stamp=stamp + 0.6),
        _event("bash", "final-completion-check", command=commands["docs-completion-check"], exit_code=completion_exit, stamp=stamp + 0.7),
    ])
    if late_diff_check:
        events.append(_event("bash", "late-diff-check", command=commands["docs-git-diff-check"], exit_code=0, stamp=stamp + 0.8))
    return events


def _forgery_regressions(failures: list[str]) -> None:
    case_id = "first-finished-contribution-primary"
    command = "python .github/scripts/validate_agent_docs.py truthfulness"
    claim = {"command": command, "modes": ["truthfulness"], "exit_code": 0, "result": "passed"}
    if _find_action_by_declared_command(claim, [], "opencode", Path("/tmp/v2-workspace"), 0) is not None:
        failures.append("self-reported JSON success without a native action passed anti-forgery check")

    denied = normalize_opencode(
        (json.dumps(_event("bash", "denied-validator", command=command, exit_code=0, status="denied")) + "\n").encode(),
        Path("/tmp/v2-workspace"), REPO,
    )["actions"]
    if _find_action_by_declared_command(claim, denied, "opencode", Path("/tmp/v2-workspace"), 0) is not None:
        failures.append("refused validator tool with a fake exit field passed anti-forgery check")

    masked_event = _event("bash", "masked-validator", command=command + "; true", exit_code=0)
    masked_actions = normalize_opencode((json.dumps(masked_event) + "\n").encode(), Path("/tmp/v2-workspace"), REPO)["actions"]
    if _find_action_by_declared_command(claim, masked_actions, "opencode", Path("/tmp/v2-workspace"), 0) is not None:
        failures.append("masked `; true` validator tool result passed anti-forgery check")

    fake = _valid_completion(case_id, "docs/example.md", command)
    try:
        with tempfile.TemporaryDirectory(prefix="of-v2-forgery-") as temporary:
            workspace = Path(temporary)
            output = workspace / "trial-output"
            output.mkdir()
            (output / "completion.json").write_text(json.dumps(fake), encoding="utf-8")
            initial = {"README.md": "0" * 64}
            result = _check_completion_claims(REPO, workspace, case_id, [], "opencode", initial)
            if not result["failures"]:
                failures.append("fake log/self-report without tools or diff passed completion checks")
    except OSError as exc:
        failures.append(f"anti-forgery synthetic setup failed: {type(exc).__name__}")


def _phase_claim_result(completion: dict, target: str, *, before_doc=(), between_doc_writes=(),
                        after_doc=(), after_completion=(), doc_write_count: int = 1) -> dict:
    events = [
        _event("read", "phase-root-read", path="README.md"),
        _event("read", "phase-agents-read", path="AGENTS.md"),
        _event("bash", "phase-route-selector", command="python scripts/start_here.py --task contribute", exit_code=0),
        _event("read", "phase-route-read", path="docs/agents/first-contribution.md"),
    ]

    def append_checks(checks):
        for call_id, command, exit_code, status in checks:
            events.append(_event("bash", call_id, command=command, exit_code=exit_code, status=status))

    append_checks(before_doc)
    for index in range(doc_write_count):
        events.append(_event("edit", f"phase-doc-write-{index}", path=target))
        if index + 1 < doc_write_count:
            append_checks(between_doc_writes)
    append_checks(after_doc)
    events.append(_event("write", "phase-completion-write", path=COMPLETION_REL))
    append_checks(after_completion)

    with tempfile.TemporaryDirectory(prefix="of-v2-phase-claims-") as temporary:
        workspace = Path(temporary)
        for relative in (
            "README.md", "AGENTS.md", "CLAUDE.md", "docs/agents/start-here.md",
            "docs/agents/first-contribution.md",
        ):
            route_source = workspace / relative
            route_source.parent.mkdir(parents=True, exist_ok=True)
            route_source.write_text(f"observed source: {relative}\n", encoding="utf-8")
        output = workspace / "trial-output"
        output.mkdir()
        (output / "completion.json").write_text(json.dumps(completion), encoding="utf-8")
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in events)
        normalized = normalize_opencode(raw, workspace, REPO)
        return _check_completion_claims(
            REPO, workspace, "first-finished-contribution-primary",
            normalized["actions"], "opencode", {},
        )


def _validation_phase_regressions(failures: list[str]) -> None:
    case_id = "first-finished-contribution-primary"
    oracle = json.loads((REPO / "examples/fresh-agent-evals/v2/oracle.json").read_text(encoding="utf-8"))
    target = oracle["cases"][case_id]["fixture"]["path"]
    command = "python .github/scripts/validate_agent_docs.py truthfulness"

    def completion(baseline_code: int = 1, final_code: int = 0) -> dict:
        value = _valid_completion(case_id, target, command)
        value["validation"]["baseline"] = {
            "command": command, "modes": ["truthfulness"],
            "exit_code": baseline_code, "result": "failed" if baseline_code != 0 else "passed",
        }
        value["validation"]["final"] = {
            "command": command, "modes": ["truthfulness"],
            "exit_code": final_code, "result": "passed" if final_code == 0 else "failed",
        }
        return value

    repeated = _phase_claim_result(
        completion(), target,
        before_doc=[
            ("baseline-earlier", command, 0, "completed"),
            ("baseline-boundary", command, 1, "completed"),
        ],
        after_doc=[
            ("final-earlier", command, 1, "completed"),
            ("final-boundary", command, 0, "completed"),
        ],
        after_completion=[("optional-post-completion-recheck", command, 1, "completed")],
    )
    if repeated["failures"]:
        failures.append("same-command repeated phase checks did not correlate at observed boundaries: " + "; ".join(repeated["failures"]))
    elif (
        repeated["baseline_action"].get("native_tool_call_id_sha256") != hashlib.sha256(b"baseline-boundary").hexdigest()
        or repeated["final_action"].get("native_tool_call_id_sha256") != hashlib.sha256(b"final-boundary").hexdigest()
    ):
        failures.append("phase correlation did not select the latest exact result before each artifact boundary")

    for launch_code in (126, 127):
        launch_claim = completion(baseline_code=launch_code)
        launch_result = _phase_claim_result(
            launch_claim, target,
            before_doc=[(f"baseline-shell-launch-{launch_code}", command, launch_code, "error")],
            after_doc=[(f"final-after-launch-{launch_code}", command, 0, "completed")],
        )
        launch_reason = next(
            (reason for reason in launch_result["failures"] if "runtime unavailable" in reason),
            None,
        )
        cause = launch_result.get("diagnostic_causes", {}).get(launch_reason) if launch_reason else None
        launch_observations = validator_observations(
            [cause], "opencode", Path("/tmp/v2-workspace"),
        ) if isinstance(cause, dict) else []
        launch_diagnostic = build_failure_diagnostics(
            [launch_reason] if launch_reason else ["missing launch failure diagnosis"],
            [cause] if isinstance(cause, dict) else [],
            "opencode", scope="docs",
            causal_actions={launch_reason: cause} if launch_reason and isinstance(cause, dict) else {},
        )[0]
        if (
            launch_result.get("baseline_action") is not None
            or launch_reason is None
            or len(launch_observations) != 1
            or launch_observations[0].get("native_shell_exit_code") != launch_code
            or launch_observations[0].get("exit_code") is not None
            or launch_diagnostic.get("category") != "runtime_unavailability"
            or launch_diagnostic.get("native_action", {}).get("identity_sha256") != cause.get("native_tool_call_id_sha256")
        ):
            failures.append(f"native shell launch exit {launch_code} was accepted as a substantive failed baseline validator")

    and_command = (
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py "
        "truthfulness links && git diff --check"
    )
    and_completion = completion()
    and_completion["validation"]["final"] = {
        "command": and_command, "modes": ["truthfulness", "links"],
        "exit_code": 0, "result": "passed",
    }
    successful_chain = _phase_claim_result(
        and_completion, target,
        before_doc=[("and-baseline", command, 1, "completed")],
        after_doc=[("and-final", and_command, 0, "completed")],
    )
    chain_observations = validator_observations(
        [successful_chain.get("final_action")], "opencode", Path("/tmp/v2-workspace"),
    ) if successful_chain.get("final_action") is not None else []
    if (
        successful_chain["failures"]
        or successful_chain.get("final_action") is None
        or len(chain_observations) != 1
        or chain_observations[0].get("exit_code") != 0
        or chain_observations[0].get("compound_shell_exit_code") != 0
        or chain_observations[0].get("inference_basis") != "explicit_native_exit_zero_for_fully_admitted_and_chain"
    ):
        failures.append("explicit native exit zero did not prove the validator segment passed in the admitted AND chain")

    former_chain = (
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py truthfulness && "
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py links && "
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py evals && "
        "git diff --check"
    )
    multi_completion = completion()
    multi_completion["validation"]["final"] = {
        "command": former_chain,
        "modes": ["truthfulness", "links", "evals"],
        "exit_code": 0,
        "result": "passed",
    }
    multi_result = _phase_claim_result(
        multi_completion, target,
        before_doc=[("multi-baseline", command, 1, "completed")],
        after_doc=[("former-multi-final", former_chain, 0, "completed")],
    )
    multi_provenance = _validation_outcome_for_action(
        multi_result.get("final_action"), "opencode", Path("/tmp/v2-workspace"),
    ) if multi_result.get("final_action") is not None else None
    if (
        multi_result.get("failures")
        or not isinstance(multi_provenance, dict)
        or multi_provenance.get("validator_segment_indices") != [0, 1, 2]
        or multi_provenance.get("validator_segment_exit_code") != 0
    ):
        failures.append("completion claim did not correlate the exact former multi-validator chain in its causal phase")

    nonzero_multi = json.loads(json.dumps(multi_completion))
    nonzero_multi["validation"]["baseline"] = {
        "command": former_chain,
        "modes": ["truthfulness", "links", "evals"],
        "exit_code": 1,
        "result": "failed",
    }
    nonzero_result = _phase_claim_result(
        nonzero_multi, target,
        before_doc=[("former-multi-nonzero", former_chain, 1, "completed")],
        after_doc=[("single-final-after-nonzero-chain", command, 0, "completed")],
    )
    if nonzero_result.get("baseline_action") is not None or not any(
        "compound-shell nonzero exit does not identify" in item
        for item in nonzero_result.get("failures", [])
    ):
        failures.append("nonzero former chain was reported as three known validator failures")

    for chain_index, chain_command in enumerate((
        "false && python .github/scripts/validate_agent_docs.py truthfulness",
        "python .github/scripts/validate_agent_docs.py truthfulness && false",
    )):
        chain_baseline = completion()
        chain_baseline["validation"]["baseline"] = {
            "command": chain_command, "modes": ["truthfulness"],
            "exit_code": 1, "result": "failed",
        }
        unknown_chain = _phase_claim_result(
            chain_baseline, target,
            before_doc=[(f"ambiguous-chain-{chain_index}", chain_command, 1, "completed")],
            after_doc=[(f"ambiguous-final-{chain_index}", command, 0, "completed")],
        )
        if unknown_chain.get("baseline_action") is not None or not any(
            "compound-shell nonzero exit does not identify" in reason
            for reason in unknown_chain["failures"]
        ):
            failures.append("nonzero AND-chain exit was incorrectly attributed to its validator segment")

    false_chain_command = "python .github/scripts/validate_agent_docs.py truthfulness && false"
    false_chain_completion = completion()
    false_chain_completion["validation"]["final"] = {
        "command": false_chain_command, "modes": ["truthfulness"],
        "exit_code": 0, "result": "passed",
    }
    impossible_zero_chain = _phase_claim_result(
        false_chain_completion, target,
        before_doc=[("false-chain-baseline", command, 1, "completed")],
        after_doc=[("false-chain-zero", false_chain_command, 0, "completed")],
    )
    if impossible_zero_chain.get("final_action") is not None or not any(
        "conflicts with a literal false" in reason for reason in impossible_zero_chain["failures"]
    ):
        failures.append("zero shell exit contradicted by a literal false segment was accepted as validator success")

    wrong_original = _phase_claim_result(
        completion(), target,
        before_doc=[("original-baseline", command, 1, "completed")],
        after_doc=[("different-full-command", and_command, 0, "completed")],
    )
    if wrong_original.get("final_action") is not None:
        failures.append("validator segment from a different full original command satisfied the claim")

    codex_baseline_command = "python .github/scripts/validate_agent_docs.py all"
    codex_final_command = (
        "PYTHONDONTWRITEBYTECODE=1 python .github/scripts/validate_agent_docs.py "
        "truthfulness links && git diff --check"
    )
    codex_completion = _valid_completion(case_id, target, codex_final_command)
    codex_completion["validation"]["final"] = {
        "command": codex_final_command, "modes": ["truthfulness", "links"],
        "exit_code": 0, "result": "passed",
    }
    codex_completion["validation"]["baseline"] = {
        "command": codex_baseline_command, "modes": ["all"], "exit_code": 1, "result": "failed",
    }

    def codex_command(item_id: str, command_text: str, exit_code: int) -> dict:
        return {
            "type": "item.completed",
            "item": {
                "id": item_id, "type": "command_execution",
                "command": f"/bin/bash -lc '{command_text}'",
                "status": "failed" if exit_code else "completed", "exit_code": exit_code,
                "aggregated_output": "synthetic bounded command output",
            },
        }

    nested_inner = f"bash -lc {shlex.quote(codex_baseline_command)}"
    nested_native = f"/bin/bash -lc {shlex.quote(nested_inner)}"
    nested_launch_raw = (
        json.dumps({"type": "thread.started", "thread_id": "codex-launch-error-session"}) + "\n"
        + json.dumps({
            "type": "item.completed",
            "item": {
                "id": "codex-launch-error", "type": "command_execution",
                "command": nested_native, "status": "failed", "exit_code": 127,
            },
        }) + "\n"
    ).encode()
    nested_launch_trace = normalize_codex(nested_launch_raw, Path("/tmp/v2-workspace"), REPO)
    nested_launch_failures: list[str] = []
    nested_launch_causes: dict[str, object] = {}
    nested_launch_selected = _check_validation_claim(
        "baseline",
        {"command": codex_baseline_command, "modes": ["all"], "exit_code": 127, "result": "failed"},
        "failed", nested_launch_trace["actions"], "codex", Path("/tmp/v2-workspace"), {"all"},
        nested_launch_failures, lower_exclusive=None, upper_exclusive=5,
        causal_actions=nested_launch_causes,
    )
    nested_launch_reason = next(
        (reason for reason in nested_launch_failures if "runtime unavailable" in reason),
        None,
    )
    nested_launch_diagnostic = build_failure_diagnostics(
        [nested_launch_reason] if nested_launch_reason else ["missing nested launch diagnosis"],
        nested_launch_trace["actions"], "codex", scope="docs",
        causal_actions=nested_launch_causes,
    )[0]
    if (
        nested_launch_selected is not None
        or nested_launch_reason is None
        or len(validator_observations(nested_launch_trace["actions"], "codex", Path("/tmp/v2-workspace"))) != 1
        or nested_launch_diagnostic.get("category") != "runtime_unavailability"
        or nested_launch_diagnostic.get("native_action", {}).get("identity_sha256")
        != nested_launch_trace["actions"][0].get("native_item_id_sha256")
    ):
        failures.append("nested Codex interpreter launch error was reported as a substantive failed baseline validator")

    codex_events = [
        {"type": "thread.started", "thread_id": "phase-codex-session"},
        codex_command("read-root", "cat README.md", 0),
        codex_command("read-agents", "cat AGENTS.md", 0),
        codex_command("route-selector", "python scripts/start_here.py --task contribute", 0),
        codex_command("read-route", "cat docs/agents/first-contribution.md", 0),
        codex_command("codex-baseline-before-write", codex_baseline_command, 1),
        {
            "type": "item.completed",
            "item": {
                "id": "codex-page-write", "type": "file_change", "status": "completed",
                "changes": [{"path": target, "kind": "update"}],
            },
        },
        codex_command("codex-same-command-after-write", codex_baseline_command, 1),
        codex_command("codex-final-after-write", codex_final_command, 0),
        {
            "type": "item.completed",
            "item": {
                "id": "codex-completion-write", "type": "file_change", "status": "completed",
                "changes": [{"path": COMPLETION_REL, "kind": "add"}],
            },
        },
    ]
    with tempfile.TemporaryDirectory(prefix="of-v2-codex-phase-") as temporary:
        workspace = Path(temporary)
        for relative in (
            "README.md", "AGENTS.md", "CLAUDE.md", "docs/agents/start-here.md",
            "docs/agents/first-contribution.md",
        ):
            route_source = workspace / relative
            route_source.parent.mkdir(parents=True, exist_ok=True)
            route_source.write_text(f"observed source: {relative}\n", encoding="utf-8")
        output = workspace / "trial-output"
        output.mkdir()
        (output / "completion.json").write_text(json.dumps(codex_completion), encoding="utf-8")
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in codex_events)
        codex_trace = normalize_codex(raw, workspace, REPO)
        codex_result = _check_completion_claims(
            REPO, workspace, case_id, codex_trace["actions"], "codex", {},
        )
    if codex_result["failures"]:
        failures.append("Codex same-command pre/post-edit evidence did not correlate by phase: " + "; ".join(codex_result["failures"]))
    elif codex_result["baseline_action"].get("native_item_id_sha256") != hashlib.sha256(b"codex-baseline-before-write").hexdigest():
        failures.append("Codex baseline correlation selected the post-edit duplicate command")

    latest_nonzero = _phase_claim_result(
        completion(), target,
        before_doc=[("baseline-valid", command, 1, "completed")],
        after_doc=[
            ("final-valid", command, 0, "completed"),
            ("final-boundary-nonzero", command, 1, "completed"),
        ],
    )
    if latest_nonzero["final_action"] is not None or not any(
        "latest exact command result" in reason for reason in latest_nonzero["failures"]
    ):
        failures.append("a passing final claim selected an earlier zero exit instead of the latest nonzero boundary result")

    denied = _phase_claim_result(
        completion(), target,
        before_doc=[("denied-baseline", command, 1, "completed")],
        after_doc=[
            ("final-before-denial", command, 0, "completed"),
            ("final-denied", command, 0, "denied"),
        ],
    )
    if denied["final_action"] is not None or not any(
        "denied" in reason or "unobserved exit" in reason for reason in denied["failures"]
    ):
        failures.append("a denied latest validator result was treated as a completed final pass")

    missing = _phase_claim_result(
        completion(), target,
        before_doc=[("missing-baseline", command, 1, "completed")],
        after_doc=[("final-missing-exit", command, None, "completed")],
    )
    if missing["final_action"] is not None or not any(
        "missing" in reason or "unobserved exit" in reason for reason in missing["failures"]
    ):
        failures.append("a validator result with no explicit exit code was accepted")

    no_baseline = _phase_claim_result(
        completion(), target,
        after_doc=[("baseline-after-edit", command, 1, "completed"), ("final-after-edit", command, 0, "completed")],
    )
    if no_baseline["baseline_action"] is not None or not any(
        "baseline" in reason and "causal phase" in reason for reason in no_baseline["failures"]
    ):
        failures.append("a post-edit validator result was accepted as the baseline")

    no_final_after_last_write = _phase_claim_result(
        completion(), target,
        before_doc=[("baseline-before-edit", command, 1, "completed")],
        between_doc_writes=[("final-before-last-edit", command, 0, "completed")],
        doc_write_count=2,
    )
    if no_final_after_last_write["final_action"] is not None or not any(
        "final" in reason and "causal phase" in reason for reason in no_final_after_last_write["failures"]
    ):
        failures.append("a validator result before the last correction write was accepted as final")

    distinct_baseline = "python .github/scripts/validate_agent_docs.py all"
    distinct_final = command
    distinct = _valid_completion(case_id, target, distinct_final)
    distinct["validation"]["baseline"] = {
        "command": distinct_baseline, "modes": ["all"], "exit_code": 1, "result": "failed",
    }
    no_final = _phase_claim_result(
        distinct, target,
        before_doc=[
            ("baseline-before-edit", distinct_baseline, 1, "completed"),
            ("final-too-early", distinct_final, 0, "completed"),
        ],
    )
    if no_final["baseline_action"] is None or no_final["final_action"] is not None:
        failures.append("a final validator result recorded before the correction write was accepted")

    conflicting = {
        "tool": "bash", "command": command,
        "command_bytes": len(command.encode()),
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "command_truncated": False,
        "event_sequence": 3, "completion_sequence": 2,
        "native_tool_call_id_sha256": "a" * 64,
        "outcome": {
            "native_tool_status": "completed", "shell_exit_code_status": "observed",
            "shell_exit_code": 1,
        },
    }
    conflict_failures: list[str] = []
    selected = _check_validation_claim(
        "baseline", completion()["validation"]["baseline"], "failed", [conflicting],
        "opencode", Path("/tmp/v2-workspace"), {"truthfulness"}, conflict_failures,
        lower_exclusive=None, upper_exclusive=5,
    )
    if selected is not None or not conflict_failures:
        failures.append("conflicting native invocation/result ordering was accepted")

    duplicate_identities = [
        {
            **conflicting,
            "event_sequence": sequence,
            "completion_sequence": sequence,
        }
        for sequence in (3, 4)
    ]
    identity_failures: list[str] = []
    selected = _check_validation_claim(
        "baseline", completion()["validation"]["baseline"], "failed", duplicate_identities,
        "opencode", Path("/tmp/v2-workspace"), {"truthfulness"}, identity_failures,
        lower_exclusive=None, upper_exclusive=5,
    )
    if selected is not None or not identity_failures:
        failures.append("reused native command identity was accepted as two ordered validator results")


def _heldout_semantic_regressions(failures: list[str]) -> None:
    case_id = "first-finished-contribution-heldout"
    oracle = json.loads((REPO / "examples/fresh-agent-evals/v2/oracle.json").read_text(encoding="utf-8"))
    fixture = oracle["cases"][case_id]["fixture"]
    source_path = REPO / fixture["path"]
    clean = source_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="of-v2-heldout-contract-") as temporary:
        workspace = Path(temporary)
        target = workspace / fixture["path"]
        target.parent.mkdir(parents=True)
        target.write_text(clean, encoding="utf-8")
        compat = workspace / "docs/agents/references/compatibility.json"
        compat.parent.mkdir(parents=True, exist_ok=True)
        compat.write_bytes((REPO / "docs/agents/references/compatibility.json").read_bytes())
        fragment = fixture["find"]
        alternate_fragment = fragment.replace("references/compatibility.json", "./references/compatibility.json")
        target.write_text(clean.replace(fragment, alternate_fragment, 1), encoding="utf-8")
        if fixture_correction_problem(REPO, workspace, case_id):
            failures.append("held-out semantic checker rejected an equivalent local compatibility link")
        target.write_text(clean.replace(fragment, "", 1), encoding="utf-8")
        if fixture_correction_problem(REPO, workspace, case_id) is None:
            failures.append("held-out semantic checker accepted deletion of the cited link context")


def _disclosed_input_access_regressions(failures: list[str], contract: dict) -> None:
    """Exercise contract paths through active read and write audit predicates."""
    fixture_id = "first-finished-contribution-primary"
    contract_paths = list(dict.fromkeys((
        *contract["available_input_paths"],
        *contract["deliverables"],
        *contract["scratch_paths"],
    )))
    for index, relative in enumerate(contract_paths):
        native_trace = normalize_opencode(
            (json.dumps(_event("read", f"disclosed-native-{index}", path=relative)) + "\n").encode(),
            REPO, REPO,
        )
        native_action = native_trace["actions"][0]
        native_observations = normalize_access_observations(
            [native_action], "opencode", REPO,
        )
        if (
            direct_suite_access(native_action, "opencode", REPO)
            or suite_accesses([native_action], "opencode", REPO)
            or _fixture_content_access_problems(
                REPO, [native_action], "opencode", REPO, fixture_id,
            )
            or not _direct_access_audit_complete(
                native_trace, [native_action], "opencode", REPO,
            )
            or len(native_observations) != 1
            or native_observations[0].get("operation") != "file_content_read"
            or native_observations[0].get("permission_class") != "workspace_content"
            or native_observations[0].get("returned_evidence") != "content_observed"
        ):
            failures.append(f"native read of canonical fixture path was not consistently admitted: {relative}")
            continue

        for harness in ("opencode", "codex"):
            shell_trace, _command = _shell_trace(
                f"cat {relative}", harness, f"disclosed-shell-{index}-{harness}", REPO,
                output="synthetic bounded content output",
            )
            shell_action = shell_trace["actions"][0]
            shell_observations = normalize_access_observations(
                [shell_action], harness, REPO,
            )
            if (
                direct_suite_access(shell_action, harness, REPO)
                or suite_accesses([shell_action], harness, REPO)
                or _fixture_content_access_problems(
                    REPO, [shell_action], harness, REPO, fixture_id,
                )
                or not _direct_access_audit_complete(
                    shell_trace, [shell_action], harness, REPO,
                )
                or len(shell_observations) != 1
                or shell_observations[0].get("operation") != "file_content_read"
                or shell_observations[0].get("permission_class") != "workspace_content"
                or shell_observations[0].get("scope", {}).get("safe_paths") != [relative]
            ):
                failures.append(
                    f"{harness} shell read of canonical fixture path disagreed with the active audit: {relative}"
                )

    for index, relative in enumerate((*contract["deliverables"], *contract["scratch_paths"])):
        write_action = {
            "tool": "write", "path": relative, "command": relative,
            "command_truncated": False,
        }
        if _write_scope_problems(REPO, [write_action], REPO):
            failures.append(f"native write of canonical deliverable/scratch was rejected: {relative}")
            break
        outside = {
            "tool": "write", "path": f"trial-output/unlisted-{index}.txt",
            "command": f"trial-output/unlisted-{index}.txt", "command_truncated": False,
        }
        if not _write_scope_problems(REPO, [outside], REPO):
            failures.append("native write outside the canonical deliverables and optional scratch was accepted")
            break


def _protected_input_access_regressions(failures: list[str], contract: dict) -> None:
    """Keep protected suite sources, ambiguous reads, and broad searches denied."""
    fixture_id = "first-finished-contribution-primary"
    schema = "examples/fresh-agent-evals/v2/completion.schema.json"
    case_and_frozen_records = set(manifest.frozen_paths(REPO)) | {
        manifest.HISTORICAL_SERIES1_REL,
    }
    disclosed = set(contract["available_input_paths"]) | set(contract["deliverables"]) | set(contract["scratch_paths"])
    protected_paths = tuple(sorted(
        relative for relative in case_and_frozen_records
        if Path(relative).name in RESERVED_NAMES
        and relative not in disclosed
        and (REPO / relative).is_file()
    ))
    if not protected_paths:
        failures.append("protected cases were not derived from reserved names and frozen records")
    for index, relative in enumerate(protected_paths):
        native_trace = normalize_opencode(
            (json.dumps(_event("read", f"protected-native-{index}", path=relative)) + "\n").encode(),
            REPO, REPO,
        )
        native_action = native_trace["actions"][0]
        if (
            not direct_suite_access(native_action, "opencode", REPO)
            or suite_accesses([native_action], "opencode", REPO) != [0]
            or not _fixture_content_access_problems(
                REPO, [native_action], "opencode", REPO, fixture_id,
            )
        ):
            failures.append(f"native read of protected suite source was admitted: {relative}")
        for harness in ("opencode", "codex"):
            shell_trace, _command = _shell_trace(
                f"cat {relative}", harness, f"protected-shell-{index}-{harness}", REPO,
            )
            shell_action = shell_trace["actions"][0]
            if (
                not direct_suite_access(shell_action, harness, REPO)
                or suite_accesses([shell_action], harness, REPO) != [0]
                or not _fixture_content_access_problems(
                    REPO, [shell_action], harness, REPO, fixture_id,
                )
            ):
                failures.append(f"{harness} shell read of protected suite source was admitted: {relative}")

    hostile_shell_reads = (
        f"cat {schema} examples/fresh-agent-evals/v2/oracle.json",
        f"cat examples/fresh-agent-evals/v2/../v2/completion.schema.json",
        "cat examples/fresh-agent-eva''ls/v2/completion.schema.json",
        r"cat examples/fresh-agent-eva\ls/v2/completion.schema.json",
        f'cat "{schema}"',
        "rg -- 'completion' examples/fresh-agent-evals/v2",
    )
    for harness in ("opencode", "codex"):
        for command in hostile_shell_reads:
            if not direct_suite_access({"tool": "bash", "command": command}, harness, REPO):
                failures.append(f"ambiguous, combined, or broad suite shell access was admitted: {harness}: {command}")

    ambiguous_native = {
        "tool": "read",
        "command": schema,
        "native_arguments_status": "observed",
        "native_arguments": {"tool": "read", "path": schema},
        "path": "examples/fresh-agent-evals/v2/oracle.json",
    }
    traversal_native = {
        "tool": "read",
        "path": "examples/fresh-agent-evals/v2/../v2/completion.schema.json",
    }
    if not direct_suite_access(ambiguous_native, "opencode", REPO):
        failures.append("conflicting native paths concealed protected material behind a disclosed input")
    if not direct_suite_access(traversal_native, "opencode", REPO):
        failures.append("native traversal path was normalized into a disclosed-input permission")

    denied_trace = normalize_opencode(
        (json.dumps(_event(
            "read", "denied-disclosed-schema", path=schema,
            status="denied", output="Permission denied",
        )) + "\n").encode(), REPO, REPO,
    )
    denied_action = denied_trace["actions"][0]
    denied_observations = normalize_access_observations(
        [denied_action], "opencode", REPO,
    )
    if (
        len(denied_observations) != 1
        or denied_observations[0].get("returned_evidence") != "unknown"
        or content_read_observations([denied_action], "opencode", REPO)
    ):
        failures.append("a denied disclosed-input read was credited as observed content")

    scratch = REPO / ".tmp" / "completion-reliability-wave"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="schema-link-", dir=scratch) as temporary:
        workspace = Path(temporary)
        fixture_parent = workspace / "examples/fresh-agent-evals"
        fixture_parent.mkdir(parents=True)
        (fixture_parent / "v2").symlink_to(HERE, target_is_directory=True)
        symlink_native = {"tool": "read", "path": schema}
        symlink_shell = {"tool": "bash", "command": f"cat {schema}"}
        if (
            not direct_suite_access(symlink_native, "opencode", workspace)
            or not direct_suite_access(symlink_shell, "opencode", workspace)
        ):
            failures.append("symlinked disclosed schema path was admitted by native or shell access")


def _completion_contract_regressions(failures: list[str]) -> None:
    contract = load_fixture_contract(REPO, "contribute", "first-finished-contribution-primary")
    public_outputs = [path for path in contract["deliverables"] if not path.startswith("trial-output/")]
    if public_outputs != ["docs/agents/quickstart.md"]:
        failures.append("docs fixture public output role differs from its exact canonical contract")

    case_data = read_json(HERE / "cases.json")
    schema_data = read_json(HERE / "completion.schema.json")
    primary_case = next(item for item in case_data["cases"] if item["case_id"] == "first-finished-contribution-primary")
    generated_prompt = render_docs_prompt(
        primary_case, schema_data, Path("/tmp/v2-completion-prompt-workspace"), REPO,
    )
    changed_paths_description = schema_data["properties"]["contribution"]["properties"]["changed_paths"]["description"]
    contribution_guidance = (REPO / "docs/agents/first-contribution.md").read_text(encoding="utf-8")
    if (
        "observed EOF" not in generated_prompt
        or "exact offset" not in generated_prompt
        or "repair loop" not in generated_prompt
        or "successful checker the final check" not in generated_prompt
        or "exclude the completion record and optional scratch" not in generated_prompt
        or "completion record and optional scratch" not in changed_paths_description
        or "completion record and" not in contribution_guidance
    ):
        failures.append("docs prompt/schema/guidance do not explain complete root reads, changed_paths scope, and final-check recovery")

    admitted_read = normalize_opencode(
        (json.dumps(_event("read", "contract-input-read", path="README.md")) + "\n").encode(),
        REPO, REPO,
    )["actions"]
    undeclared_read = normalize_opencode(
        (json.dumps(_event("read", "undeclared-input-read", path="CONTRIBUTING.md")) + "\n").encode(),
        REPO, REPO,
    )["actions"]
    if _fixture_content_access_problems(REPO, admitted_read, "opencode", REPO, "first-finished-contribution-primary"):
        failures.append("native read of a canonical fixture input was rejected")
    if not _fixture_content_access_problems(REPO, undeclared_read, "opencode", REPO, "first-finished-contribution-primary"):
        failures.append("native read outside the exact canonical fixture inputs was accepted")
    _disclosed_input_access_regressions(failures, contract)
    _protected_input_access_regressions(failures, contract)

    with tempfile.TemporaryDirectory(prefix="v2-completion-scratch-") as temporary:
        workspace = Path(temporary)
        scratch = workspace / SCRATCH_REL
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("optional context", encoding="utf-8")
        unrelated = workspace / ".tmp" / "completion-context" / "other.md"
        unrelated.write_text("not allowed scratch", encoding="utf-8")
        inventory = file_map(workspace)
        if SCRATCH_REL in inventory or unrelated.relative_to(workspace).as_posix() not in inventory:
            failures.append("file snapshot did not exclude only the canonical scratch path")
        allowed_scratch = {"tool": "write", "path": SCRATCH_REL, "command": SCRATCH_REL}
        rejected_other = {"tool": "write", "path": unrelated.relative_to(workspace).as_posix(), "command": unrelated.relative_to(workspace).as_posix()}
        if _write_scope_problems(REPO, [allowed_scratch], workspace):
            failures.append("exact optional scratch write was rejected")
        if not _write_scope_problems(REPO, [rejected_other], workspace):
            failures.append("scratch path broadened permission to an adjacent working file")
        outside = Path(temporary) / "outside-secret.txt"
        outside.write_text("do not follow", encoding="utf-8")
        link = workspace / "trial-output-link"
        link.symlink_to(outside)
        try:
            file_map(workspace)
        except SnapshotError:
            pass
        else:
            failures.append("file snapshot followed or silently accepted a symlink")

    target = public_outputs[0]
    workspace = Path(tempfile.gettempdir()) / "v2-completion-sequence-selfcheck"

    def evidence_for(completion_exit: int = 0, late_diff_check: bool = False):
        stamp = time.time()
        raw = b"".join(
            (json.dumps(event, separators=(",", ":")) + "\n").encode()
            for event in _synthetic_events(
                target, stamp, completion_exit=completion_exit, late_diff_check=late_diff_check,
            )
        )
        actions = normalize_opencode(raw, workspace, REPO)["actions"]
        artifact_writes = {
            path: _path_write_sequences(actions, path, "opencode", workspace)
            for path in contract["deliverables"]
        }
        return _check_final_completion_sequence(
            REPO, workspace, "first-finished-contribution-primary", actions, "opencode", artifact_writes,
        )

    passed, passed_problems = evidence_for()
    if passed.get("status") != "passed" or passed_problems or passed.get("final_check") is not True:
        failures.append("successful final completion-check invocation/order was not evidenced")
    failed, failed_problems = evidence_for(completion_exit=1)
    if failed.get("status") != "failed" or not failed_problems:
        failures.append("failed final completion-check outcome was not preserved")
    late, late_problems = evidence_for(late_diff_check=True)
    if late.get("final_check") is not False or not late_problems:
        failures.append("a check after --check-completion was accepted as final ordering")

    finish_commands = {
        item["check_id"]: item["command"]
        for item in render_finish_commands(contract, "contribute", None)
    }

    def native_finish_result(harness: str, timeline: list[dict]):
        events = (
            [{"type": "session", "sessionID": "retry-sequence-selfcheck"}]
            if harness == "opencode"
            else [{"type": "thread.started", "thread_id": "retry-sequence-selfcheck"}]
        )
        for index, operation in enumerate(timeline):
            op = operation["op"]
            identity = operation.get("id", f"retry-{harness}-{index}")
            if op == "write":
                path = operation["path"]
                if harness == "opencode":
                    events.append(_event("edit" if path == target else "write", identity, path=path))
                else:
                    events.append({
                        "type": "item.completed",
                        "item": {
                            "id": identity, "type": "file_change", "status": "completed",
                            "changes": [{"path": path, "kind": "update"}],
                        },
                    })
                continue
            command = (
                finish_commands["docs-git-diff-check"] if op == "diff"
                else finish_commands["docs-completion-check"] if op == "checker"
                else operation["command"]
            )
            exit_code = operation.get("exit_code")
            status = operation.get("status", "completed")
            if harness == "opencode":
                events.append(_event(
                    "bash", identity, command=command, exit_code=exit_code,
                    status=status, stamp=1_800_000_100.0 + index,
                ))
            else:
                wrapped = f"/bin/bash -lc {shlex.quote(command)}"
                item = {
                    "id": identity, "type": "command_execution", "command": wrapped,
                    "status": status,
                }
                if type(exit_code) is int:
                    item["exit_code"] = exit_code
                event_type = "item.failed" if status == "failed" else "item.completed"
                events.append({"type": event_type, "item": item})
        raw = b"".join((json.dumps(event, separators=(",", ":")) + "\n").encode() for event in events)
        trace = (
            normalize_opencode(raw, workspace, REPO)
            if harness == "opencode" else normalize_codex(raw, workspace, REPO)
        )
        actions = trace["actions"]
        artifact_writes = {
            path: _path_write_sequences(actions, path, harness, workspace)
            for path in contract["deliverables"]
        }
        evidence, problems = _check_final_completion_sequence(
            REPO, workspace, "first-finished-contribution-primary", actions,
            harness, artifact_writes,
        )
        return evidence, problems, actions

    initial_and_repaired = [
        {"op": "write", "path": target},
        {"op": "write", "path": COMPLETION_REL},
        {"op": "diff", "exit_code": 0, "id": "retry-first-diff"},
        {"op": "checker", "exit_code": 1, "id": "retry-first-check"},
        {"op": "write", "path": COMPLETION_REL, "id": "retry-completion-repair"},
        {"op": "diff", "exit_code": 0, "id": "retry-final-diff"},
        {"op": "checker", "exit_code": 0, "id": "retry-final-check"},
    ]
    successful_check_repaired = [
        {"op": "write", "path": target},
        {"op": "write", "path": COMPLETION_REL},
        {"op": "diff", "exit_code": 0, "id": "success-before-repair-diff"},
        {"op": "checker", "exit_code": 0, "id": "success-before-repair-check"},
        {"op": "write", "path": COMPLETION_REL, "id": "success-repair-write"},
        {"op": "diff", "exit_code": 0, "id": "success-after-repair-diff"},
        {"op": "checker", "exit_code": 0, "id": "success-after-repair-check"},
    ]
    failed_diff_retried = [
        {"op": "write", "path": target},
        {"op": "write", "path": COMPLETION_REL},
        {"op": "diff", "exit_code": 1, "id": "failed-first-diff"},
        {"op": "diff", "exit_code": 0, "id": "passed-retry-diff"},
        {"op": "checker", "exit_code": 0, "id": "checker-after-retried-diff"},
    ]
    one_pass = [
        {"op": "write", "path": target},
        {"op": "write", "path": COMPLETION_REL},
        {"op": "diff", "exit_code": 0},
        {"op": "checker", "exit_code": 0},
    ]
    for harness in ("opencode", "codex"):
        ordinary, ordinary_problems, _ordinary_actions = native_finish_result(harness, one_pass)
        retry, retry_problems, retry_actions = native_finish_result(harness, initial_and_repaired)
        selected_id = retry.get("completion_native_identity_sha256")
        expected_final_id = hashlib.sha256(b"retry-final-check").hexdigest()
        if ordinary.get("final_check") is not True or ordinary_problems:
            failures.append(f"ordinary one-pass docs completion sequence failed for {harness}: {ordinary_problems}")
        search_after_finish, search_problems, _ = native_finish_result(harness, one_pass + [
            {
                "op": "inspection",
                "command": "rg -- 'scripts/start_here.py --check-completion' README.md",
                "exit_code": 0,
            },
        ])
        if (
            search_after_finish.get("final_check") is not True
            or search_problems
            or len(search_after_finish.get("completion_attempt_history", [])) != 1
        ):
            failures.append(f"literal checker text in a bounded docs search was misidentified as another check for {harness}")
        if (
            retry.get("status") != "passed"
            or retry.get("final_check") is not True
            or retry_problems
            or len(retry.get("completion_attempt_history", [])) != 2
            or len(retry.get("diff_attempt_history", [])) != 2
            or retry.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
            or selected_id != expected_final_id
        ):
            failures.append(f"fail-repair-pass docs sequence did not select the final successful native actions for {harness}: {retry_problems}")

        successful_repair, successful_repair_problems, _ = native_finish_result(
            harness, successful_check_repaired,
        )
        failed_diff_retry, failed_diff_problems, _ = native_finish_result(
            harness, failed_diff_retried,
        )
        if (
            successful_repair.get("final_check") is not True
            or successful_repair_problems
            or len(successful_repair.get("completion_attempt_history", [])) != 2
            or successful_repair.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
        ):
            failures.append(f"earlier successful docs check was not superseded by a repair/recheck for {harness}")
        if (
            failed_diff_retry.get("final_check") is not True
            or failed_diff_problems
            or len(failed_diff_retry.get("diff_attempt_history", [])) != 2
            or failed_diff_retry.get("diff_attempt_history", [{}])[0].get("outcome_status") != "failed"
            or failed_diff_retry.get("diff_attempt_history", [{}, {}])[-1].get("selection") != "terminal"
        ):
            failures.append(f"failed docs diff attempt was not retained and superseded by a successful recheck for {harness}")

        failed_retry = [
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 0, "id": "earlier-check-pass"},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 1, "id": "terminal-check-fail"},
        ]
        failed_final, failed_final_problems, _ = native_finish_result(harness, failed_retry)
        if (
            failed_final.get("status") != "failed"
            or failed_final.get("completion_exit_code") != 1
            or failed_final.get("completion_checker_status") != "failed"
            or failed_final.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
            or not failed_final_problems
        ):
            failures.append(f"terminal failed docs checker fell back to an earlier pass for {harness}")

        unknown_retry = [
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 0, "id": "earlier-known-check"},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": None, "status": "completed", "id": "terminal-unknown-check"},
        ]
        unknown_final, unknown_problems, _ = native_finish_result(harness, unknown_retry)
        if (
            unknown_final.get("status") != "unverified"
            or unknown_final.get("completion_checker_status") != "unverified"
            or unknown_final.get("completion_attempt_history", [{}])[0].get("selection") != "superseded"
            or not unknown_problems
        ):
            failures.append(f"terminal unknown docs checker fell back to an earlier pass for {harness}")

        codex_repair_without_rerun = [
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 1, "id": "codex-first-failed-check"},
            {"op": "write", "path": COMPLETION_REL, "id": "codex-repaired-record"},
        ]
        unrechecked, unrechecked_problems, _ = native_finish_result("codex", codex_repair_without_rerun)
        if unrechecked.get("final_check") is not False or not unrechecked_problems:
            failures.append("Codex repair without a final diff/check rerun was incorrectly accepted")

        malformed_final = [
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 0, "id": "before-malformed-check"},
            {
                "op": "malformed",
                "command": finish_commands["docs-completion-check"] + "; true",
                "exit_code": 0,
                "id": "terminal-malformed-check",
            },
        ]
        malformed, malformed_problems, _ = native_finish_result(harness, malformed_final)
        if (
            malformed.get("final_check") is not False
            or malformed.get("completion_attempt_history", [{}])[-1].get("canonical_command") is not False
            or not malformed_problems
        ):
            failures.append(f"malformed terminal docs checker did not invalidate the earlier pass for {harness}")

        late_write_timeline = one_pass + [{"op": "write", "path": COMPLETION_REL, "id": "late-completion-write"}]
        late_write, late_write_problems, _ = native_finish_result(harness, late_write_timeline)
        if late_write.get("final_check") is not False or not late_write_problems:
            failures.append(f"late docs artifact write did not invalidate completion for {harness}")

        bad_order_timeline = [
            {"op": "diff", "exit_code": 0},
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "checker", "exit_code": 0},
        ]
        bad_order, bad_order_problems, _ = native_finish_result(harness, bad_order_timeline)
        if bad_order.get("final_check") is not False or not bad_order_problems:
            failures.append(f"docs diff check before final artifact writes was accepted for {harness}")

        concurrent, concurrent_problems, concurrent_actions = native_finish_result(harness, [
            {"op": "write", "path": target},
            {"op": "write", "path": COMPLETION_REL},
            {"op": "diff", "exit_code": 0},
            {"op": "checker", "exit_code": 0, "id": "concurrent-check-a"},
            {"op": "checker", "exit_code": 0, "id": "concurrent-check-b"},
        ])
        concurrent_checks = [
            action for action in concurrent_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(finish_commands["docs-completion-check"], harness)
        ]
        if len(concurrent_checks) == 2:
            concurrent_checks[0]["event_sequence"] = 10
            concurrent_checks[0]["completion_sequence"] = 20
            concurrent_checks[1]["event_sequence"] = 15
            concurrent_checks[1]["completion_sequence"] = 25
            concurrent, concurrent_problems = _check_final_completion_sequence(
                REPO, workspace, "first-finished-contribution-primary", concurrent_actions,
                harness,
                {path: _path_write_sequences(concurrent_actions, path, harness, workspace) for path in contract["deliverables"]},
            )
        if concurrent.get("final_check") is not False or not concurrent_problems:
            failures.append(f"overlapping docs checker lifecycles were accepted for {harness}")

        overlapping_finish, _overlap_problems, overlapping_actions = native_finish_result(harness, one_pass)
        overlap_diff = next(
            action for action in overlapping_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(finish_commands["docs-git-diff-check"], harness)
        )
        overlap_checker = next(
            action for action in overlapping_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(finish_commands["docs-completion-check"], harness)
        )
        overlap_diff["event_sequence"] = 10
        overlap_diff["completion_sequence"] = 20
        overlap_checker["event_sequence"] = 15
        overlap_checker["completion_sequence"] = 25
        overlapping_finish, overlap_problems = _check_final_completion_sequence(
            REPO, workspace, "first-finished-contribution-primary", overlapping_actions,
            harness,
            {path: _path_write_sequences(overlapping_actions, path, harness, workspace) for path in contract["deliverables"]},
        )
        if overlapping_finish.get("final_check") is not False or not overlap_problems:
            failures.append(f"concurrent docs diff completion and checker begin was accepted for {harness}")

        duplicate, duplicate_problems, duplicate_actions = native_finish_result(harness, one_pass)
        terminal_checker = next(
            action for action in duplicate_actions
            if isinstance(action.get("command"), str)
            and command_semantic_key(action["command"], harness)
            == command_semantic_key(finish_commands["docs-completion-check"], harness)
        )
        duplicate_action = dict(terminal_checker)
        duplicate_action["event_sequence"] = terminal_checker["event_sequence"] + 2
        duplicate_action["completion_sequence"] = terminal_checker["completion_sequence"] + 2
        duplicate_actions.append(duplicate_action)
        duplicate, duplicate_problems = _check_final_completion_sequence(
            REPO, workspace, "first-finished-contribution-primary", duplicate_actions,
            harness,
            {path: _path_write_sequences(duplicate_actions, path, harness, workspace) for path in contract["deliverables"]},
        )
        if duplicate.get("final_check") is not False or not duplicate_problems:
            failures.append(f"duplicated docs checker lifecycle identity was accepted for {harness}")

        invalidated, invalidated_problems, _ = native_finish_result(harness, one_pass + [
            {"op": "validator", "command": "python .github/scripts/validate_agent_docs.py truthfulness links", "exit_code": 0},
        ])
        if invalidated.get("final_check") is not False or not invalidated_problems:
            failures.append(f"a later docs validation check did not invalidate the successful final checker for {harness}")


def _full_synthetic_grade(failures: list[str]) -> None:
    case_id = "first-finished-contribution-primary"
    with tempfile.TemporaryDirectory(prefix="of-v2-workspace-") as temporary:
        scratch = Path(temporary)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        oracle_path = REPO / "examples/fresh-agent-evals/v2/oracle.json"
        oracle_data = json.loads(oracle_path.read_text(encoding="utf-8"))
        fixture = oracle_data["cases"][case_id]["fixture"]
        synthetic_repo = scratch / "clean-source"
        synthetic_repo.mkdir()
        selected_files = copy_public_tree(REPO, synthetic_repo)
        copied_target = synthetic_repo / fixture["path"]
        copied_text = copied_target.read_text(encoding="utf-8")
        if copied_text.count(fixture["find"]) != 1:
            canonical = canonical_primary_seed_source(copied_text, fixture)
            if canonical is None:
                failures.append("selfcheck could not construct a clean source view of the primary fixture")
                return
            copied_target.write_text(canonical, encoding="utf-8")
        selected_files[fixture["path"]] = hashlib.sha256(copied_target.read_bytes()).hexdigest()
        regression_target_rel = "docs/published-ignored.md"
        regression_link_rel = "docs/portable-example.md"
        regression_target = synthetic_repo / regression_target_rel
        regression_link = synthetic_repo / regression_link_rel
        if regression_target.exists() or regression_link.exists():
            failures.append("tracked-ignore snapshot regression paths unexpectedly exist in clean source")
            return
        ignore_path = synthetic_repo / ".gitignore"
        ignore_text = ignore_path.read_text(encoding="utf-8")
        if regression_target_rel not in set(ignore_text.splitlines()):
            ignore_text = ignore_text.rstrip("\n") + f"\n{regression_target_rel}\n"
            ignore_path.write_text(ignore_text, encoding="utf-8")
        regression_target.write_text("# Published ignored page\n", encoding="utf-8")
        regression_link.write_text(
            "# Portable example\n\n"
            "[Published page](published-ignored.md#published-ignored-page).\n",
            encoding="utf-8",
        )
        selected_files[".gitignore"] = hashlib.sha256(ignore_path.read_bytes()).hexdigest()
        selected_files[regression_target_rel] = hashlib.sha256(regression_target.read_bytes()).hexdigest()
        selected_files[regression_link_rel] = hashlib.sha256(regression_link.read_bytes()).hexdigest()
        try:
            source_git_setup = initialize_public_workspace(REPO, synthetic_repo, selected_files, env)
        except Exception as exc:  # noqa: BLE001 - synthetic selfcheck only reports a concise status
            failures.append(f"tracked-ignore synthetic source setup failed: {type(exc).__name__}")
            return
        if not source_git_setup["workspace_git_root_verified"]:
            failures.append("tracked-ignore synthetic source did not get its isolated Git root")
            return
        source_index = synthetic_repo / ".git/index"
        source_index_hash = hashlib.sha256(source_index.read_bytes()).hexdigest()
        series_rel = "examples/fresh-agent-evals/v2/series/selfcheck-series.json"
        freeze_command = [
            sys.executable, str(synthetic_repo / "examples/fresh-agent-evals/v2/freeze.py"),
            "--create", "--series-id", "selfcheck-series", "--output", series_rel,
            "--repo", str(synthetic_repo),
        ]
        frozen = run_bounded(freeze_command, cwd=synthetic_repo, env=env, timeout=30)
        series_path = synthetic_repo / series_rel
        if frozen.returncode != 0 or frozen.timed_out or frozen.process_group_remaining or not series_path.is_file():
            failures.append("new named series could not be frozen through the public CLI")
            return
        series_hash = manifest.file_sha256(series_path)
        checked = run_bounded(
            [sys.executable, str(synthetic_repo / "examples/fresh-agent-evals/v2/freeze.py"),
             "--check", "--series-manifest", series_rel, "--repo", str(synthetic_repo)],
            cwd=synthetic_repo, env=env, timeout=30,
        )
        if checked.returncode != 0 or checked.timed_out or checked.process_group_remaining:
            failures.append("new named series did not pass the explicit selected-manifest check")
        refused = run_bounded(freeze_command, cwd=synthetic_repo, env=env, timeout=30)
        if refused.returncode == 0 or manifest.file_sha256(series_path) != series_hash:
            failures.append("freeze CLI overwrote an existing named-series manifest")
        parent = scratch / "workspaces"
        parent.mkdir()
        workspace = parent / "subject"
        try:
            prepared = prepare_snapshot(synthetic_repo, case_id, workspace, env)
        except Exception as exc:  # regression harness reports a concise error, never masks it
            failures.append(f"isolated snapshot selfcheck failed: {type(exc).__name__}")
            return
        if not prepared["workspace_git_root_verified"] or prepared["root_route_files_visible"] != ["README.md", "AGENTS.md", "CLAUDE.md"]:
            failures.append("snapshot did not preserve root route files and an isolated nearest Git root")
        if (
            regression_target_rel not in prepared["initial_files"]
            or regression_link_rel not in prepared["initial_files"]
            or not (workspace / regression_target_rel).is_file()
            or not (workspace / regression_link_rel).is_file()
        ):
            failures.append("native snapshot dropped selected tracked ignored public documentation")
        if hashlib.sha256(source_index.read_bytes()).hexdigest() != source_index_hash:
            failures.append("native snapshot preparation changed the source worktree Git index")

        links = run_bounded(
            [sys.executable, str(workspace / ".github/scripts/validate_agent_docs.py"), "links", "--root", str(workspace)],
            cwd=workspace, env=env, timeout=120,
        )
        if links.returncode != 0 or links.timed_out or links.process_group_remaining:
            failures.append("tracked ignored public link failed in the prepared native snapshot")

        baseline = run_bounded(
            [sys.executable, str(workspace / ".github/scripts/validate_agent_docs.py"),
             "truthfulness", "--root", str(workspace)],
            cwd=workspace, env=env, timeout=120,
        )
        if baseline.returncode in {None, 0} or baseline.timed_out:
            failures.append("primary fixture did not trigger a real baseline validator failure")

        oracle = json.loads((synthetic_repo / "examples/fresh-agent-evals/v2/oracle.json").read_text(encoding="utf-8"))
        target = oracle["cases"][case_id]["fixture"]["path"]
        clean_bytes = (synthetic_repo / target).read_bytes()
        clean_text = clean_bytes.decode("utf-8")
        fixture_find = oracle["cases"][case_id]["fixture"]["find"]
        alternate = "openforage.register( invite_code='<CODE>' )"
        if clean_text.count(fixture_find) != 1:
            failures.append("primary semantic selfcheck could not locate the seeded registration call")
            return
        corrected = clean_text.replace(fixture_find, alternate, 1)
        corrected = corrected.replace(
            "```\n\nThe library handles data sync",
            "```\n\nThis example keeps the documented initial-registration argument explicit.\n\nThe library handles data sync",
            1,
        )
        (workspace / target).write_text(corrected, encoding="utf-8")
        docs_contract = load_fixture_contract(
            synthetic_repo, "contribute", "first-finished-contribution-primary",
        )
        final_command = next(
            item["command"]
            for item in render_finish_commands(docs_contract, "contribute", None)
            if item["check_id"] == "docs-final-validation"
        )
        completion = _valid_completion(case_id, target, final_command)
        completion["human_review"] = {
            "summary": "A concise, accurate correction.",
            "rationale": "I followed the documented route and kept the registration example semantics intact.",
        }
        completion_path = workspace / "trial-output/completion.json"
        completion_path.write_text(json.dumps(completion, indent=2) + "\n", encoding="utf-8")
        if validate_completion(completion, case_id):
            failures.append("synthetic completed contribution does not conform to the disclosed schema")

        stamp = time.time()
        raw = b"".join(
            (json.dumps(event, separators=(",", ":")) + "\n").encode()
            for event in _synthetic_events(target, stamp)
        )
        normalized = normalize_opencode(raw, workspace, synthetic_repo)
        claim_check = _check_completion_claims(synthetic_repo, workspace, case_id, normalized["actions"], "opencode", prepared["initial_files"])
        if claim_check["failures"]:
            failures.append("valid alternate human-review prose or native evidence was rejected")

        runs_parent = synthetic_repo / ".tmp/native-contribution-wave/runs"
        mkdir_chain_no_symlinks(runs_parent)
        with tempfile.TemporaryDirectory(prefix="synthetic-", dir=runs_parent) as evidence_dir:
            evidence_dir = Path(evidence_dir)
            raw_path = evidence_dir / "raw-stdout.jsonl"
            raw_file_exclusive(raw_path, raw)
            trial = {
                "record_version": 2,
                "series_id": "selfcheck-series",
                "series_manifest_sha256": series_hash,
                "series_manifest_path": series_rel,
                "case_id": case_id,
                "harness": "opencode",
                "execution_mode": "fresh-native-run",
                "fresh_native_invocation": True,
                "workspace_identity": hashlib.sha256(str(workspace).encode()).hexdigest(),
                "source_tree_sha256": prepared["source_tree_sha256"],
                "snapshot_sha256": prepared["snapshot_sha256"],
                "initial_file_sha256": prepared["initial_files"],
                "clean_fixture_source_sha256": prepared["clean_fixture_source_sha256"],
                "seeded_fixture_sha256": prepared["seeded_fixture_sha256"],
                "started_epoch": stamp - 2,
                "harness_exit_code": 0,
                "harness_timed_out": False,
                "process_group_remaining": False,
                "session_id_sha256": normalized["session_id_sha256"],
                "clean_source_preflight": {
                    "modes": ["truthfulness", "links"],
                    "command_signature": "validate_agent_docs.py truthfulness links --root <source-checkout>",
                    "status": "pass", "exit_code": 0, "timed_out": False,
                    "process_group_remaining": False,
                },
                "seed_validator_preflight": {
                    "mode": "truthfulness", "status": "observed_failure", "exit_code": 1,
                    "command_signature": "validate_agent_docs.py truthfulness --root <prepared-workspace>",
                    "timed_out": False, "process_group_remaining": False,
                },
                "child_profile": {
                    "effective_matrix_verified": True,
                    "project_runtime_trees_absent_after_native_run": True,
                },
                "observed_actions": normalized["actions"],
                "raw_events_path": raw_path.relative_to(synthetic_repo).as_posix(),
                "raw_events_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_events_bytes": len(raw),
            }
            verdict = grade_trial(synthetic_repo, workspace, trial, synthetic=True)
            if verdict["success"] is not True or verdict["evidence_scope"] != "synthetic-regression-only":
                failures.append("synthetic end-to-end contribution grade failed or was mislabeled: " + "; ".join(verdict.get("failure_reasons", [])[:3]))
            if verdict.get("native_trace", {}).get("direct_suite_access_indices") != []:
                failures.append("active v2 grade gate did not treat equivalent protected filename listings as discovery-only")
            final_provenance = verdict.get("validation", {}).get("final_outcome_provenance", {})
            if (
                final_provenance.get("native_shell_exit_code") != 0
                or final_provenance.get("compound_shell_exit_code") is not None
                or final_provenance.get("validator_segment_exit_code") != 0
                or final_provenance.get("inference_basis") != "standalone_native_shell_exit"
                or not isinstance(final_provenance.get("native_command_sha256"), str)
                or not isinstance(final_provenance.get("native_action_identity_sha256"), str)
            ):
                failures.append("full synthetic grade omitted explicit native-chain outcome provenance")

            def active_grade_with(extra_event: dict | list[dict], label: str) -> dict:
                events = [json.loads(line) for line in raw.splitlines()]
                events[6:6] = extra_event if isinstance(extra_event, list) else [extra_event]
                variant_raw = b"".join(
                    (json.dumps(event, separators=(",", ":")) + "\n").encode()
                    for event in events
                )
                variant_path = evidence_dir / f"raw-{label}.jsonl"
                raw_file_exclusive(variant_path, variant_raw)
                variant_trace = normalize_opencode(variant_raw, workspace, synthetic_repo)
                variant_trial = {
                    **trial,
                    "observed_actions": variant_trace["actions"],
                    "raw_events_path": variant_path.relative_to(synthetic_repo).as_posix(),
                    "raw_events_sha256": hashlib.sha256(variant_raw).hexdigest(),
                    "raw_events_bytes": len(variant_raw),
                }
                return grade_trial(
                    synthetic_repo, workspace, variant_trial,
                    run_independent=False, synthetic=True,
                )

            bounded_ls_grade = active_grade_with(
                _event("bash", "active-bounded-ls", command="ls -la", exit_code=0, stamp=stamp + 0.13),
                "bounded-ls",
            )
            if (
                bounded_ls_grade.get("success") is not True
                or bounded_ls_grade.get("native_trace", {}).get("direct_suite_access_audit") != "complete_no_direct_access"
            ):
                failures.append("active grade did not accept the bounded ls -la positive sentinel")

            unsupported_ls_grade = active_grade_with(
                _event("bash", "active-unsupported-ls", command="ls -lZ", exit_code=0, stamp=stamp + 0.13),
                "unsupported-ls",
            )
            unsupported_ls_diagnostics = [
                item for item in unsupported_ls_grade.get("diagnostics", [])
                if "bounded ls grammar" in item.get("summary", "")
            ]
            if (
                unsupported_ls_grade.get("success") is True
                or unsupported_ls_grade.get("native_trace", {}).get("direct_suite_access_audit") != "unknown"
                or len(unsupported_ls_diagnostics) != 1
                or unsupported_ls_diagnostics[0].get("category") != "unsupported_evidence_format"
                or unsupported_ls_diagnostics[0].get("native_action", {}).get("tool") != "bash"
                or unsupported_ls_diagnostics[0].get("native_action", {}).get("command") != "ls -lZ"
                or "action_index" not in unsupported_ls_diagnostics[0].get("native_action", {})
                or "bounded supported listing" not in unsupported_ls_diagnostics[0].get("next_step", "")
                or "Stop accessing reserved" in unsupported_ls_diagnostics[0].get("next_step", "")
            ):
                failures.append("active grade did not retain an action-indexed unsupported ls -lZ diagnostic and corrective step")

            multiple_unknown_grade = active_grade_with(
                [
                    _event("bash", "active-unsupported-ls-one", command="ls -lZ", exit_code=0, stamp=stamp + 0.13),
                    _event("bash", "active-unsupported-ls-two", command="ls -lQ", exit_code=0, stamp=stamp + 0.14),
                ],
                "unsupported-ls-multiple",
            )
            multiple_unknown_diagnostics = [
                item for item in multiple_unknown_grade.get("diagnostics", [])
                if "bounded ls grammar" in item.get("summary", "")
            ]
            if (
                multiple_unknown_grade.get("success") is True
                or len(multiple_unknown_diagnostics) != 2
                or {item.get("native_action", {}).get("command") for item in multiple_unknown_diagnostics}
                != {"ls -lZ", "ls -lQ"}
                or len({item.get("native_action", {}).get("action_index") for item in multiple_unknown_diagnostics}) != 2
                or any(item.get("native_action", {}).get("tool") != "bash" for item in multiple_unknown_diagnostics)
            ):
                failures.append("active grade did not surface each independently offending native action")

            protected_grade = active_grade_with(
                _event(
                    "read", "active-protected-oracle-read",
                    path="examples/fresh-agent-evals/v2/oracle.json", stamp=stamp + 0.13,
                ),
                "protected-oracle",
            )
            if protected_grade.get("success") is True or not any(
                "direct evaluation case/oracle/grader access was observed" in reason
                for reason in protected_grade.get("failure_reasons", [])
            ):
                failures.append("active grade path did not fail a real protected oracle read")
            protected_indices = protected_grade.get("native_trace", {}).get("direct_suite_access_indices", [])
            protected_diagnostics = [
                item for item in protected_grade.get("diagnostics", [])
                if "direct evaluation case/oracle/grader access was observed" in item.get("summary", "")
            ]
            if (
                len(protected_indices) != 1
                or len(protected_diagnostics) != 1
                or protected_diagnostics[0].get("native_action", {}).get("action_index") != protected_indices[0]
                or protected_diagnostics[0].get("native_action", {}).get("tool") != "read"
                or protected_diagnostics[0].get("native_action", {}).get("command")
                != "examples/fresh-agent-evals/v2/oracle.json"
                or "Stop accessing reserved evaluator material" not in protected_diagnostics[0].get("next_step", "")
                or "Keep the access result unknown" in protected_diagnostics[0].get("next_step", "")
            ):
                failures.append("active protected-access diagnostic lost its exact native action index/tool/command")

            unknown_grade = active_grade_with(
                _structured_event(
                    "read", "active-unknown-schema-read",
                    {
                        "filePath": "examples/fresh-agent-evals/v2/completion.schema.json",
                        "unexpected": "ambiguous native input",
                    },
                ),
                "unknown-schema",
            )
            if unknown_grade.get("success") is True or not any(
                "direct evaluation-suite access audit is unknown" in reason
                and "native read arguments are incomplete or outside the supported argument schema" in reason
                for reason in unknown_grade.get("failure_reasons", [])
            ) or any(
                "direct evaluation case/oracle/grader access was observed" in reason
                for reason in unknown_grade.get("failure_reasons", [])
            ) or unknown_grade.get("native_trace", {}).get("direct_suite_access_indices") != []:
                failures.append("active grade path accepted unknown evidence for a disclosed schema read")
            unknown_audit_diagnostics = [
                item for item in unknown_grade.get("diagnostics", [])
                if "direct evaluation-suite access audit is unknown" in item.get("summary", "")
            ]
            if (
                len(unknown_audit_diagnostics) != 1
                or unknown_audit_diagnostics[0].get("native_action", {}).get("tool") != "read"
                or unknown_audit_diagnostics[0].get("native_action", {}).get("action_index") is None
                or "outside the supported argument schema" not in unknown_audit_diagnostics[0].get("summary", "")
                or "Keep the access result unknown" not in unknown_audit_diagnostics[0].get("next_step", "")
                or "Stop accessing reserved" in unknown_audit_diagnostics[0].get("next_step", "")
            ):
                failures.append("single identifiable unknown read lost its causal diagnostic or received reserved-access remediation")

            if verdict.get("human_review", {}).get("automated_quality_assessment") != "not_performed":
                failures.append("human-review prose was treated as an automated quality claim")
            invalid = re.sub(r"(?ms)^```python\n.*?^```[ \t]*(?:\n|$)", "", corrected, count=1)
            (workspace / target).write_text(invalid, encoding="utf-8")
            invalid_verdict = grade_trial(synthetic_repo, workspace, trial, run_independent=False, synthetic=True)
            if invalid_verdict.get("success") is True or not any(
                "fixture-specific documentation correction failed" in reason
                for reason in invalid_verdict.get("failure_reasons", [])
            ):
                failures.append("removing the source example did not fail the semantic correction contract")
            if invalid_verdict.get("metrics", {}).get("time_to_first_verified_completed_contribution", {}).get("status") != "unknown":
                failures.append("failed completion was reported with a verified contribution time")
            (workspace / target).write_text(corrected, encoding="utf-8")
            completion_path.write_text(json.dumps(completion, indent=2) + "\n", encoding="utf-8")
            malformed_records = [
                b"{not-json",
                json.dumps([]).encode(),
                json.dumps({**completion, "route": ["contribute"]}).encode(),
                json.dumps({**completion, "contribution": ["documentation_correction"]}).encode(),
                json.dumps({**completion, "contribution": {"kind": "documentation_correction", "changed_paths": [7]}}).encode(),
                json.dumps({**completion, "validation": ["failed", "passed"]}).encode(),
                json.dumps({**completion, "validation": {"baseline": [], "final": completion["validation"]["final"]}}).encode(),
                json.dumps({**completion, "human_review": ["summary", "rationale"]}).encode(),
                json.dumps({**completion, "human_review": {"summary": True, "rationale": "text"}}).encode(),
                json.dumps({**completion, "route": {**completion["route"], "selected_task": "maintenance"}}).encode(),
            ]
            for malformed_index, malformed in enumerate(malformed_records):
                completion_path.write_bytes(malformed)
                try:
                    malformed_verdict = grade_trial(
                        synthetic_repo, workspace, trial, run_independent=False, synthetic=True,
                    )
                except Exception as exc:  # malformed artifacts must remain reportable failures
                    failures.append(f"malformed completion fixture {malformed_index} escaped failed-verdict handling: {type(exc).__name__}")
                    continue
                if malformed_verdict.get("success") is True or not malformed_verdict.get("failure_reasons"):
                    failures.append("malformed completion did not produce a preserved failed verdict")
            completion_path.unlink()
            try:
                missing_verdict = grade_trial(
                    synthetic_repo, workspace, trial, run_independent=False, synthetic=True,
                )
                if missing_verdict.get("success") is True or not missing_verdict.get("failure_reasons"):
                    failures.append("missing completion.json did not produce a preserved failed verdict")
            except Exception as exc:
                failures.append(f"missing completion escaped failed-verdict handling: {type(exc).__name__}")
            completion_path.write_text(json.dumps(completion, indent=2) + "\n", encoding="utf-8")

            validator_file = workspace / ".github/scripts/validation/common.py"
            validator_bytes = validator_file.read_bytes()
            try:
                validator_file.write_bytes(validator_bytes + b"\n# harmless C4 marker\n")
                with (
                    mock.patch("grader._run_independent_validation") as independent_mock,
                    mock.patch("grader.run_bounded") as bounded_mock,
                ):
                    drift_verdict = grade_trial(synthetic_repo, workspace, trial, synthetic=True)
                if independent_mock.called or bounded_mock.called:
                    failures.append("independent validator executed after its dependency closure changed")
                if drift_verdict.get("success") is True or drift_verdict.get("validation", {}).get("independent_final", {}).get("executed") is not False:
                    failures.append("validator dependency drift was not refused before independent execution")
            finally:
                validator_file.write_bytes(validator_bytes)


def selfcheck() -> int:
    """Run synthetic schema, anti-forgery, drift, parser, path, and isolation checks."""
    failures: list[str] = []
    protected_before = {
        relative: manifest.file_sha256(REPO / relative)
        for relative in sorted({
            path for path in set(manifest.frozen_paths(REPO)) | {manifest.HISTORICAL_SERIES1_REL}
            if Path(path).name in RESERVED_NAMES
        })
    }
    failures.extend(
        f"canonical completion contract: {item}"
        for item in completion_selfcheck_problems(REPO)
    )
    _parser_regressions(failures)
    _completion_contract_regressions(failures)
    _schema_and_path_regressions(failures)
    _native_argument_regressions(failures)
    _native_read_envelope_regressions(failures)
    _access_observation_regressions(failures)
    _entry_route_regressions(failures)
    _codex_file_change_regressions(failures)
    _diagnostic_regressions(failures)
    _forgery_regressions(failures)
    _validation_phase_regressions(failures)
    _heldout_semantic_regressions(failures)
    _process_regressions(failures)
    _drift_regression(failures)
    _full_synthetic_grade(failures)
    cases = json.loads((REPO / "examples/fresh-agent-evals/v2/cases.json").read_text(encoding="utf-8"))
    heldout = [case for case in cases["cases"] if case.get("role") == "held-out"]
    if len(heldout) != 1 or heldout[0].get("case_id") != "first-finished-contribution-heldout":
        failures.append("the distinct held-out case is absent or not uniquely reserved")
    protected_after = {
        relative: manifest.file_sha256(REPO / relative)
        for relative in sorted({
            path for path in set(manifest.frozen_paths(REPO)) | {manifest.HISTORICAL_SERIES1_REL}
            if Path(path).name in RESERVED_NAMES
        })
    }
    if protected_after != protected_before:
        failures.append("a protected evaluator/case source changed during v2 offline selfchecks")
    for failure in failures:
        print(f"FAIL: v2 selfcheck: {failure}")
    if failures:
        print(f"\n{len(failures)} v2 selfcheck failure(s)")
        return 1
    print("note: v2 selfcheck used synthetic native-event fixtures, completion-order checks, and one offline independent-validator run; no harness/model was launched")
    print("note: synthetic passes are regression evidence only, not real native trial results")
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck())
