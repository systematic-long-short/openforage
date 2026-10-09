#!/usr/bin/env python3
"""Exercise one canonical first-contribution completion in a public-only copy."""

from __future__ import annotations

import hashlib
import importlib
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "contribute"
FIXTURE_ID = "first-finished-contribution-primary"
MAX_COMMAND_SECONDS = 180
TRACKED_IGNORE_PATH = "docs/published-ignored.md"
TRACKED_IGNORE_LINK = "docs/portable-example.md"


class SmokeFailure(RuntimeError):
    """The isolated first-contribution exercise did not complete safely."""


def _child_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for key in tuple(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    return env


def _outside_temp_parent(source: Path) -> Path:
    candidates = (Path(tempfile.gettempdir()), source.parent)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and resolved != source and not resolved.is_relative_to(source):
            return resolved
    raise SmokeFailure("no temporary directory is available outside the source tree")


def _copy_helper(source: Path):
    helper_dir = source / "examples/fresh-agent-evals/v2"
    helper_path = helper_dir / "snapshot.py"
    if not helper_path.is_file() or helper_path.is_symlink():
        raise SmokeFailure("the canonical public-tree copy helper is unavailable")
    sys.path.insert(0, str(helper_dir))
    try:
        helper = importlib.import_module("snapshot")
        helper_file = Path(helper.__file__).resolve()
        if helper_file != helper_path.resolve():
            raise SmokeFailure("the public-tree copy helper did not load from this source tree")
        return helper
    except (ImportError, OSError) as exc:
        raise SmokeFailure("the canonical public-tree copy helper could not load") from exc


def _source_unchanged(source: Path, hashes: dict[str, str]) -> bool:
    for relative, expected in hashes.items():
        path = source / Path(relative)
        try:
            if path.is_symlink() or not path.is_file():
                return False
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _record_process(
    records: list[dict],
    label: str,
    command: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = MAX_COMMAND_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        records.append({
            "label": label,
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "launch_error": type(exc).__name__,
        })
        raise SmokeFailure(f"{label} could not run") from exc
    records.append({
        "label": label,
        "command": command,
        "exit_code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    })
    return process


def _require_exit(process: subprocess.CompletedProcess[str], label: str, expected: int = 0) -> None:
    if process.returncode != expected:
        raise SmokeFailure(f"{label} returned an unexpected exit status")


def _contract_argv(command: str) -> list[str]:
    words = shlex.split(command)
    if not words:
        raise SmokeFailure("the selected contract contains an empty finish command")
    if Path(words[0]).name in {"python", "python3"}:
        words[0] = sys.executable
    elif Path(words[0]).name != "git":
        raise SmokeFailure("the selected contract contains an unsupported finish command")
    return words


def _completion_module(copy_root: Path):
    source_scripts = (SOURCE_ROOT / "scripts").resolve()
    for name, module in tuple(sys.modules.items()):
        if name == "onboarding" or name.startswith("onboarding."):
            module_path = getattr(module, "__file__", None)
            if isinstance(module_path, str):
                try:
                    if Path(module_path).resolve().is_relative_to(source_scripts):
                        del sys.modules[name]
                except OSError:
                    del sys.modules[name]
    copy_scripts = copy_root / "scripts"
    sys.path.insert(0, str(copy_scripts))
    try:
        module = importlib.import_module("onboarding.completion")
    except (ImportError, OSError) as exc:
        raise SmokeFailure("the copied completion contract could not load") from exc
    if not Path(module.__file__).resolve().is_relative_to(copy_scripts.resolve()):
        raise SmokeFailure("completion contract import escaped the isolated public copy")
    return module


def _one_input(contract: dict, predicate, label: str) -> str:
    matches = [path for path in contract["available_input_paths"] if predicate(path)]
    if len(matches) != 1:
        raise SmokeFailure(f"the canonical contract does not disclose one {label} input")
    return matches[0]


def _validation_modes(command: str) -> list[str]:
    words = shlex.split(command)
    try:
        script_index = next(i for i, word in enumerate(words) if word.endswith("validate_agent_docs.py"))
    except StopIteration as exc:
        raise SmokeFailure("the canonical contract has no offline documentation validator command") from exc
    modes = []
    for word in words[script_index + 1 :]:
        if word.startswith("-"):
            break
        modes.append(word)
    if not modes:
        raise SmokeFailure("the canonical contract does not name validator modes")
    return modes


def _index_sha256(root: Path) -> str | None:
    index = root / ".git/index"
    if index.is_symlink() or not index.is_file():
        return None
    return hashlib.sha256(index.read_bytes()).hexdigest()


def _tracked_ignore_regression(source: Path, snapshot_helpers, env: dict[str, str]) -> dict:
    processes: list[dict] = []
    parent = _outside_temp_parent(source)
    with tempfile.TemporaryDirectory(prefix="of-tracked-ignore-source-", dir=parent) as temporary:
        fixture_source = Path(temporary) / "public-source"
        try:
            selected_files = snapshot_helpers.copy_public_tree(source, fixture_source)
        except Exception as exc:  # noqa: BLE001 - don't expose excluded source paths
            raise SmokeFailure("tracked-ignore regression source copy failed") from exc
        target = fixture_source / TRACKED_IGNORE_PATH
        page = fixture_source / TRACKED_IGNORE_LINK
        if target.exists() or target.is_symlink() or page.exists() or page.is_symlink():
            raise SmokeFailure("tracked-ignore regression paths already exist in the public source")

        ignore_file = fixture_source / ".gitignore"
        ignore_text = ignore_file.read_text(encoding="utf-8")
        ignore_lines = set(ignore_text.splitlines())
        if TRACKED_IGNORE_PATH not in ignore_lines:
            ignore_text = ignore_text.rstrip("\n") + f"\n{TRACKED_IGNORE_PATH}\n"
            ignore_file.write_text(ignore_text, encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Published ignored page\n", encoding="utf-8")
        page.write_text(
            "# Portable example\n\n"
            "[Published page](published-ignored.md#published-ignored-page).\n",
            encoding="utf-8",
        )
        selected_files[".gitignore"] = hashlib.sha256(ignore_file.read_bytes()).hexdigest()
        selected_files[TRACKED_IGNORE_PATH] = hashlib.sha256(target.read_bytes()).hexdigest()
        selected_files[TRACKED_IGNORE_LINK] = hashlib.sha256(page.read_bytes()).hexdigest()

        try:
            setup = snapshot_helpers.initialize_public_workspace(
                source, fixture_source, selected_files, env,
            )
        except Exception as exc:  # noqa: BLE001 - only safe bounded context is surfaced
            raise SmokeFailure("tracked-ignore regression source setup failed") from exc
        setup_processes = setup["processes"]

        ignore_match = _record_process(
            processes,
            "tracked ignored-name rule proof",
            f"git check-ignore --no-index -v -- {TRACKED_IGNORE_PATH}",
            ["git", "check-ignore", "--no-index", "-v", "--", TRACKED_IGNORE_PATH],
            cwd=fixture_source,
            env=env,
        )
        tracked_index = _record_process(
            processes,
            "tracked ignored-name index proof",
            f"git diff --cached --name-only -- {TRACKED_IGNORE_PATH}",
            ["git", "diff", "--cached", "--name-only", "--", TRACKED_IGNORE_PATH],
            cwd=fixture_source,
            env=env,
        )
        source_links = _record_process(
            processes,
            "tracked-ignore source links validator",
            "python .github/scripts/validate_agent_docs.py links --root .",
            [
                sys.executable,
                ".github/scripts/validate_agent_docs.py",
                "links",
                "--root",
                ".",
            ],
            cwd=fixture_source,
            env=env,
        )
        index_before = _index_sha256(fixture_source)
        child_script = fixture_source / ".github/scripts/smoke_first_contribution.py"
        child_smoke = _record_process(
            processes,
            "tracked-ignore full contribution smoke",
            "python .github/scripts/smoke_first_contribution.py --skip-tracked-ignore-regression",
            [sys.executable, str(child_script), "--skip-tracked-ignore-regression"],
            cwd=fixture_source,
            env=env,
            timeout=MAX_COMMAND_SECONDS * 2,
        )
        index_after = _index_sha256(fixture_source)
        try:
            child_result = json.loads(child_smoke.stdout)
        except json.JSONDecodeError:
            child_result = {}
        child_processes = child_result.get("processes", [])
        child_labels = [
            item.get("label") for item in child_processes
            if isinstance(item, dict)
        ]
        baseline = next(
            (item for item in child_processes if isinstance(item, dict)
             and item.get("label") == "baseline truthfulness and links validators"),
            {},
        )
        final = next(
            (item for item in child_processes if isinstance(item, dict)
             and item.get("label") == "final truthfulness and links validators"),
            {},
        )
        passed = (
            ignore_match.returncode == 0
            and "docs/published-ignored.md" in ignore_match.stdout
            and tracked_index.returncode == 0
            and TRACKED_IGNORE_PATH in tracked_index.stdout.splitlines()
            and source_links.returncode == 0
            and child_smoke.returncode == 0
            and child_result.get("status") == "passed"
            and child_result.get("source_unchanged") is True
            and baseline.get("exit_code") == 0
            and final.get("exit_code") == 0
            and child_labels
            and child_labels[-1] == "successful completion checker"
            and index_before is not None
            and index_before == index_after
        )
        return {
            "status": "passed" if passed else "failed",
            "path": TRACKED_IGNORE_PATH,
            "linked_from": TRACKED_IGNORE_LINK,
            "selected_file_count": setup["selected_file_count"],
            "selected_paths_sha256": setup["selected_paths_sha256"],
            "source_links_exit": source_links.returncode,
            "ignore_rule_exit": ignore_match.returncode,
            "tracked_index_exit": tracked_index.returncode,
            "full_smoke_exit": child_smoke.returncode,
            "baseline_validator_exit": baseline.get("exit_code"),
            "final_validator_exit": final.get("exit_code"),
            "source_index_sha256_before": index_before,
            "source_index_sha256_after": index_after,
            "source_index_unchanged": index_before is not None and index_before == index_after,
            "full_completion_smoke_passed": child_result.get("status") == "passed",
            "setup_processes": setup_processes,
            "processes": processes,
        }


def _run(source: Path, expected_hashes: dict[str, str], snapshot_helpers=None) -> dict:
    snapshot_helpers = snapshot_helpers or _copy_helper(source)
    temp_parent = _outside_temp_parent(source)
    env = _child_environment()
    processes: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="of-first-contribution-", dir=temp_parent) as temporary:
        temporary_root = Path(temporary).resolve()
        copy_root = temporary_root / "public-copy"
        if copy_root == source or copy_root.is_relative_to(source) or source.is_relative_to(copy_root):
            raise SmokeFailure("isolated copy is not separate from the source tree")
        try:
            expected_hashes.update(snapshot_helpers.copy_public_tree(source, copy_root))
        except Exception as exc:  # noqa: BLE001 - present only safe, bounded diagnostics
            if isinstance(exc, SmokeFailure):
                raise
            if str(exc) == "Git is required to evaluate repository ignore rules":
                raise SmokeFailure("Git is required to evaluate .gitignore rules for the public copy") from exc
            raise SmokeFailure("public-only source copy failed") from exc

        required_routes = ("README.md", "AGENTS.md", "CLAUDE.md")
        if any(not (copy_root / relative).is_file() for relative in required_routes):
            raise SmokeFailure("the isolated copy is missing a canonical root route file")

        route_command = (
            "python scripts/start_here.py --task contribute --fixture " + FIXTURE_ID
        )
        route = _record_process(
            processes,
            "selected contribution route",
            route_command,
            [
                sys.executable,
                "scripts/start_here.py",
                "--task",
                TASK_ID,
                "--fixture",
                FIXTURE_ID,
            ],
            cwd=copy_root,
            env=env,
        )
        _require_exit(route, "selected contribution route")
        if FIXTURE_ID not in route.stdout:
            raise SmokeFailure("the copied route did not display the selected contribution fixture")

        completion = _completion_module(copy_root)
        contract = completion.load_fixture_contract(copy_root, TASK_ID, FIXTURE_ID)
        guide_rel = _one_input(
            contract,
            lambda item: item.endswith("/first-contribution.md"),
            "first-contribution guide",
        )
        guide_path = copy_root / guide_rel
        guide_text = guide_path.read_text(encoding="utf-8")
        if "Optional fixture completion route" not in guide_text or "Follow the printed order" not in guide_text:
            raise SmokeFailure("the copied first-contribution route is incomplete")

        onboarding_rel = _one_input(
            contract,
            lambda item: item.startswith("docs/agents/references/") and item.endswith("/onboarding.json"),
            "canonical onboarding contract",
        )
        onboarding = completion.load_onboarding(copy_root)
        task_contract = onboarding["tasks"][TASK_ID]
        access_rule = str(onboarding.get("access_rule", ""))
        live_authorization = (
            "explicit_user_authorization_required"
            if "explicitly authorized live campaign" in access_rule
            else "unknown"
        )
        if task_contract.get("offline_allowed") is not True or task_contract.get("live_campaign_required") is not False:
            raise SmokeFailure("the copied contribution route does not establish offline completion access")

        completion_command = contract["permitted_finish_commands"].get("docs-completion-check")
        if not isinstance(completion_command, str):
            raise SmokeFailure("the canonical contract has no completion checker command")
        incomplete = _record_process(
            processes,
            "incomplete contribution rejection",
            completion_command,
            _contract_argv(completion_command),
            cwd=copy_root,
            env=env,
        )
        if incomplete.returncode == 0 or "Completion check: FAIL" not in incomplete.stdout:
            raise SmokeFailure("the canonical completion checker did not reject the incomplete contribution")

        try:
            git_setup = snapshot_helpers.initialize_public_workspace(
                source, copy_root, expected_hashes, env,
            )
        except Exception as exc:  # noqa: BLE001 - keep workspace paths private
            if "Git is required" in str(exc):
                raise SmokeFailure("Git is required to initialize the isolated public copy") from exc
            raise SmokeFailure("selected public workspace initialization failed") from exc
        processes.extend(git_setup["processes"])

        baseline_command = contract["permitted_finish_commands"].get("docs-baseline-validation")
        final_command = contract["permitted_finish_commands"].get("docs-final-validation")
        diff_command = contract["permitted_finish_commands"].get("docs-git-diff-check")
        if not all(isinstance(item, str) for item in (baseline_command, final_command, diff_command)):
            raise SmokeFailure("the canonical contract omits a required offline finishing command")
        baseline = _record_process(
            processes,
            "baseline truthfulness and links validators",
            baseline_command,
            _contract_argv(baseline_command),
            cwd=copy_root,
            env=env,
        )
        _require_exit(baseline, "baseline truthfulness and links validators")

        public_outputs = [path for path in contract["deliverables"] if not path.startswith("trial-output/")]
        if len(public_outputs) != 1 or not public_outputs[0].endswith(".md"):
            raise SmokeFailure("the canonical contract does not disclose one Markdown public deliverable")
        public_rel = public_outputs[0]
        deliverable = copy_root / public_rel
        if deliverable.is_symlink() or not deliverable.is_file():
            raise SmokeFailure("the declared public Markdown deliverable is unavailable")
        original = deliverable.read_text(encoding="utf-8")
        addition = (
            "\n\n## Isolated first-contribution smoke\n\n"
            "This paragraph exists only in the temporary public copy to exercise the offline contribution finish route.\n"
        )
        deliverable.write_text(original.rstrip() + addition, encoding="utf-8")

        final = _record_process(
            processes,
            "final truthfulness and links validators",
            final_command,
            _contract_argv(final_command),
            cwd=copy_root,
            env=env,
        )
        _require_exit(final, "final truthfulness and links validators")

        completion_rel = completion.fixture_deliverable(
            copy_root, TASK_ID, FIXTURE_ID, "completion.json"
        )
        artifact_path = copy_root / completion_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        completion_record = {
            "schema_version": 2,
            "case_id": FIXTURE_ID,
            "route": {
                "selected_task": TASK_ID,
                "offline_contribution_access": "not_required",
                "live_campaign_authorization": live_authorization,
                "authoritative_sources": [guide_rel, onboarding_rel],
            },
            "contribution": {
                "kind": "documentation_addition",
                "changed_paths": public_outputs,
            },
            "validation": {
                "baseline": {
                    "command": baseline_command,
                    "modes": _validation_modes(baseline_command),
                    "exit_code": baseline.returncode,
                    "result": "passed",
                },
                "final": {
                    "command": final_command,
                    "modes": _validation_modes(final_command),
                    "exit_code": final.returncode,
                    "result": "passed",
                },
            },
            "human_review": {
                "summary": "Added a temporary explanatory paragraph to the contract-declared Markdown deliverable in the isolated copy.",
                "rationale": "The change exists only to exercise the selected offline contribution workflow and is discarded with the temporary workspace.",
            },
        }
        artifact_path.write_text(json.dumps(completion_record, indent=2) + "\n", encoding="utf-8")

        diff = _record_process(
            processes,
            "permitted whitespace check",
            diff_command,
            _contract_argv(diff_command),
            cwd=copy_root,
            env=env,
        )
        _require_exit(diff, "permitted whitespace check")

        success = _record_process(
            processes,
            "successful completion checker",
            completion_command,
            _contract_argv(completion_command),
            cwd=copy_root,
            env=env,
        )
        _require_exit(success, "successful completion checker")
        if "Completion check: PASS" not in success.stdout:
            raise SmokeFailure("the canonical completion checker did not report success")

        return {
            "status": "passed",
            "fixture": FIXTURE_ID,
            "copy_is_outside_source": True,
            "source_file_count_copied": len(expected_hashes),
            "workspace_public_selection_count": git_setup["selected_file_count"],
            "workspace_public_selection_sha256": git_setup["selected_paths_sha256"],
            "source_unchanged": _source_unchanged(source, expected_hashes),
            "route_loaded_from_copy": True,
            "task_inputs_loaded_from_copy": True,
            "incomplete_contribution_rejected": True,
            "incomplete_probe": {
                "command": completion_command,
                "exit_code": incomplete.returncode,
                "status": "rejected before the required completion artifact existed",
            },
            "completed_sequence": list(contract["finish_sequence"]),
            "processes": processes,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-tracked-ignore-regression",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    expected_hashes: dict[str, str] = {}
    source = SOURCE_ROOT.resolve()
    source_index_before = _index_sha256(source)
    try:
        helpers = _copy_helper(source)
        result = _run(source, expected_hashes, helpers)
        if not result["source_unchanged"]:
            raise SmokeFailure("a copied public source file changed during the smoke")
        if not args.skip_tracked_ignore_regression:
            regression = _tracked_ignore_regression(source, helpers, _child_environment())
            result["tracked_ignored_regression"] = regression
            if regression["status"] != "passed":
                raise SmokeFailure("tracked-ignore full completion smoke regression failed")
        source_index_after = _index_sha256(source)
        result["source_git_index_sha256_before"] = source_index_before
        result["source_git_index_sha256_after"] = source_index_after
        result["source_git_index_unchanged"] = source_index_before == source_index_after
        if not result["source_git_index_unchanged"]:
            raise SmokeFailure("the source Git index changed during isolated workspace setup")
    except SmokeFailure as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - never print host paths or excluded target details
        print(
            json.dumps({
                "status": "failed",
                "reason": f"isolated first-contribution smoke failed safely ({type(exc).__name__})",
            }, indent=2),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
