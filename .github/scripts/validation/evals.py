"""Fresh-agent evaluation suite schema and deterministic grader regression."""

from __future__ import annotations

import json
import re
import ast
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import is_real_string, read_text

EVALS_DIR = "examples/fresh-agent-evals"
TASKS_REL = f"{EVALS_DIR}/tasks.json"
GRADER_REL = f"{EVALS_DIR}/grader.py"
DISCOVERY_CHECKS_REL = f"{EVALS_DIR}/discovery_checks.py"
ENTRY_CHECKS_REL = f"{EVALS_DIR}/discovery_entry_checks.py"
OUTCOME_CHECKS_REL = f"{EVALS_DIR}/outcome_checks.py"
TRACE_EVIDENCE_REL = f"{EVALS_DIR}/trace_evidence.py"
TOOL_OUTCOMES_REL = f"{EVALS_DIR}/tool_outcomes.py"
TOOL_PROFILE_REL = f"{EVALS_DIR}/tool_profile.py"
PROFILE_CONFIG_REL = f"{EVALS_DIR}/trial-inputs/opencode-file-tools-only.json"
RUNNER_REL = f"{EVALS_DIR}/run_trial.py"
TRIAL_TEMPLATE_REL = f"{EVALS_DIR}/trial-record.template.json"
V2_DIR = f"{EVALS_DIR}/v2"
V2_CASES_REL = f"{V2_DIR}/cases.json"
V2_SCHEMA_REL = f"{V2_DIR}/completion.schema.json"
V2_ORACLE_REL = f"{V2_DIR}/oracle.json"
V2_SERIES_REL = f"{V2_DIR}/series.json"
V2_RUNNER_REL = f"{V2_DIR}/runner.py"
V2_FREEZE_REL = f"{V2_DIR}/freeze.py"
V2_SELFTEST_REL = f"{V2_DIR}/selfcheck.py"
V2_SELFTEST_RAN = False
RESEARCH_DIR = f"{EVALS_DIR}/research"
RESEARCH_RUNNER_REL = f"{RESEARCH_DIR}/runner.py"
RESEARCH_SELFTEST_RAN = False
FIXTURE_RUNNER_REL = "examples/opencode-lifecycle/run_fixture.py"
FORBIDDEN_RECORD_KEYS = (
    "raw_output_tail",
    "output_tail",
)
SENSITIVE_RECORD_KEY_RE = re.compile(r"(?:^|_)(?:usage|cost|tokens?|price|credit|balance)(?:_|$)", re.I)
REQUIRED_CASES = {
    "docs-fix",
    "campaign-planning-separation",
    "recovery",
    "challenger-research",
    "discovery-research",
    "discovery-contribution-cold-start",
    "discovery-quickstart-entry",
    "discovery-pasted-entry",
}
DISCOVERY_CASES = {
    "discovery-research",
    "discovery-contribution-cold-start",
    "discovery-quickstart-entry",
    "discovery-pasted-entry",
}
README_DISCOVERY_CASES = {"discovery-research", "discovery-contribution-cold-start"}
FILE_ONLY_DISCOVERY_CASES = {"discovery-quickstart-entry", "discovery-pasted-entry"}
DISCOVERY_ENUM_OPTIONS = {
    "capability_availability.openforage_package": ("available", "unavailable", "unknown"),
    "capability_availability.campaign_access": ("available", "unavailable", "unknown"),
    "capability_availability.scheduler": ("verified", "unverified", "unavailable", "unknown"),
    "capability_availability.separate_session": ("established", "not_established", "unknown"),
    "continuous_search_posture": (
        "blocked_until_separate_session",
        "permitted_only_in_separate_session",
        "main_session_fallback",
    ),
}
SELECTED_TASK_PROMPT_CONTRACT = (
    "selected_task (string)",
    "canonical task identifier",
    "startup helper",
    "canonical onboarding task definitions",
    "not a task title or description",
)
KNOWN_CHECK_KINDS = {
    "text_matches",
    "text_not_matches",
    "json_field_present",
    "json_field_equals",
    "json_field_in",
    "json_field_gt",
    "json_list_min_length",
    "json_list_contains",
    "json_field_in_map",
    "json_field_matches_map_value",
    "json_field_contains",
    "json_field_references_file",
    "file_unchanged",
    "validator_passes",
}
REQUIRED_CASE_KEYS = {
    "case_id",
    "title",
    "objective",
    "user_task",
    "prompt",
    "fixture",
    "deliverables",
    "expected_actions",
    "forbidden_actions",
    "isolation",
}


def check_evals(report, root: Path) -> None:
    tasks_path = root / TASKS_REL
    grader_path = root / GRADER_REL
    if not tasks_path.is_file():
        report.fail(f"fresh-agent evals: missing {TASKS_REL}")
        return
    if not grader_path.is_file():
        report.fail(f"fresh-agent evals: missing {GRADER_REL}")
        return
    if not (root / DISCOVERY_CHECKS_REL).is_file():
        report.fail(f"fresh-agent evals: missing shared discovery checker {DISCOVERY_CHECKS_REL}")
    for helper in (
        ENTRY_CHECKS_REL,
        OUTCOME_CHECKS_REL,
        TRACE_EVIDENCE_REL,
        TOOL_OUTCOMES_REL,
        TOOL_PROFILE_REL,
    ):
        if not (root / helper).is_file():
            report.fail(f"fresh-agent evals: missing focused helper {helper}")
    if not (root / RUNNER_REL).is_file():
        report.fail(f"fresh-agent evals: missing {RUNNER_REL}; the orchestrator runner must exist")

    try:
        suite = json.loads(read_text(tasks_path))
    except json.JSONDecodeError as exc:
        report.fail(f"{TASKS_REL}: invalid JSON: {exc}")
        return

    isolation = suite.get("isolation")
    if not isinstance(isolation, dict):
        report.fail(f"{TASKS_REL}: isolation must be an object")
        return
    for key, expected in (("network", "forbidden"), ("credentials", "forbidden")):
        if isolation.get(key) != expected:
            report.fail(f"{TASKS_REL}: isolation.{key} must be {expected!r}")
    if isolation.get("writes_allowed_within") != "throwaway workspace only":
        report.fail(f"{TASKS_REL}: isolation.writes_allowed_within must scope writes to the throwaway workspace")
    if "file-tools-only" not in str(isolation.get("file_tools_profile_disclosure", "")):
        report.fail(f"{TASKS_REL}: file-only cases must disclose the enforced tool-profile fixture and its limits")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        report.fail(f"{TASKS_REL}: cases must be a non-empty list")
        return
    seen = set()
    for index, case in enumerate(cases):
        where = f"{TASKS_REL}: cases[{index}]"
        if not isinstance(case, dict):
            report.fail(f"{where}: case must be an object")
            continue
        missing = REQUIRED_CASE_KEYS - set(case)
        if missing:
            report.fail(f"{where}: missing keys {sorted(missing)}")
            continue
        case_id = case["case_id"]
        if case_id in seen:
            report.fail(f"{where}: duplicate case_id {case_id!r}")
        seen.add(case_id)
        mode = case.get("mode", "coached")
        if not isinstance(mode, str) or mode not in {"coached", "discovery"}:
            report.fail(f"{where}: mode must be 'coached' or 'discovery'")
        if not is_real_string(case.get("user_task")):
            report.fail(f"{where}: user_task must be a real user-facing task, not a placeholder")
        prompt = case.get("prompt")
        if not is_real_string(prompt):
            report.fail(f"{where}: prompt must be the exact task text handed to the trial")
        elif "case_id" not in prompt:
            report.fail(f"{where}: prompt must state the case_id artifact field, not hide it from the agent")
        fixture = case.get("fixture")
        if not isinstance(fixture, dict):
            report.fail(f"{where}: fixture must be an object")
        else:
            fixture_dir = root / EVALS_DIR / "fixtures" / case_id
            spec_path = fixture_dir / "fixture.json"
            fixture_spec = {}
            if not spec_path.is_file():
                report.fail(f"{where}: missing fixture spec {spec_path.relative_to(root)}")
            else:
                try:
                    fixture_spec = json.loads(read_text(spec_path))
                except json.JSONDecodeError as exc:
                    report.fail(f"{where}: fixture spec is invalid JSON: {exc}")
                    fixture_spec = {}
            for index, item in enumerate(fixture.get("overlay_files", []) or []):
                if not (fixture_dir / item).is_file():
                    report.fail(f"{where}: fixture.overlay_files[{index}] does not exist: {item!r}")
            remove_paths = fixture_spec.get("remove_paths", []) if isinstance(fixture_spec, dict) else []
            if not isinstance(remove_paths, list) or any(not isinstance(item, str) for item in remove_paths):
                report.fail(f"{where}: fixture remove_paths must be a list of relative paths")
            elif any(Path(item).is_absolute() or ".." in Path(item).parts for item in remove_paths):
                report.fail(f"{where}: fixture remove_paths must remain inside the workspace")
            conditions = fixture_spec.get("conditions", []) if isinstance(fixture_spec, dict) else []
            if not isinstance(conditions, list) or any(not isinstance(item, str) for item in conditions):
                report.fail(f"{where}: fixture conditions must be a list of strings")
        deliverables = case.get("deliverables")
        if not isinstance(deliverables, list) or not deliverables:
            report.fail(f"{where}: deliverables must be a non-empty list")
            continue
        if is_real_string(prompt):
            for deliverable in deliverables:
                path = deliverable.get("path") if isinstance(deliverable, dict) else None
                if is_real_string(path) and path not in prompt:
                    report.fail(f"{where}: prompt must name the deliverable path {path!r}")
        expected_actions = case.get("expected_actions")
        if not isinstance(expected_actions, list) or not expected_actions:
            report.fail(f"{where}: expected_actions must be a non-empty list")
        elif not any(isinstance(item, dict) and item.get("command_pattern") for item in expected_actions):
            report.fail(f"{where}: at least one expected action must carry a command_pattern graded against the trace")
        for a_index, action in enumerate(expected_actions or []):
            if not isinstance(action, dict) or "command_contract" not in action:
                continue
            contract = action.get("command_contract")
            a_where = f"{where}.expected_actions[{a_index}].command_contract"
            if not isinstance(contract, dict):
                report.fail(f"{a_where}: must be an object")
            elif (
                set(contract) != {"kind", "task"}
                or contract.get("kind") not in {"start_here_task", "start_here_task_or_read"}
                or not is_real_string(contract.get("task"))
            ):
                report.fail(
                    f"{a_where}: must disclose a supported selector-or-read route contract with a task name"
                )
        for d_index, deliverable in enumerate(deliverables):
            d_where = f"{where}.deliverables[{d_index}]"
            if not isinstance(deliverable, dict) or not is_real_string(deliverable.get("path")):
                report.fail(f"{d_where}: must be an object with a real path")
                continue
            checks = deliverable.get("checks")
            if not isinstance(checks, list) or not checks:
                report.fail(f"{d_where}: checks must be a non-empty list")
                continue
            for c_index, check in enumerate(checks):
                kind = check.get("kind") if isinstance(check, dict) else None
                if kind not in KNOWN_CHECK_KINDS:
                    report.fail(f"{d_where}.checks[{c_index}]: unknown check kind {kind!r}")
        if not case.get("forbidden_actions"):
            report.fail(f"{where}: forbidden_actions must be non-empty")
        isolation_case = case.get("isolation")
        if not isinstance(isolation_case, dict) or not isolation_case.get("allowed_write_paths"):
            report.fail(f"{where}: isolation.allowed_write_paths must be non-empty")

    if seen != REQUIRED_CASES:
        report.fail(
            f"{TASKS_REL}: cases must cover {sorted(REQUIRED_CASES)}; found {sorted(seen)}"
        )

    discovery_cases = [
        case for case in cases
        if isinstance(case, dict) and case.get("mode") == "discovery"
    ]
    if {case.get("case_id") for case in discovery_cases} != DISCOVERY_CASES:
        report.fail(f"{TASKS_REL}: required discovery cases must be present: {sorted(DISCOVERY_CASES)}")
    undisclosed_solution_paths = (
        "scripts/start_here.py",
        "docs/agents/research-opportunities.json",
        "docs/agents/research-campaign.md",
        "docs/agents/first-contribution.md",
        ".github/scripts/validate_agent_docs.py",
    )
    for case in discovery_cases:
        where = f"{TASKS_REL}: discovery case {case.get('case_id')}"
        case_id = case.get("case_id")
        prompt = case.get("prompt", "")
        if case_id in README_DISCOVERY_CASES and "README.md" not in prompt:
            report.fail(f"{where}: prompt must use README.md as the entrypoint")
        if "trial-output/" not in prompt or "case_id" not in prompt:
            report.fail(f"{where}: artifact location and schema must be disclosed")
        if case_id in README_DISCOVERY_CASES and (
            "discoverable in the workspace" not in prompt
            and "discovered from workspace documents" not in prompt
        ):
            report.fail(f"{where}: prompt must make clear that route facts are discovered from workspace sources")
        if case_id in FILE_ONLY_DISCOVERY_CASES:
            if case.get("execution_profile") != "opencode-file-tools-only-v2":
                report.fail(f"{where}: direct/pasted entry case must select the enforced file-tools-only profile")
            if "workspace" not in str(prompt).casefold() or not any(
                phrase in str(prompt).casefold()
                for phrase in ("entry text", "pasted", "directly on", "direct entry")
            ):
                report.fail(f"{where}: prompt must identify a direct or pasted entry and require visible workspace evidence")
            if "use shell/python" not in str(prompt).casefold():
                report.fail(f"{where}: task must disclose that the file-only case does not use shell/Python")
        normalized_prompt = re.sub(r"\s+", " ", str(prompt).casefold())
        if not all(fragment in normalized_prompt for fragment in SELECTED_TASK_PROMPT_CONTRACT):
            report.fail(
                f"{where}: selected_task must request the canonical task identifier discovered from the "
                "startup helper, canonical onboarding task definitions, or readable packet, "
                "not a task title or description"
            )
        for hidden_path in undisclosed_solution_paths:
            if hidden_path in prompt:
                report.fail(f"{where}: prompt gives away an authoritative solution path: {hidden_path}")
        for deliverable in case.get("deliverables", []):
            for check in deliverable.get("checks", []) if isinstance(deliverable, dict) else []:
                field = check.get("field") if isinstance(check, dict) else None
                if isinstance(field, str) and field.split(".", 1)[0] not in prompt:
                    report.fail(f"{where}: output field {field!r} is graded but not disclosed in the prompt")

        if case_id in FILE_ONLY_DISCOVERY_CASES:
            entry_check = case.get("entry_check")
            if not isinstance(entry_check, dict) or entry_check.get("kind") not in {
                "research_entry", "pasted_contribution"
            }:
                report.fail(f"{where}: focused entry rubric must declare a supported entry_check kind")
            fixture_spec = root / EVALS_DIR / "fixtures" / str(case_id) / "fixture.json"
            if fixture_spec.is_file():
                try:
                    fixture_data = json.loads(read_text(fixture_spec))
                except json.JSONDecodeError:
                    fixture_data = {}
                removed = set(fixture_data.get("remove_paths", [])) if isinstance(fixture_data, dict) else set()
                if not {"AGENTS.md", "CLAUDE.md"} <= removed:
                    report.fail(f"{where}: file-only fixture must remove root AGENTS.md and CLAUDE.md")
                conditions = fixture_data.get("conditions", []) if isinstance(fixture_data, dict) else []
                condition_text = " ".join(str(item).casefold() for item in conditions)
                if (
                    not ("visible files" in condition_text and "autoload" in condition_text)
                    or "python executable" not in condition_text
                    or not any(phrase in condition_text for phrase in ("not a claim", "does not claim"))
                ):
                    report.fail(f"{where}: fixture must disclose visible-file and host-Python limits")
                profile_rel = fixture_data.get("profile_config")
                if profile_rel != PROFILE_CONFIG_REL or case.get("fixture", {}).get("profile_config") != PROFILE_CONFIG_REL:
                    report.fail(f"{where}: fixture must reference the parent-supplied runtime JSON profile")
                if fixture_data.get("overlay_files"):
                    report.fail(f"{where}: file-only profile must not create an OpenCode project-agent file")
                profile_path = root / PROFILE_CONFIG_REL
                if not profile_path.is_file():
                    report.fail(f"{where}: missing reviewable runtime profile JSON {PROFILE_CONFIG_REL}")
                else:
                    try:
                        profile_data = json.loads(read_text(profile_path))
                        permission = profile_data["agent"]["entry-files-only"]["permission"]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        permission = {}
                    if (
                        not isinstance(permission, dict)
                        or list(permission)[:1] != ["*"]
                        or permission.get("*") != "deny"
                        or {key for key, value in permission.items() if value == "allow"}
                        != {"read", "list", "glob", "grep", "edit"}
                        or permission.get("external_directory") != "deny"
                    ):
                        report.fail(f"{where}: runtime profile must wildcard-deny first and allow only the five file tools")
            if case_id == "discovery-quickstart-entry":
                entry_check = case.get("entry_check", {})
                required_reads = entry_check.get("required_reads", [])
                groups = entry_check.get("required_read_groups", [])
                if "README.md" in required_reads or "docs/agents/start-here.md" in required_reads:
                    report.fail(f"{where}: direct-entry rubric must not require rereading README or one fixed packet form")
                if groups != [["docs/agents/references/onboarding.json", "docs/agents/start-here.md"]]:
                    report.fail(f"{where}: direct-entry rubric must accept onboarding.json or readable start-here.md")
                required_substantive = {
                    "docs/agents/research-opportunities.json",
                    "docs/agents/research-campaign.md",
                    "docs/agents/references/compatibility.json",
                    "examples/agent-campaign/campaign.json",
                }
                if not required_substantive <= set(required_reads):
                    report.fail(f"{where}: direct-entry rubric must require public research, compatibility, and campaign facts")
            if case_id == "discovery-quickstart-entry" and not all(
                entry in prompt for entry in ("docs/agents/quickstart.md", "docs/agents/first-search.md")
            ):
                report.fail(f"{where}: direct-entry prompt must expose quickstart and first-search as entry options")
            if case_id == "discovery-pasted-entry" and case.get("pasted_canonical_entry") != "root_route":
                report.fail(f"{where}: pasted-entry task must request the canonical generated root route")

    research_case = next((case for case in discovery_cases if case.get("case_id") == "discovery-research"), None)
    if research_case:
        user_task = str(research_case.get("user_task", "")).casefold()
        for condition in ("invite", "not installed", "scheduler"):
            if condition not in user_task:
                report.fail(f"{TASKS_REL}: discovery-research user objective must disclose unavailable {condition}")
        prompt = str(research_case.get("prompt", ""))
        rubric_checks = [
            check
            for deliverable in research_case.get("deliverables", [])
            if isinstance(deliverable, dict)
            for check in deliverable.get("checks", [])
            if isinstance(check, dict)
        ]
        for field, options in DISCOVERY_ENUM_OPTIONS.items():
            option_pattern = r"\s*\|\s*".join(re.escape(option) for option in options)
            enum_pattern = rf"\b{re.escape(field.rsplit('.', 1)[-1])}\s+enum\s+{option_pattern}\b"
            if re.search(enum_pattern, prompt) is None:
                report.fail(
                    f"{TASKS_REL}: enum alternatives for {field!r} must be disclosed together in the task prompt"
                )
            checks_for_field = [
                check for check in rubric_checks
                if check.get("field") == field and check.get("kind") in {"json_field_equals", "json_field_in"}
            ]
            if not checks_for_field:
                report.fail(f"{TASKS_REL}: structured discovery field {field!r} must have a rubric check")
            for check in checks_for_field:
                if check.get("kind") == "json_field_equals":
                    valid_values = [check.get("value")]
                else:
                    valid_values = check.get("values")
                if (
                    not isinstance(valid_values, list)
                    or not valid_values
                    or any(value not in options for value in valid_values)
                ):
                    report.fail(
                        f"{TASKS_REL}: graded enum value(s) for {field!r} must be disclosed alternatives"
                    )

    cold_case = next((case for case in discovery_cases if case.get("case_id") == "discovery-contribution-cold-start"), None)
    if cold_case:
        fixture_spec = root / EVALS_DIR / "fixtures" / cold_case["case_id"] / "fixture.json"
        if fixture_spec.is_file():
            try:
                fixture_data = json.loads(read_text(fixture_spec))
            except json.JSONDecodeError:
                fixture_data = {}
            removed = set(fixture_data.get("remove_paths", [])) if isinstance(fixture_data, dict) else set()
            if not {"AGENTS.md", "CLAUDE.md"} <= removed:
                report.fail(f"{TASKS_REL}: cold-start fixture must actually remove AGENTS.md and CLAUDE.md")
            conditions = fixture_data.get("conditions", []) if isinstance(fixture_data, dict) else []
            if not any(
                "visible workspace contents only" in str(item) and "no blanket claim" in str(item)
                for item in conditions
            ):
                report.fail(f"{TASKS_REL}: cold-start fixture must record file omission without a blanket native-loading claim")
        if "automatic" in str(cold_case.get("prompt", "")).casefold() and "autoload" in str(cold_case.get("prompt", "")).casefold():
            report.fail(f"{TASKS_REL}: cold-start prompt must not claim native harness autoload behavior")
        prompt = str(cold_case.get("prompt", "")).casefold()
        if not all(token in prompt for token in ("standalone shell invocation", "native exit metadata", "pipeline")):
            report.fail(f"{TASKS_REL}: cold-start prompt must preserve standalone command exit metadata")

    template_path = root / TRIAL_TEMPLATE_REL
    if not template_path.is_file():
        report.fail(f"fresh-agent evals: missing {TRIAL_TEMPLATE_REL}")
    else:
        try:
            template = json.loads(read_text(template_path))
        except json.JSONDecodeError as exc:
            report.fail(f"{TRIAL_TEMPLATE_REL}: invalid JSON: {exc}")
            template = None
        if isinstance(template, dict):
            if "execution_mode" not in template:
                report.fail(f"{TRIAL_TEMPLATE_REL}: must label execution_mode (fresh vs replay)")
            notes = str(template.get("notes", "")).casefold()
            if not all(token in notes for token in ("synthetic rubric-only", "replay-only", "not cryptographic authentication")):
                report.fail(f"{TRIAL_TEMPLATE_REL}: evidence scope must distinguish synthetic, replay, and trace consistency")
            for required_field in (
                "task_mode", "fixture_conditions", "observed_actions", "session_id",
                "observed_action_schema_version", "outcome_schema_version", "child_tool_profile",
            ):
                if required_field not in template:
                    report.fail(f"{TRIAL_TEMPLATE_REL}: missing shared record field {required_field!r}")
            for banned in FORBIDDEN_RECORD_KEYS:
                if banned in template:
                    report.fail(f"{TRIAL_TEMPLATE_REL}: raw event tails must stay in private scratch; found {banned!r}")
            for key in template:
                if SENSITIVE_RECORD_KEY_RE.search(str(key)):
                    report.fail(f"{TRIAL_TEMPLATE_REL}: excluded resource/accounting key {key!r} not allowed")

    for relpath, required, forbidden in (
        (RUNNER_REL, ("execution_mode", "--variant", "--agent", "--pure", "OPENCODE_CONFIG_CONTENT",
                      "_effective_profile_probe", "render_child_prompt",
                      "PROMPT_PROTOCOL_VERSION", "scratch_path_for", "fixture_conditions",
                      "task_mode", "model sessions", "network", "outcome_schema_version",
                      "native tool-call ID", "shell exit code"), ("raw_output_tail",)),
        (GRADER_REL, ("remove_paths", "fixture_conditions", "rubric_checks", "assessment_scope", "evidence_scope",
                      "grader_regression_only", "check_entry_trial", "check_baseline_outcome", "legal"), ("raw_output_tail",)),
        (ENTRY_CHECKS_REL, ("research_entry", "pasted_contribution", "native_tool_call_id_sha256", "offline_next_action",
                            "required_read_groups", "action_trace_consistency_problems", "unknown execution mode"), ("raw_output_tail",)),
        (OUTCOME_CHECKS_REL, ("shell_exit_code_status", "standalone_only", "command_truncated", "unknown"), ("raw_output_tail",)),
        (TRACE_EVIDENCE_REL, ("raw_events_sha256", "normalize_tool_actions", "action_trace_consistency_problems"), ("raw_output_tail",)),
        (TOOL_OUTCOMES_REL, ("callID", "state.metadata.exit", "completed", "command_sha256"), ("raw_output_tail",)),
        (TOOL_PROFILE_REL, ("OPENCODE_CONFIG_CONTENT", "default_tool_permission", "future_unlisted_tool", "external_directory"), ("raw_output_tail",)),
        (FIXTURE_RUNNER_REL, ("--variant",), ("output_tail",)),
    ):
        path = (root / relpath).resolve()
        if not path.is_file():
            report.fail(f"fresh-agent evals: missing {relpath}")
            continue
        text = read_text(path)
        for needle in required:
            if needle not in text:
                report.fail(f"{relpath}: runner must carry {needle!r}")
        for needle in forbidden:
            if needle in text:
                report.fail(f"{relpath}: raw tail field {needle!r} must stay out of public records")

    if not (root / PROFILE_CONFIG_REL).is_file():
        report.fail(f"fresh-agent evals: missing reviewable runtime profile {PROFILE_CONFIG_REL}")

    with tempfile.TemporaryDirectory(prefix="of-eval-prefail-") as tmp:
        prepared = Path(tmp) / "docs-fix"
        prep = subprocess.run(
            [sys.executable, str(grader_path), "prepare", "--case", "docs-fix", "--dest", str(prepared)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if prep.returncode != 0:
            report.fail(f"evals: docs-fix prepare failed: {(prep.stdout + prep.stderr)[-160:]}")
        else:
            if not (prepared / DISCOVERY_CHECKS_REL).is_file():
                report.fail(
                    "evals: prepared snapshots must include the shared discovery checker dependency"
                )
            if not (prepared / TRACE_EVIDENCE_REL).is_file():
                report.fail("evals: prepared snapshots must include the retained-trace consistency helper")
            if not (prepared / TOOL_OUTCOMES_REL).is_file():
                report.fail("evals: prepared snapshots must include the retained-trace parser dependency")
            validator = root / ".github/scripts/validate_agent_docs.py"
            proc = subprocess.run(
                [sys.executable, str(validator), "truthfulness", "--root", str(prepared)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                report.fail(
                    "evals: the prepared docs-fix fixture passes truthfulness before repair; "
                    "the fixture does not exercise a real failure"
                )
            else:
                report.note("evals: prepared docs-fix fixture fails truthfulness before repair, as intended")

    with tempfile.TemporaryDirectory(prefix="of-eval-entry-fixtures-") as tmp:
        for case_id in sorted(FILE_ONLY_DISCOVERY_CASES):
            case = next((item for item in cases if item.get("case_id") == case_id), {})
            prepared = Path(tmp) / case_id
            proc = subprocess.run(
                [sys.executable, str(grader_path), "prepare", "--case", case_id, "--dest", str(prepared)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                report.fail(f"evals: {case_id} prepare failed: {(proc.stdout + proc.stderr).strip()[-160:]}")
                continue
            if (prepared / "AGENTS.md").exists() or (prepared / "CLAUDE.md").exists():
                report.fail(f"evals: {case_id} did not remove root instruction files")
            if (prepared / ".opencode").exists():
                report.fail(f"evals: {case_id} unexpectedly contains a project .opencode directory")
            if (prepared / PROFILE_CONFIG_REL).exists():
                report.fail(f"evals: {case_id} copied the parent-only permission config into the child workspace")
            runtime_dirs = [
                item for item in prepared.rglob("node_modules")
                if item.is_dir()
            ]
            if runtime_dirs:
                report.fail(f"evals: {case_id} prepared workspace contains a runtime dependency tree")
            artifact_path = case.get("entry_check", {}).get("artifact_path")
            if not isinstance(artifact_path, str) or not (prepared / artifact_path).is_file():
                report.fail(f"evals: {case_id} fixture must seed its artifact for the edit-only tool allowlist")
            if not (prepared / "legal/terms.md").is_file():
                report.fail(f"evals: {case_id} snapshot omitted legal/terms.md linked by the onboarding entry")
            entry_check = case.get("entry_check", {})
            required_paths = list(entry_check.get("required_reads", []))
            required_paths.extend(entry_check.get("entry_paths", []))
            for group in entry_check.get("required_read_groups", []):
                if isinstance(group, list):
                    required_paths.extend(group)
            required_paths.append("docs/agents/first-contribution.md")
            for relpath in required_paths:
                if not (prepared / relpath).is_file():
                    report.fail(f"evals: {case_id} prepared snapshot omitted referenced public source {relpath}")
    if (root / "legal/terms.md").is_file():
        report.note("evals: direct/pasted entry snapshots retain linked legal documentation and the runtime JSON file-only profile")
    else:
        report.fail("evals: missing public legal/terms.md source required by onboarding links")

    proc = subprocess.run(
        [sys.executable, str(grader_path), "regress"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    if proc.returncode != 0:
        report.fail(
            f"fresh-agent evals: grader regress failed (exit {proc.returncode}): {tail[0][:200]}"
        )
    else:
        report.note("evals: grader regress passed for correct and plausible wrong outputs")

    runner_path = root / RUNNER_REL
    proc = subprocess.run(
        [sys.executable, str(runner_path), "--selfcheck"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    if proc.returncode != 0:
        report.fail(
            f"fresh-agent evals: runner selfcheck failed (exit {proc.returncode}): {tail[0][:200]}"
        )
    else:
        report.note("evals: runner prompt/profile and native tool-outcome selfcheck passed")

    _check_v2_suite(report, root)
    _check_research_suite(report, root)


def run_research_selfcheck(report, root: Path, *, caller: str) -> None:
    """Run the deterministic callback/evaluator selfcheck once per validator process."""
    global RESEARCH_SELFTEST_RAN
    runner = root / RESEARCH_RUNNER_REL
    if not runner.is_file():
        report.fail(f"fresh-agent research eval: missing {RESEARCH_RUNNER_REL}")
        return
    if not (root / ".git").exists():
        report.note("research evaluation selfcheck skipped in a throwaway validator-mutation copy")
        return
    if RESEARCH_SELFTEST_RAN:
        report.note(f"research evaluation synthetic selfcheck already ran before {caller}")
        return
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, str(runner), "--selfcheck", "--repo", str(root)],
            capture_output=True,
            text=True,
            timeout=240,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        RESEARCH_SELFTEST_RAN = True
        report.fail(f"research evaluation {caller} selfcheck could not complete: {type(exc).__name__}")
        return
    RESEARCH_SELFTEST_RAN = True
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = None
    if (
        proc.returncode != 0
        or not isinstance(result, dict)
        or result.get("status") != "pass"
        or result.get("synthetic_selfcheck") is not True
        or type(result.get("native_calls")) is not int or result.get("native_calls") != 0
        or type(result.get("model_calls")) is not int or result.get("model_calls") != 0
    ):
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["no selfcheck output"]
        report.fail(f"research evaluation {caller} selfcheck failed: {tail[0][:220]}")
    else:
        report.note("research evaluation independent recomputation, negative cases, and callback bounds passed offline (no native/model calls)")


def _completion_contract_api(root: Path):
    """Load the same stdlib contract implementation used by startup and fixtures."""
    sys.dont_write_bytecode = True
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from onboarding.completion import load_fixture_contract, render_finish_commands  # noqa: PLC0415

    return load_fixture_contract, render_finish_commands


def _check_research_suite(report, root: Path) -> None:
    """Check the optional research fixture's typed inputs, provenance, and CLI."""
    required = (
        f"{RESEARCH_DIR}/README.md",
        f"{RESEARCH_DIR}/task.md",
        f"{RESEARCH_DIR}/callback-contract.md",
        f"{RESEARCH_DIR}/source-provenance.json",
        f"{RESEARCH_DIR}/fixture.json",
        f"{RESEARCH_DIR}/fixture.schema.json",
        f"{RESEARCH_DIR}/research-result.schema.json",
        f"{RESEARCH_DIR}/comparison-report.schema.json",
        f"{RESEARCH_DIR}/series.schema.json",
        f"{RESEARCH_DIR}/trial-record.schema.json",
        f"{RESEARCH_DIR}/verdict.schema.json",
        f"{RESEARCH_DIR}/contract.py",
        f"{RESEARCH_DIR}/callback_worker.py",
        f"{RESEARCH_DIR}/synthetic_evaluator.py",
        f"{RESEARCH_DIR}/series.py",
        f"{RESEARCH_DIR}/typed_schemas.py",
        f"{RESEARCH_DIR}/research_access.py",
        f"{RESEARCH_DIR}/research_grader.py",
        f"{RESEARCH_DIR}/research_selfcheck.py",
        RESEARCH_RUNNER_REL,
    )
    missing = [relative for relative in required if not (root / relative).is_file()]
    for relative in missing:
        report.fail(f"fresh-agent research eval: missing {relative}")
    if missing:
        return
    try:
        fixture = json.loads(read_text(root / f"{RESEARCH_DIR}/fixture.json"))
        provenance = json.loads(read_text(root / f"{RESEARCH_DIR}/source-provenance.json"))
        schemas = [
            json.loads(read_text(root / f"{RESEARCH_DIR}/{name}"))
            for name in (
                "fixture.schema.json", "research-result.schema.json",
                "comparison-report.schema.json", "series.schema.json",
                "trial-record.schema.json", "verdict.schema.json",
            )
        ]
    except json.JSONDecodeError as exc:
        report.fail(f"fresh-agent research eval: invalid JSON input or schema: {exc}")
        return
    if not isinstance(fixture, dict) or not isinstance(provenance, dict) or any(not isinstance(item, dict) for item in schemas):
        report.fail("fresh-agent research eval: fixture, provenance, and schemas must be JSON objects")
        return
    try:
        diagnostic_schema = json.loads(read_text(root / f"{V2_DIR}/diagnostic.schema.json"))
    except (OSError, json.JSONDecodeError) as exc:
        report.fail(f"fresh-agent research eval: shared diagnostic schema is unavailable or invalid: {type(exc).__name__}")
        diagnostic_schema = {}
    research_diagnostic = schemas[5].get("$defs", {}).get("diagnostic")
    shared_contract = {
        key: diagnostic_schema[key]
        for key in ("type", "additionalProperties", "required", "properties")
        if key in diagnostic_schema
    }
    properties = diagnostic_schema.get("properties") if isinstance(diagnostic_schema.get("properties"), dict) else {}
    native_reference_options = properties.get("native_action", {}).get("anyOf", []) if isinstance(properties.get("native_action"), dict) else []
    native_reference = native_reference_options[1] if len(native_reference_options) > 1 else {}
    if (
        not shared_contract
        or research_diagnostic != shared_contract
        or not isinstance(native_reference, dict)
        or not {"action_index", "event_sequence", "completion_sequence", "identity_kind", "identity_sha256", "tool", "command"}
        <= set(native_reference.get("required", []))
    ):
        report.fail("fresh-agent research eval: docs and research diagnostics must share the action-indexed sanitized contract")
    if fixture.get("evaluation_origin") != "synthetic_fixture_only":
        report.fail("fresh-agent research eval: fixture origin must be explicitly synthetic")
    uniform_reference = fixture.get("uniform_reference")
    if (
        not isinstance(uniform_reference, dict)
        or uniform_reference.get("identity") != "synthetic_uniform_pair_selector_v1"
        or uniform_reference.get("is_openforage_genetic") is not False
    ):
        report.fail("fresh-agent research eval: comparator must remain explicitly synthetic, not genetic")
    inputs = fixture.get("inputs")
    roles = {item.get("input_id"): item.get("role") for item in inputs if isinstance(item, dict)} if isinstance(inputs, list) else {}
    if roles != {
        "development-alpha": "development",
        "development-beta": "development",
        "reserved-heldout-gamma": "reserved-heldout",
    }:
        report.fail("fresh-agent research eval: two development inputs and one reserved held-out input are required")
    depths_over_six = False
    has_excluded_or_unloaded = False
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            streams = item.get("candidate_streams")
            if isinstance(streams, dict):
                for records in streams.values():
                    if not isinstance(records, list):
                        continue
                    if any(
                        isinstance(record, dict)
                        and type(record.get("depth")) is int
                        and record["depth"] > 6
                        for record in records
                    ):
                        depths_over_six = True
            pairs = item.get("pairs")
            if isinstance(pairs, list) and any(
                isinstance(pair, dict)
                and (pair.get("eligible") is False or pair.get("loaded") is False)
                for pair in pairs
            ):
                has_excluded_or_unloaded = True
    if not depths_over_six or not has_excluded_or_unloaded:
        report.fail("fresh-agent research eval: synthetic fixture must disclose depth-over-six and excluded/unloaded pairs")
    try:
        load_fixture_contract, render_finish_commands = _completion_contract_api(root)
        research_contract = load_fixture_contract(root, "research", "paper-backed-coverage-challenger")
    except (OSError, ValueError, KeyError) as exc:
        report.fail(f"fresh-agent research eval: canonical completion contract is invalid: {type(exc).__name__}")
        research_contract = {}
        render_finish_commands = None
    source_path = provenance.get("source_excerpt_path")
    if (
        not isinstance(source_path, str)
        or source_path not in research_contract.get("available_input_paths", [])
        or not (root / source_path).is_file()
        or provenance.get("identifier") != "arXiv:1504.04909"
        or provenance.get("url") != "https://arxiv.org/abs/1504.04909"
    ):
        report.fail("fresh-agent research eval: paper attribution must use the contract-declared public excerpt and link")
    task = " ".join(read_text(root / f"{RESEARCH_DIR}/task.md").casefold().split())
    for phrase in (
        "reserved held-out", "incomplete_comparison", "synthetic_uniform_pair_selector_v1",
        "not a perfect blind evaluation", "out-of-sample return correlation",
        "explicit fixture packet", "--check-completion",
    ):
        if phrase not in task:
            report.fail(f"fresh-agent research eval: task disclosure is missing {phrase!r}")
    runner_text = read_text(root / RESEARCH_RUNNER_REL)
    for fragment in (
        "--selfcheck", "--freeze", "--native-run", "--grade", "--model",
        "_native_argv", "run_bounded", "load_fixture_contract",
        "Available input paths:", "research-completion-check",
    ):
        if fragment not in runner_text:
            report.fail(f"{RESEARCH_RUNNER_REL}: missing explicit research CLI/helper contract {fragment!r}")
    research_access_text = read_text(root / f"{RESEARCH_DIR}/research_access.py")
    research_grader_text = read_text(root / f"{RESEARCH_DIR}/research_grader.py")
    if "load_fixture_contract" not in research_access_text or "READABLE_PATHS" in research_access_text or "OUTPUT_PATHS" in research_access_text:
        report.fail("fresh-agent research eval: subject access audit must derive inputs/outputs from the canonical fixture contract")
    if "load_fixture_contract" not in research_grader_text or "ALLOWED_CHANGED = {" in research_grader_text:
        report.fail("fresh-agent research eval: research output scope must derive from the canonical fixture contract")
    if render_finish_commands is not None and research_contract:
        try:
            commands = render_finish_commands(
                research_contract,
                "research",
                None,
            )
            if not any(item["check_id"] == "research-completion-check" and "--check-completion" in item["command"] for item in commands):
                report.fail("fresh-agent research eval: canonical fixed finish grammar omits the final completion invocation")
        except ValueError as exc:
            report.fail(f"fresh-agent research eval: fixed finish grammar could not project a safe series path: {type(exc).__name__}")
    run_research_selfcheck(report, root, caller="evals")


def _validator_modes_from_source(path: Path) -> set[str]:
    try:
        tree = ast.parse(read_text(path), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "MODES" for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            return set()
        return {
            key.value for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        } | {"all"}
    return set()


def _check_v2_suite(report, root: Path) -> None:
    required = (
        V2_CASES_REL, V2_SCHEMA_REL, V2_ORACLE_REL, V2_SERIES_REL,
        V2_RUNNER_REL, V2_FREEZE_REL, V2_SELFTEST_REL,
        f"{V2_DIR}/commands.py", f"{V2_DIR}/evidence.py",
        f"{V2_DIR}/diagnostics.py", f"{V2_DIR}/diagnostic.schema.json",
        f"{V2_DIR}/grader.py", f"{V2_DIR}/native_events.py",
        f"{V2_DIR}/semantic.py", f"{V2_DIR}/comparison.py", f"{V2_DIR}/manifest.py",
        "scripts/onboarding/completion.py",
    )
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        for path in missing:
            report.fail(f"fresh-agent evals v2: missing {path}")
        return
    try:
        cases_data = json.loads(read_text(root / V2_CASES_REL))
        schema = json.loads(read_text(root / V2_SCHEMA_REL))
        oracle = json.loads(read_text(root / V2_ORACLE_REL))
        onboarding = json.loads(read_text(root / "docs/agents/references/onboarding.json"))
    except json.JSONDecodeError as exc:
        report.fail(f"fresh-agent evals v2: invalid JSON: {exc}")
        return
    if not all(isinstance(item, dict) for item in (cases_data, schema, oracle, onboarding)):
        report.fail("fresh-agent evals v2: cases, schema, oracle, and onboarding source must be JSON objects")
        return
    try:
        historical = json.loads(read_text(root / V2_SERIES_REL))
    except json.JSONDecodeError as exc:
        report.fail(f"fresh-agent evals v2: historical series-1 manifest is invalid JSON: {exc}")
        return
    if (
        not isinstance(historical, dict)
        or historical.get("series_id") != "first-finished-contribution-v2-series-1"
        or historical.get("manifest_schema_version") != 1
        or historical.get("series_version") != 1
    ):
        report.fail("fresh-agent evals v2: byte-preserved series-1 must remain a historical pre-native draft")
    if hashlib.sha256((root / V2_SERIES_REL).read_bytes()).hexdigest() != "00d615cf24e0daf6742deace4a78e92a7b460880c1642e0db2a5fbe8aac8fa73":
        report.fail("fresh-agent evals v2: the original pre-native series-1 manifest bytes changed")
    historical_docs = read_text(root / f"{V2_DIR}/README.md").casefold()
    if "historical, pre-native series-1 draft" not in historical_docs or "no native model trials" not in historical_docs:
        report.fail("fresh-agent evals v2: series-1 must remain labeled as an unrun historical draft")
    historical_digest = hashlib.sha256((root / V2_SERIES_REL).read_bytes()).hexdigest()
    if historical_digest != "00d615cf24e0daf6742deace4a78e92a7b460880c1642e0db2a5fbe8aac8fa73":
        report.fail("fresh-agent evals v2: historical series-1 manifest bytes must remain preserved")
    suite_readme = read_text(root / f"{V2_DIR}/README.md").casefold()
    if "historical, pre-native series-1 draft" not in suite_readme or "no native model trials" not in suite_readme:
        report.fail("fresh-agent evals v2: series-1 must be labeled as an unrun historical draft")
    cases = cases_data.get("cases")
    expected_ids = {"first-finished-contribution-primary", "first-finished-contribution-heldout"}
    if not isinstance(cases, list) or {item.get("case_id") for item in cases if isinstance(item, dict)} != expected_ids:
        report.fail("fresh-agent evals v2: exactly the primary and reserved held-out cases must be defined")
        return
    roles = {item.get("case_id"): item.get("role") for item in cases if isinstance(item, dict)}
    if roles != {
        "first-finished-contribution-primary": "primary",
        "first-finished-contribution-heldout": "held-out",
    }:
        report.fail("fresh-agent evals v2: primary and held-out roles must remain distinct")
    oracle_cases = oracle.get("cases")
    if not isinstance(oracle_cases, dict) or set(oracle_cases) != expected_ids:
        report.fail("fresh-agent evals v2: oracle cases must match the public case inventory")
    elif any(not isinstance(value, dict) for value in oracle_cases.values()):
        report.fail("fresh-agent evals v2: each oracle case must be an object")

    def enum_at(value, *keys):
        for key in keys:
            if not isinstance(value, dict):
                return []
            value = value.get(key)
        return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []

    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    case_enum = enum_at(properties, "case_id", "enum")
    if set(case_enum) != expected_ids:
        report.fail("fresh-agent evals v2: completion schema case_id enum must match the defined cases")
    route_enums = properties.get("route", {}).get("properties", {}) if isinstance(properties.get("route"), dict) else {}
    if not isinstance(route_enums, dict):
        report.fail("fresh-agent evals v2: route schema properties must be an object")
        route_enums = {}
    contribution_enums = enum_at(route_enums, "offline_contribution_access", "enum")
    auth_enums = enum_at(route_enums, "live_campaign_authorization", "enum")
    task_enum = enum_at(route_enums, "selected_task", "enum")
    source_enum = enum_at(route_enums, "authoritative_sources", "items", "enum")
    if not {"not_required", "required", "unknown"} <= set(contribution_enums):
        report.fail("fresh-agent evals v2: offline contribution access enum is incomplete")
    if "explicit_user_authorization_required" not in auth_enums:
        report.fail("fresh-agent evals v2: live authorization fact is absent from the schema")
    tasks = onboarding.get("tasks", {}) if isinstance(onboarding, dict) else {}
    try:
        load_fixture_contract, render_finish_commands = _completion_contract_api(root)
        docs_contract = load_fixture_contract(root, "contribute", "first-finished-contribution-primary")
        research_contract = load_fixture_contract(root, "research", "paper-backed-coverage-challenger")
        for task_id, fixture_id, contract in (
            ("contribute", "first-finished-contribution-primary", docs_contract),
            ("research", "paper-backed-coverage-challenger", research_contract),
        ):
            if contract.get("contract_version") != 1 or not contract.get("available_input_paths") or not contract.get("deliverables"):
                report.fail(f"fresh-agent evals v2: {task_id}/{fixture_id} completion contract is incomplete")
        docs_finish = render_finish_commands(docs_contract, "contribute", None)
        if not docs_finish or docs_finish[-1]["check_id"] != "docs-completion-check" or "--check-completion" not in docs_finish[-1]["command"]:
            report.fail("fresh-agent evals v2: final docs completion invocation is not the last fixed finish command")
        manifest_text = read_text(root / f"{V2_DIR}/manifest.py")
        if "scripts/onboarding/completion.py" not in manifest_text:
            report.fail("fresh-agent evals v2: completion implementation is absent from the frozen source closure")
        manifest_directory = str(root / V2_DIR)
        try:
            sys.path.insert(0, manifest_directory)
            manifest_spec = importlib.util.spec_from_file_location(
                "_fresh_agent_v2_manifest_for_eval_closure", root / f"{V2_DIR}/manifest.py",
            )
            if manifest_spec is None or manifest_spec.loader is None:
                raise ImportError("v2 manifest module loader is unavailable")
            manifest_module = importlib.util.module_from_spec(manifest_spec)
            manifest_spec.loader.exec_module(manifest_module)
            frozen_closure = set(manifest_module.frozen_paths(root))
        except (ImportError, OSError, ValueError, RuntimeError, TypeError) as exc:
            frozen_closure = set()
            report.fail(f"fresh-agent evals v2: frozen source closure could not be enumerated: {type(exc).__name__}")
        finally:
            if sys.path and sys.path[0] == manifest_directory:
                sys.path.pop(0)
            elif manifest_directory in sys.path:
                sys.path.remove(manifest_directory)
        required_closure_inputs = {
            ".gitignore", ".github/scripts/smoke_first_contribution.py",
        }
        missing_closure_inputs = required_closure_inputs - frozen_closure
        for relative in sorted(missing_closure_inputs):
            report.fail(f"fresh-agent evals v2: frozen source closure omits required grading input {relative}")
        applicable_ignore_rules = set()
        for relative in frozen_closure:
            parent = Path(relative).parent
            while True:
                ignore_relative = (parent / ".gitignore").as_posix()
                if (root / ignore_relative).is_file():
                    applicable_ignore_rules.add(ignore_relative)
                if parent == Path("."):
                    break
                parent = parent.parent
        missing_ignore_rules = applicable_ignore_rules - frozen_closure
        if missing_ignore_rules:
            report.fail(
                "fresh-agent evals v2: frozen source closure omits applicable ignore rules: "
                + ", ".join(sorted(missing_ignore_rules))
            )
        for relative in required_closure_inputs:
            if not (root / relative).is_file():
                report.fail(f"fresh-agent evals v2: required grading input is missing: {relative}")
        v2_runner_text = read_text(root / V2_RUNNER_REL)
        v2_grader_text = read_text(root / f"{V2_DIR}/grader.py")
        v2_commands_text = read_text(root / f"{V2_DIR}/commands.py")
        v2_snapshot_text = read_text(root / f"{V2_DIR}/snapshot.py")
        if (
            "fixture_deliverable" not in v2_runner_text
            or "load_fixture_contract" not in v2_grader_text
            or "completion-checker" not in v2_commands_text
            or "SCRATCH_REL" not in v2_snapshot_text
        ):
            report.fail("fresh-agent evals v2: prompt, semantic allowlist, grader, and snapshot must share contract/scratch semantics")
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        report.fail(f"fresh-agent evals v2: strict canonical fixture contract validation failed: {type(exc).__name__}")
        docs_contract = {}
        research_contract = {}
    selected_enum = task_enum
    if not isinstance(tasks, dict) or set(selected_enum) != set(tasks):
        report.fail("fresh-agent evals v2: selected_task enum must match canonical onboarding task ids")
    generated_entries = onboarding.get("generated_entrypoints", {})
    canonical_first_contribution = onboarding.get("source_paths", {}).get("first_contribution")
    expected_source_ids = set(generated_entries.values()) | {
        canonical_first_contribution, "docs/agents/references/onboarding.json"
    } if isinstance(generated_entries, dict) else set()
    if not expected_source_ids or set(source_enum) != expected_source_ids:
        report.fail("fresh-agent evals v2: authoritative source ids must match canonical onboarding entrypoints")
    contribute_route = tasks.get("contribute", {}) if isinstance(tasks, dict) else {}
    access_rule = onboarding.get("access_rule", "") if isinstance(onboarding, dict) else ""
    if (
        not isinstance(contribute_route, dict)
        or contribute_route.get("offline_allowed") is not True
        or contribute_route.get("live_campaign_required") is not False
    ):
        report.fail("fresh-agent evals v2: contribution access facts must remain tied to canonical onboarding booleans")
    if not isinstance(access_rule, str) or "explicitly authorized live campaign" not in access_rule:
        report.fail("fresh-agent evals v2: live authorization fact must remain tied to canonical onboarding access text")
    mode_path = root / ".github/scripts/validate_agent_docs.py"
    modes = _validator_modes_from_source(mode_path)
    mode_enum = enum_at(schema.get("$defs", {}), "validation_observation", "properties", "modes", "items", "enum")
    if not modes or set(mode_enum) != modes:
        report.fail("fresh-agent evals v2: schema validator-mode enum must match the authoritative validator")
    hidden_answer_markers = (
        "docs/agents/quickstart.md", "docs/agents/first-search.md",
        ".github/scripts/validate_agent_docs.py", "openforage.register(invite_code",
    )
    for case in cases:
        task = case.get("task") if isinstance(case, dict) else None
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(task, str) or "schema supplied" not in task:
            report.fail("fresh-agent evals v2: task must disclose the completion artifact schema")
            continue
        if case_id == "first-finished-contribution-primary":
            if "--check-completion" not in task or "contract-declared completion artifact" not in task:
                report.fail("fresh-agent evals v2: primary task must disclose the final contract checker and artifact order")
            if "trial-output/completion.json" in task:
                report.fail("fresh-agent evals v2: primary task duplicates a mutable deliverable path instead of using the contract")
        elif "trial-output/completion.json" not in task:
            report.fail("fresh-agent evals v2: reserved held-out task must preserve its existing completion-artifact protocol")
        for marker in hidden_answer_markers:
            if marker in task:
                report.fail(f"fresh-agent evals v2: task leaks a target or repair recipe ({marker})")
    oracle_cases = oracle.get("cases", {})
    if isinstance(oracle_cases, dict) and set(oracle_cases) == expected_ids and all(isinstance(value, dict) for value in oracle_cases.values()):
        primary_fixture = oracle_cases["first-finished-contribution-primary"].get("fixture", {})
        heldout_fixture = oracle_cases["first-finished-contribution-heldout"].get("fixture", {})
        if primary_fixture.get("path") == heldout_fixture.get("path") or primary_fixture == heldout_fixture:
            report.fail("fresh-agent evals v2: held-out fixture must be distinct from the primary fixture")
        for case_id in sorted(expected_ids):
            fixture = oracle_cases[case_id].get("fixture", {})
            relpath = fixture.get("path")
            if not isinstance(relpath, str) or Path(relpath).is_absolute() or ".." in Path(relpath).parts:
                report.fail(f"fresh-agent evals v2: {case_id} fixture path is unsafe")
            elif not (root / relpath).is_file():
                report.fail(f"fresh-agent evals v2: {case_id} fixture target is missing")
            if not isinstance(fixture.get("find"), str) or not isinstance(fixture.get("replace"), str):
                report.fail(f"fresh-agent evals v2: {case_id} fixture must have explicit text fragments")
            facts = oracle_cases[case_id].get("required_route_facts", {})
            if (
                not isinstance(facts, dict)
                or facts.get("selected_task") != "contribute"
                or facts.get("offline_contribution_access") != "not_required"
                or facts.get("live_campaign_authorization") != "explicit_user_authorization_required"
            ):
                report.fail(f"fresh-agent evals v2: {case_id} facts must match the canonical contribution route")
                facts = {}
            if (
                not isinstance(facts, dict)
                or
                facts.get("selected_task") not in task_enum
                or facts.get("offline_contribution_access") not in contribution_enums
                or facts.get("live_campaign_authorization") not in auth_enums
            ):
                report.fail(f"fresh-agent evals v2: {case_id} oracle facts are outside the disclosed typed enums")
    runner_text = read_text(root / V2_RUNNER_REL)
    required_adapter_facts = (
        "--pure", "--dir", "--agent", "--format", "--no-daemon",
        "--ask-for-approval", "workspace-write", "--ignore-user-config",
        "PYTHONDONTWRITEBYTECODE", "--include-heldout", "--series-manifest",
        "--expected-snapshot-sha256", "requested_model_selector",
        "--expected-snapshot-sha256", "requested_model_selector",
    )
    for fragment in required_adapter_facts:
        if fragment not in runner_text:
            report.fail(f"{V2_RUNNER_REL}: missing native adapter/isolation contract {fragment!r}")

    if not (root / ".git").exists():
        report.note("evals: v2 freeze/runtime selfchecks are skipped for throwaway validator-mutation copies")
        return

    freeze_text = read_text(root / V2_FREEZE_REL)
    for fragment in ("--series-id", "--series-manifest", "_write_manifest_once", "--archived-series1-drift-check"):
        if fragment not in freeze_text:
            report.fail(f"{V2_FREEZE_REL}: missing named-series/immutable-history control {fragment!r}")

    global V2_SELFTEST_RAN
    if (root / ".git").exists() and not V2_SELFTEST_RAN:
        v2_runner = subprocess.run(
            [sys.executable, str(root / V2_RUNNER_REL), "--selfcheck"],
            capture_output=True, text=True, timeout=600,
        )
        V2_SELFTEST_RAN = True
        if v2_runner.returncode != 0:
            tail = (v2_runner.stdout + v2_runner.stderr).strip().splitlines()[-1:] or [""]
            report.fail(f"fresh-agent evals v2: offline synthetic selfcheck failed (exit {v2_runner.returncode}): {tail[0][:200]}")
        else:
            report.note("evals: v2 schema, anti-forgery, normalization, isolation, drift, and synthetic-only regressions passed")
    elif (root / ".git").exists():
        report.note("evals: v2 offline synthetic selfcheck already ran in this validator process")
