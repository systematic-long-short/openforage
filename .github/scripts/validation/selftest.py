"""Negative and positive self-tests on throwaway repository copies.

Each scenario copies the repository to a temporary directory, applies one
mutation, and asserts the matching check behaves as expected. Negative
scenarios must be caught; positive scenarios must pass. This covers the
independent-review regressions directly (resume history, phase-aware protocol
claims, baseline availability, exhaustion edges, lifecycle contradictions,
and registry evidence scope/result/path/date consistency).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import anchors, campaign, compatibility, controls, evals, first_read, guidance, opportunities, registry
from .common import EMBEDDED_PLACEHOLDER_RE, Report

COPY_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "LICENSE")
COPY_DIRS = ("docs", "src", "examples", ".github", "scripts")
IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tmp",
    ".venv",
    ".venv-docs",
)

REGISTRY_REL = "src/openforage/skills/agent_runtime_integration/references/harnesses.json"
REGISTRY_FIXTURE = (
    "src/openforage/skills/agent_runtime_integration/references/evidence/selftest-fixture.json"
)

BASE_OVERRIDES = {
    "campaign_id": "campaign-selftest-20260101",
    "state_machine.ownership.owner_session_identity": "child-selftest",
    "state_machine.ownership.claim_mechanism": "atomic-create",
    "state_machine.ownership.claimed_at": "2026-01-01T00:00:00Z",
    "state_machine.ownership.fencing_token": 1,
    "frozen_baseline.availability": "available",
    "frozen_baseline.id": "frozen-selftest",
    "frozen_baseline.hash": "a" * 64,
    "frozen_baseline.observed_completed_simulations": 0,
    "frozen_baseline.frozen": True,
    "compatible_baseline.status": "created",
    "compatible_baseline.baseline_id": "compat-selftest",
    "compatible_baseline.config_frozen_before_run": True,
    "compatible_baseline.evidence_frozen_after_run": True,
    "preregistration.direction": "increase",
    "preregistration.recorded_at": "2026-01-01T00:00:00Z",
    "preregistration.look_schedule.kind": "fixed_horizon",
    "usefulness_metrics[0].availability": "available",
    "provenance.algorithm.identity": "random_weighted",
    "lifecycle_observations.observed_at": "2026-01-01T00:00:00Z",
    "lifecycle_observations.source": "selftest status fixture",
}

TERMINAL_RESOURCES = {
    "outcome": "completed",
    "terminal_report": {
        "path": "terminal-report.md",
        "pending_outcomes_preserved": True,
        "relaunch_prevented": True,
    },
}


def _copy_repo(root: Path, dest: Path) -> None:
    for name in COPY_FILES:
        source = root / name
        if source.is_file():
            shutil.copy2(source, dest / name)
    for name in COPY_DIRS:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=IGNORE, dirs_exist_ok=True)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _fill_placeholders(obj, overrides: dict[str, object], path: str = ""):
    if isinstance(obj, dict):
        return {
            key: _fill_placeholders(value, overrides, f"{path}.{key}" if path else key)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [
            _fill_placeholders(value, overrides, f"{path}[{index}]")
            for index, value in enumerate(obj)
        ]
    if path in overrides:
        return overrides[path]
    if campaign.is_placeholder(obj):
        return "filled"
    if isinstance(obj, str) and EMBEDDED_PLACEHOLDER_RE.search(obj):
        return EMBEDDED_PLACEHOLDER_RE.sub("filled", obj)
    return obj


def _transition(
    transition_id: str,
    source: str,
    target: str,
    revision: int,
    resume_target: str | None = None,
    owner: str = "child-selftest",
) -> dict:
    record = {
        "transition_id": transition_id,
        "from": source,
        "to": target,
        "timestamp": f"2026-01-{revision:02d}T00:00:00Z",
        "reason": f"{transition_id} selftest",
        "owner_session_identity": owner,
        "owner_revision": revision,
        "guard_passed": True,
    }
    if resume_target is not None:
        record["resume_target"] = resume_target
    return record


def _run_scenario(report, root, name, mutate, check, expect_failure=True) -> bool:
    with tempfile.TemporaryDirectory(prefix=f"of-selftest-{name}-") as tmp:
        copy = Path(tmp) / "repo"
        copy.mkdir()
        _copy_repo(root, copy)
        try:
            mutate(copy)
        except Exception as exc:  # noqa: BLE001
            report.fail(f"selftest {name}: mutation failed: {exc}")
            return False
        probe = Report()
        try:
            check(probe, copy)
        except Exception as exc:  # noqa: BLE001
            report.fail(f"selftest {name}: check raised {type(exc).__name__}: {exc}")
            return False
        if expect_failure:
            if not probe.errors:
                report.fail(f"selftest {name}: expected a failure but the check passed")
                return False
            return True
        if probe.errors:
            report.fail(f"selftest {name}: expected a pass but found: {probe.errors[:3]}")
            return False
        return True


def _campaign_path(copy: Path) -> Path:
    return copy / "examples/agent-campaign/campaign.json"


def _filled_campaign(copy: Path, overrides: dict | None = None) -> dict:
    path = _campaign_path(copy)
    data = _load(path)
    data.pop("_template", None)
    merged = dict(BASE_OVERRIDES)
    merged.update(overrides or {})
    return _fill_placeholders(data, merged)


def _campaign_scaffold(copy: Path, transitions, state: str, revision: int, overrides=None) -> dict:
    data = _filled_campaign(copy, overrides)
    data["transitions"] = transitions
    data["state_machine"]["state"] = state
    data["state_machine"]["revision"] = revision
    data["state_machine"]["ownership"]["owner_revision"] = revision
    data["state_machine"]["resume_target"] = None
    return data


def _mutate_illegal_transition(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-bogus", "baseline", "completed", 2),
        ],
        "research",
        2,
    )
    _save(_campaign_path(copy), data)


def _mutate_stopped_resume(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-baseline-freeze", "baseline", "research", 2),
            _transition("T-stop", "research", "stopped", 3),
        ],
        "stopped",
        3,
    )
    data["state_machine"]["resume_target"] = "research"
    data["resources"]["outcome"] = "stopped"
    data["resources"]["terminal_report"] = {
        "path": "terminal-report.md",
        "pending_outcomes_preserved": True,
        "relaunch_prevented": True,
    }
    _save(_campaign_path(copy), data)


def _mutate_terminal_live_work(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-baseline-freeze", "baseline", "research", 2),
            _transition("T-protocol-open", "research", "experiment", 3),
            _transition("T-horizon-reached", "experiment", "comparison", 4),
            _transition("T-complete", "comparison", "completed", 5),
        ],
        "completed",
        5,
    )
    data["resources"]["outcome"] = TERMINAL_RESOURCES["outcome"]
    data["resources"]["terminal_report"] = dict(TERMINAL_RESOURCES["terminal_report"])
    data["lifecycle_observations"]["worker_running"] = True
    _save(_campaign_path(copy), data)


def _mutate_paused_live_triggers(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold", "baseline", "paused", 2),
        ],
        "paused",
        2,
    )
    data["state_machine"]["resume_target"] = "baseline"
    data["lifecycle_observations"]["triggers_enabled"] = True
    _save(_campaign_path(copy), data)


def _mutate_malformed_inventory(copy: Path) -> None:
    path = copy / "examples/agent-campaign/experiments.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")


def _mutate_missing_onboarding_target(copy: Path) -> None:
    path = copy / "AGENTS.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("docs/agents/first-contribution.md", "docs/agents/not-a-page.md"),
        encoding="utf-8",
    )


def _mutate_generated_onboarding_drift(copy: Path) -> None:
    path = copy / "docs/agents/start-here.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Essential context", "## stale generated route", 1), encoding="utf-8")


def _mutate_stale_algorithm_exclusivity(copy: Path) -> None:
    path = copy / "docs/agents/first-search.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n`random_weighted` is the only stable algorithm template documented.\n")


def _mutate_stale_search_settings_algorithm(copy: Path) -> None:
    path = copy / "src/openforage/skills/search_settings/SKILL.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n`random_weighted` is the only documented algorithm and the CLI rejects other strings.\n")


def _check_stale_search_settings_algorithm(report: Report, root: Path) -> None:
    validator = root / ".github/scripts/validate_agent_docs.py"
    result = subprocess.run(
        [sys.executable, str(validator), "truthfulness", "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 and "stale algorithm exclusivity claim" in output:
        report.fail("truthfulness rejected the stale search-settings algorithm claim")


def _mutate_missing_entry_session_boundary(copy: Path) -> None:
    path = copy / "docs/agents/quickstart.md"
    text = path.read_text(encoding="utf-8")
    begin = "<!-- BEGIN GENERATED ENTRY GUIDANCE -->"
    end = "<!-- END GENERATED ENTRY GUIDANCE -->"
    start = text.index(begin)
    finish = text.index(end) + len(end)
    fragment = text[start:finish]
    fragment = fragment.replace(
        "Continuous search requires an independently started CLI process of the same harness with a distinct session identity. If that identity is not established, keep work offline; never use the main conversation.",
        "Continuous signal searches may continue in the current process.",
    )
    fragment = fragment.replace(
        "Python search snippets below are for that child only.",
        "Python search snippets may run in this process.",
    )
    path.write_text(text[:start] + fragment + text[finish:], encoding="utf-8")


def _mutate_offline_registration_gate(copy: Path) -> None:
    path = copy / "docs/agents/install-authenticate.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "Reading, research, and contributions need no invite, wallet, installed package, daemon, or worker.",
        "Registration and an invite are required before offline research or documentation work.",
    )
    path.write_text(text, encoding="utf-8")


def _mutate_onboarding_default_route(copy: Path) -> None:
    path = copy / "docs/agents/references/onboarding.json"
    data = _load(path)
    data["default_task"] = "contribute"
    _save(path, data)


def _mutate_main_session_search_fallback(copy: Path) -> None:
    path = copy / "docs/agents/references/onboarding.json"
    data = _load(path)
    data["runtime_rules"]["continuous_search"] = (
        "When a separate session is not available, continue searching in the main session."
    )
    _save(path, data)


def _mutate_automatic_campaign_launch(copy: Path) -> None:
    path = copy / "docs/agents/references/onboarding.json"
    data = _load(path)
    data["access_rule"] = data["access_rule"].replace(
        "Never register, start a campaign, or start a session automatically.",
        "Register, start a campaign, and start a session automatically.",
    )
    _save(path, data)


def _mutate_deleted_protocol_field(copy: Path) -> None:
    path = _campaign_path(copy)
    data = _load(path)
    del data["preregistration"]["primary_metric"]
    _save(path, data)


def _mutate_excluded_control_field(copy: Path) -> None:
    path = _campaign_path(copy)
    data = _load(path)
    data["max_spend_usd"] = 100
    _save(path, data)


def _mutate_auth_safety_wording(copy: Path) -> None:
    path = _campaign_path(copy)
    data = _load(path)
    data["_note"] = (
        "Never paste the JWT or wallet.key; keep the bearer token in the "
        "runtime secret store."
    )
    _save(path, data)


def _mutate_broken_anchor(copy: Path) -> None:
    path = copy / "README.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "](/docs/agents/first-contribution.md)",
            "](/docs/agents/first-contribution.md#does-not-exist)",
            1,
        ),
        encoding="utf-8",
    )


def _isolated_git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    return env


def _run_test_git(args: list[str], cwd: Path) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, env=_isolated_git_environment(),
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _snapshot_copy_function(root: Path):
    helper_path = root / "examples/fresh-agent-evals/v2/snapshot.py"
    if not helper_path.is_file() or helper_path.is_symlink():
        raise RuntimeError("copy helper is unavailable")
    v2_dir = str(helper_path.parent)
    sys.path.insert(0, v2_dir)
    try:
        from snapshot import copy_public_tree
    finally:
        try:
            sys.path.remove(v2_dir)
        except ValueError:
            pass
    return copy_public_tree


def _link_probe(root: Path, report, name: str, expect_failure: bool) -> bool:
    probe = Report()
    try:
        anchors.check_links(probe, root)
    except Exception as exc:  # noqa: BLE001 - keep test failures path-safe
        report.fail(f"selftest {name}: link check raised {type(exc).__name__}")
        return False
    if bool(probe.errors) != expect_failure:
        outcome = "fail" if expect_failure else "pass"
        details = "; ".join(probe.errors[:3])
        report.fail(f"selftest {name}: expected link check to {outcome}; observed {details or 'no diagnostics'}")
        return False
    diagnostics = " ".join(probe.errors)
    if "OUTSIDE_SECRET_SENTINEL" in diagnostics or "/tmp/" in diagnostics:
        report.fail(f"selftest {name}: diagnostic disclosed excluded target information")
        return False
    return True


def run_link_portability_selftests(report, root: Path) -> None:
    """Exercise ignore, copy, URL, traversal, and symlink boundaries cheaply."""
    if shutil.which("git") is None:
        report.fail("selftest portable_links: Git is required to verify real ignore semantics")
        return

    with tempfile.TemporaryDirectory(prefix="of-portable-links-") as temporary:
        base = Path(temporary)
        parent = base / "ancestor-repository"
        source = parent / "public-source"
        source.mkdir(parents=True)
        (parent / ".gitignore").write_text("ancestor-blocked.md\n", encoding="utf-8")
        if not _run_test_git(["init", "--quiet"], parent):
            report.fail("selftest portable_links: isolated ancestor fixture setup failed")
            return

        (source / ".gitignore").write_text(
            "*.ignored\n"
            "custom-cache/\n"
            "!custom-cache/\n"
            "custom-cache/*\n"
            "!custom-cache/public/\n"
            "custom-cache/public/*\n"
            "!custom-cache/public/keep.md\n",
            encoding="utf-8",
        )
        (source / "docs").mkdir()
        (source / "examples/fresh-agent-evals/v2").mkdir(parents=True)
        (source / "custom-cache/public").mkdir(parents=True)
        (source / "custom-cache/hidden.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / "custom-cache/public/keep.md").write_text("# Kept\n", encoding="utf-8")
        (source / "custom-cache/public/drop.md").write_text("# Dropped\n", encoding="utf-8")
        (source / "ancestor-blocked.md").write_text("# Local repository root\n", encoding="utf-8")
        (source / "guide.md").write_text("# Guide\n", encoding="utf-8")
        (source / "encoded name.md").write_text("# Encoded\n", encoding="utf-8")
        (source / "angle name.md").write_text("# Angle\n", encoding="utf-8")
        (source / "examples/fresh-agent-evals/v2/README.md").write_text("# Archive\n", encoding="utf-8")
        (source / "candidate-new.md").write_text("# Untracked public candidate\n", encoding="utf-8")
        (source / ".env.example").write_text("# Environment example\n", encoding="utf-8")
        (source / ".tmp").mkdir()
        (source / ".tmp/local.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / ".venv").mkdir()
        (source / ".venv/runtime.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / ".config").mkdir()
        (source / ".config/local.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / ".openforage-state").mkdir()
        (source / ".openforage-state/runtime.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / ".secrets").mkdir()
        (source / ".secrets/token.md").write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        for name in (".env", ".env.local", "wallet.key", "credentials.json", "id_rsa", "private.pem"):
            (source / name).write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")

        index = source / "docs/index.md"
        index.write_text(
            "# Portable links\n\n"
            "[ordinary](../guide.md#guide)\n\n"
            "[encoded](../encoded%20name.md#encoded)\n\n"
            "[angle](<../angle name.md#angle>)\n\n"
            "[nested negation](../custom-cache/public/keep.md#kept)\n\n"
            "[archive](../examples/fresh-agent-evals/v2/README.md#archive)\n\n"
            "[ancestor rule](../ancestor-blocked.md#local-repository-root)\n\n"
            "[environment example](../.env.example#environment-example)\n\n"
            "[web](https://example.invalid/path) [mail](mailto:review@example.invalid)\n",
            encoding="utf-8",
        )

        behaviors = 0
        if _link_probe(source, report, "portable_encoded_angle_archive_and_ancestor_links", False):
            behaviors += 1

        try:
            copy_public_tree = _snapshot_copy_function(root)
            public_copy = base / "public-copy-no-git"
            hashes = copy_public_tree(source, public_copy)
            expected_present = (
                "candidate-new.md",
                "ancestor-blocked.md",
                "custom-cache/public/keep.md",
                ".gitignore",
                ".env.example",
            )
            expected_absent = (
                ".tmp/local.md",
                ".venv/runtime.md",
                ".config/local.md",
                ".openforage-state/runtime.md",
                ".secrets/token.md",
                ".env",
                ".env.local",
                "wallet.key",
                "credentials.json",
                "id_rsa",
                "private.pem",
                "custom-cache/hidden.md",
                "custom-cache/public/drop.md",
            )
            missing = [path for path in expected_present if not (public_copy / path).is_file()]
            leaked = [path for path in expected_absent if (public_copy / path).exists()]
            if missing or leaked or (public_copy / ".git").exists() or not hashes:
                report.fail(
                    "selftest portable_copy: public-copy membership mismatch "
                    f"(missing={missing}, excluded_present={leaked})"
                )
            else:
                behaviors += 1
        except Exception as exc:  # noqa: BLE001 - never echo host paths or excluded content
            report.fail(f"selftest portable_copy: copy helper raised {type(exc).__name__}")

        index.write_text(
            index.read_text(encoding="utf-8")
            + "\n[ignored custom target](../custom-cache/hidden.md)\n",
            encoding="utf-8",
        )
        if _link_probe(source, report, "custom_ignored_directory", True):
            behaviors += 1

        blocked_targets = [
            "../.tmp/local.md",
            "../.venv/runtime.md",
            "../.config/local.md",
            "../.openforage-state/runtime.md",
            "../.secrets/token.md",
            "../.env",
            "../.env.local",
            "../wallet.key",
            "../credentials.json",
            "../id_rsa",
            "../private.pem",
            "../custom-cache/public/drop.md",
        ]
        index.write_text(
            "# Excluded target checks\n\n"
            + "\n".join(f"[blocked {position}]({path})" for position, path in enumerate(blocked_targets))
            + "\n",
            encoding="utf-8",
        )
        if _link_probe(source, report, "existing_scratch_runtime_and_credential_targets", True):
            behaviors += 1

        outside = base / "outside-secret.md"
        outside.write_text("OUTSIDE_SECRET_SENTINEL\n", encoding="utf-8")
        (source / "docs/index.md").write_text(
            "# Outside traversal\n\n[escape](../../outside-secret.md)\n", encoding="utf-8"
        )
        if _link_probe(source, report, "parent_traversal_outside_root", True):
            behaviors += 1

        try:
            (source / "escape").symlink_to(base, target_is_directory=True)
            (source / "docs/index.md").write_text(
                "# Symlink escape\n\n[escape](../escape/outside-secret.md)\n", encoding="utf-8"
            )
            if _link_probe(source, report, "symlink_escape", True):
                behaviors += 1
            (source / "docs/loop.md").symlink_to(source / "docs/loop.md")
            (source / "docs/index.md").write_text(
                "# Symlink loop\n\n[loop](loop.md)\n", encoding="utf-8"
            )
            if _link_probe(source, report, "symlink_loop", True):
                behaviors += 1
        except OSError:
            report.fail("selftest portable_links: symlink fixture setup is unavailable")

        for symlink in (source / "escape", source / "docs/loop.md"):
            try:
                if symlink.is_symlink():
                    symlink.unlink()
            except OSError:
                pass

        if not _run_test_git(["init", "--quiet"], source):
            report.fail("selftest tracked_ignore: isolated work-tree setup failed")
        else:
            (source / "tracked.ignored").write_text("# Tracked ignored-name file\n", encoding="utf-8")
            (source / ".git/info/exclude").write_text("ancestor-blocked.md\n", encoding="utf-8")
            if not _run_test_git(["add", "--force", "tracked.ignored"], source):
                report.fail("selftest tracked_ignore: ignored-name fixture could not be indexed")
            else:
                index.write_text(
                    "# Versioned and local-exclude portability\n\n"
                    "[tracked ignored-name](../tracked.ignored)\n\n"
                    "[local exclude is not repository ignore](../ancestor-blocked.md#local-repository-root)\n",
                    encoding="utf-8",
                )
                if _link_probe(source, report, "tracked_ignored_name_and_isolated_local_config", False):
                    behaviors += 1
                try:
                    tracked_copy = base / "public-copy-with-index"
                    copy_public_tree(source, tracked_copy)
                    if not (tracked_copy / "tracked.ignored").is_file():
                        report.fail("selftest tracked_ignore: versioned ignored-name file was excluded from the public copy")
                    else:
                        behaviors += 1
                except Exception as exc:  # noqa: BLE001 - path-safe diagnostic
                    report.fail(f"selftest tracked_ignore: public copy raised {type(exc).__name__}")

        report.note(f"selftest portable_links: {behaviors}/9 portable path and copy behaviors passed")


def _mutate_missing_state_machine(copy: Path) -> None:
    (copy / "docs/agents/references/campaign-state-machine.json").unlink()


def _mutate_filled_measured_values(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    _save(_campaign_path(copy), data)


# --- resume/history regressions -------------------------------------------


def _mutate_valid_initial_bootstrapping(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [],
        "bootstrapping",
        0,
        overrides={
            "frozen_baseline.availability": "unavailable",
            "frozen_baseline.unavailable_reason": "no frozen reference recorded yet",
            "frozen_baseline.id": None,
            "frozen_baseline.hash": None,
            "frozen_baseline.observed_completed_simulations": None,
            "frozen_baseline.frozen": False,
            "compatible_baseline.status": "missing",
            "compatible_baseline.status_reason": "no compatible baseline exists yet",
            "compatible_baseline.baseline_id": None,
            "compatible_baseline.config_frozen_before_run": None,
            "compatible_baseline.evidence_frozen_after_run": None,
        },
    )
    _save(_campaign_path(copy), data)


def _mutate_valid_resume_history(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold", "baseline", "paused", 2, resume_target="baseline"),
            _transition("T-resume", "paused", "baseline", 3),
        ],
        "baseline",
        3,
    )
    _save(_campaign_path(copy), data)


def _mutate_valid_multi_hold(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold-1", "baseline", "paused", 2),
            _transition("T-resume-1", "paused", "baseline", 3),
            _transition("T-baseline-freeze", "baseline", "research", 4),
            _transition("T-hold-2", "research", "paused", 5),
            _transition("T-resume-2", "paused", "research", 6),
            _transition("T-input-needed", "research", "needs-input", 7),
            _transition("T-resume-3", "needs-input", "research", 8),
        ],
        "research",
        8,
    )
    _save(_campaign_path(copy), data)


def _mutate_valid_exploratory_comparison(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-research-start", "bootstrapping", "research", 1),
            _transition("T-exploratory-compare", "research", "comparison", 2),
        ],
        "comparison",
        2,
        overrides={
            "preregistration.recorded_at": None,
            "preregistration.protocol_hash": None,
            "preregistration.hypothesis": None,
            "preregistration.primary_metric": None,
            "preregistration.comparator": None,
            "preregistration.direction": "unknown",
        },
    )
    data["preregistration"]["unavailable_reasons"] = {
        "recorded_at": "protocol was not registered; exploratory comparison",
        "protocol_hash": "no protocol artifact exists",
        "hypothesis": "hypothesis changed after results were seen",
        "primary_metric": "exploratory metric selection",
        "comparator": "no registered comparator",
    }
    _save(_campaign_path(copy), data)


def _mutate_valid_exhaustion_immediate(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-exhausted", "bootstrapping", "exhausted", 1)],
        "exhausted",
        1,
    )
    data["resources"]["outcome"] = "exhausted"
    data["resources"]["terminal_report"] = {
        "path": "terminal-report.md",
        "pending_outcomes_preserved": True,
        "relaunch_prevented": True,
    }
    _save(_campaign_path(copy), data)


def _mutate_same_revision(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold", "baseline", "paused", 1),
        ],
        "paused",
        1,
    )
    data["state_machine"]["resume_target"] = "baseline"
    _save(_campaign_path(copy), data)


def _mutate_wrong_resume_target(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold", "baseline", "paused", 2),
            _transition("T-resume", "paused", "research", 3),
        ],
        "research",
        3,
    )
    _save(_campaign_path(copy), data)


def _mutate_manifest_resume_mismatch(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-hold", "baseline", "paused", 2),
        ],
        "paused",
        2,
    )
    data["state_machine"]["resume_target"] = "research"
    _save(_campaign_path(copy), data)


def _mutate_owner_mismatch(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-baseline-freeze", "baseline", "research", 2, owner="intruder"),
        ],
        "research",
        2,
    )
    _save(_campaign_path(copy), data)


def _mutate_revision_type(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            {
                **_transition("T-baseline-freeze", "baseline", "research", 2),
                "owner_revision": True,
            },
        ],
        "research",
        2,
    )
    _save(_campaign_path(copy), data)


def _mutate_initial_empty_history_bad_state(copy: Path) -> None:
    data = _campaign_scaffold(copy, [], "baseline", 0)
    _save(_campaign_path(copy), data)


# --- phase-aware completion/protocol regressions --------------------------


def _mutate_frozen_baseline_unsupported(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["frozen_baseline"]["id"] = None
    data["frozen_baseline"]["hash"] = None
    _save(_campaign_path(copy), data)


def _mutate_compatible_baseline_unfrozen(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["compatible_baseline"]["config_frozen_before_run"] = False
    _save(_campaign_path(copy), data)


def _mutate_enforced_without_mechanism(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["resources"]["limits"][0]["enforced"] = 4.0
    _save(_campaign_path(copy), data)


def _mutate_negative_resource_value(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["resources"]["limits"][0]["requested"] = -1
    _save(_campaign_path(copy), data)


def _mutate_unknown_resource_kind(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["resources"]["limits"][0]["resource"] = "gpu_count"
    _save(_campaign_path(copy), data)


def _mutate_terminal_relaunch_false(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-baseline-freeze", "baseline", "research", 2),
            _transition("T-protocol-open", "research", "experiment", 3),
            _transition("T-horizon-reached", "experiment", "comparison", 4),
            _transition("T-complete", "comparison", "completed", 5),
        ],
        "completed",
        5,
    )
    data["resources"]["outcome"] = "completed"
    data["resources"]["terminal_report"] = {
        "path": "terminal-report.md",
        "pending_outcomes_preserved": True,
        "relaunch_prevented": False,
    }
    _save(_campaign_path(copy), data)


def _mutate_embedded_placeholder(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["campaign_id"] = "campaign-<slug>-20260101"
    _save(_campaign_path(copy), data)


def _mutate_provenance_null_object(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["provenance"]["repo"] = None
    _save(_campaign_path(copy), data)


def _mutate_registered_prereg_missing(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "experiment",
        1,
    )
    data["statistics"]["classification"] = "measured"
    data["preregistration"]["protocol_hash"] = None
    _save(_campaign_path(copy), data)


def _mutate_complete_inventory_template_rows(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [
            _transition("T-baseline-start", "bootstrapping", "baseline", 1),
            _transition("T-baseline-freeze", "baseline", "research", 2),
            _transition("T-protocol-open", "research", "experiment", 3),
            _transition("T-horizon-reached", "experiment", "comparison", 4),
        ],
        "comparison",
        4,
    )
    data["statistics"]["classification"] = "measured"
    data["statistics"]["complete_trial_inventory"] = True
    _save(_campaign_path(copy), data)


def _real_trial_rows() -> list[dict]:
    rows = []
    for index, (status, classification) in enumerate(
        (("completed", "measured"), ("abandoned", "exploratory")), start=1
    ):
        rows.append(
            {
                "_template": False,
                "record_type": "experiment_trial",
                "trial_id": f"trial-{index:04d}",
                "experiment_id": "exp-selftest",
                "campaign_id": "campaign-selftest-20260101",
                "owner_session_identity": "child-selftest",
                "status": status,
                "classification": classification,
                "unit_of_analysis": "independent_paired_search_runs",
                "depth_cap": 6,
            }
        )
    return rows


def _mutate_real_completed_evidence(copy: Path) -> None:
    evidence_dir = copy / "examples/agent-campaign/evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "completion-selftest.json").write_text(
        json.dumps(
            {
                "reference_id": "frozen-selftest",
                "target_completed_simulations": 100000,
                "observed_completed_simulations": 100000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence_dir / "adapter-selftest.json").write_text("{}\n", encoding="utf-8")
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data.pop("_template", None)
    overrides = {
        "campaign_id": "campaign-selftest-20260101",
        "owner_session_identity": "child-selftest",
        "statistics.horizon": "fixed_horizon",
        "comparison.classification": "measured",
        "usefulness_metrics[0].availability": "available",
        "provenance.repo.commit": "d" * 40,
        "provenance.package.version": "0.2.36",
        "provenance.environment.python": "3.12.0",
        "provenance.environment.platform": "linux-x86_64",
        "provenance.harness.product": "Selftest CLI",
        "provenance.data_scope.era_id": "era-selftest",
        "provenance.data_scope.config_hash": "c" * 64,
        "provenance.artifacts[0].path": "examples/agent-campaign/README.md",
        "provenance.artifacts[0].sha256": "b" * 64,
        "honesty.full_benchmark_completion_claimed": True,
        "honesty.verified_harness_adapter_claimed": True,
    }
    data = _fill_placeholders(data, overrides)
    data["honesty"]["full_benchmark_completion_evidence"] = {
        "evidence_artifact": "examples/agent-campaign/evidence/completion-selftest.json",
        "reference_id": "frozen-selftest",
        "target_completed_simulations": 100000,
        "completed_evaluations": 100000,
        "completed_at": "2026-01-02",
    }
    data["honesty"]["harness_adapter_evidence"] = {
        "product": "Selftest CLI",
        "product_version": "1.0.0",
        "test_id": "selftest-adapter-1",
        "tested_at": "2026-01-03",
        "scope": {"surface": "CLI", "os": "linux", "lifecycle": "pause"},
        "observed_result": "passed the bounded marker fixture and exited cleanly",
        "evidence_artifact": "examples/agent-campaign/evidence/adapter-selftest.json",
    }
    _save(path, data)
    inventory = copy / "examples/agent-campaign/experiments.jsonl"
    inventory.write_text(
        "\n".join(json.dumps(row) for row in _real_trial_rows()) + "\n",
        encoding="utf-8",
    )


def _rewrite_completion_evidence(copy: Path, field: str, value) -> None:
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data["honesty"]["full_benchmark_completion_evidence"][field] = value
    _save(path, data)


def _mutate_completion_claim_zero_count(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    _rewrite_completion_evidence(copy, "completed_evaluations", 0)


def _mutate_completion_claim_partial_count(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    _rewrite_completion_evidence(copy, "completed_evaluations", 50000)


def _mutate_completion_claim_fractional_count(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    _rewrite_completion_evidence(copy, "completed_evaluations", 100000.5)


def _mutate_completion_claim_unstructured_artifact(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    _rewrite_completion_evidence(
        copy, "evidence_artifact", "examples/agent-campaign/README.md"
    )


def _mutate_completion_claim_mismatched_evidence(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    artifact = copy / "examples/agent-campaign/evidence/completion-selftest.json"
    artifact.write_text(
        json.dumps(
            {
                "reference_id": "frozen-selftest",
                "target_completed_simulations": 100000,
                "observed_completed_simulations": 99999,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _mutate_completion_claim_without_evidence(copy: Path) -> None:
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data.pop("_template", None)
    data["honesty"]["full_benchmark_completion_claimed"] = True
    _save(path, data)


def _mutate_adapter_claim_without_evidence(copy: Path) -> None:
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data.pop("_template", None)
    data["honesty"]["verified_harness_adapter_claimed"] = True
    _save(path, data)


def _mutate_adapter_claim_failed_result(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data["honesty"]["harness_adapter_evidence"]["observed_result"] = "failed: fixture crashed"
    _save(path, data)


def _mutate_adapter_claim_banana_result(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    data["honesty"]["harness_adapter_evidence"]["observed_result"] = "banana"
    _save(path, data)


def _mutate_real_adapter_typed_outcome(copy: Path) -> None:
    _mutate_real_completed_evidence(copy)
    path = copy / "examples/agent-campaign/experiment_record.json"
    data = _load(path)
    evidence = data["honesty"]["harness_adapter_evidence"]
    evidence["observed_result"] = "fixture output attached"
    evidence["outcome"] = "passed"
    _save(path, data)


def _mutate_transition_target_list(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["transitions"][0]["to"] = []
    _save(_campaign_path(copy), data)


def _mutate_transition_source_list(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["transitions"][0]["from"] = []
    _save(_campaign_path(copy), data)


def _mutate_resource_kind_list(copy: Path) -> None:
    data = _campaign_scaffold(
        copy,
        [_transition("T-baseline-start", "bootstrapping", "baseline", 1)],
        "baseline",
        1,
    )
    data["resources"]["limits"][0]["resource"] = []
    _save(_campaign_path(copy), data)


# --- independent re-verification regression --------------------------------


def _mutate_real_independent_reverification(copy: Path) -> None:
    evidence_dir = copy / "examples/agent-campaign/evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "reverification-selftest.json").write_text(
        json.dumps(
            {
                "baseline_id": "frozen-selftest",
                "observed_completed_simulations": 100000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    path = copy / "examples/agent-campaign/benchmark_record.json"
    data = _load(path)
    data.pop("_template", None)
    overrides = {
        "availability": "available",
        "unavailable_reason": None,
        "baseline_id": "frozen-selftest",
        "hash": "a" * 64,
        "observed_completed_simulations": 100000,
        "frozen": True,
        "recorded_algorithm": "random_weighted",
        "compatible_baseline_creation.status": "created",
        "compatible_baseline_creation.status_reason": "created for selftest",
        "independently_reverified": True,
    }
    data = _fill_placeholders(data, overrides)
    data["independent_reverification_evidence"] = {
        "reverifier": "independent-selftest",
        "reverified_at": "2026-01-06",
        "evidence_artifact": "examples/agent-campaign/evidence/reverification-selftest.json",
    }
    _save(path, data)


def _mutate_independent_reverification_missing_evidence(copy: Path) -> None:
    _mutate_real_independent_reverification(copy)
    path = copy / "examples/agent-campaign/benchmark_record.json"
    data = _load(path)
    del data["independent_reverification_evidence"]
    _save(path, data)


def _mutate_independent_reverification_mismatch(copy: Path) -> None:
    _mutate_real_independent_reverification(copy)
    artifact = copy / "examples/agent-campaign/evidence/reverification-selftest.json"
    artifact.write_text(
        json.dumps(
            {
                "baseline_id": "some-other-reference",
                "observed_completed_simulations": 100000,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _mutate_independent_reverification_template_claim(copy: Path) -> None:
    path = copy / "examples/agent-campaign/benchmark_record.json"
    data = _load(path)
    data["independently_reverified"] = True
    _save(path, data)


def _mutate_verified_adapter_missing_evidence(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [{"capability": "pause", "status": "verified"}]
    _save(path, data)


# --- registry regressions --------------------------------------------------


def _registry_path(copy: Path) -> Path:
    return copy / REGISTRY_REL


def _write_registry_fixture(copy: Path) -> None:
    path = copy / REGISTRY_FIXTURE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fixture": "bounded marker"}\n', encoding="utf-8")


def _cap(capability: str, **overrides):
    item = {
        "record_id": f"selftest-fixture:{capability}",
        "capability": capability,
        "status": "verified",
        "mechanism": "harness-cli",
        "probe": "selftest-bounded-marker",
        "evidence_artifact": REGISTRY_FIXTURE,
        "test_id": "selftest-fixture-1",
        "tested_at": "2026-01-05",
        "product_version": "1.2.3",
        "scope": {
            "surface": "CLI",
            "os": "linux",
            "profile": "isolated",
            "lifecycle": "spawn-pause-resume-stop",
            "evidence_scope": "throwaway bounded selftest fixture",
        },
        "assertions": [],
        "observed_result": "passed: marker echo and clean exit observed",
    }
    item.update(overrides)
    return item


def _mutate_verified_registry_fixture(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["status"] = "verified"
    data[0]["verified_on"] = "2026-01-05"
    data[0]["capability_records"] = [
        _cap(capability) for capability in sorted(registry.REQUIRED_CAPABILITIES)
    ]
    _save(path, data)


def _mutate_partial_capability_evidence(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause")]
    _save(path, data)


def _mutate_verified_registry_failed_result(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause", observed_result="failed: marker absent")]
    _save(path, data)


def _mutate_verified_registry_scope_string(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause", scope="CLI")]  # type: ignore[arg-type]
    _save(path, data)


def _mutate_verified_registry_bad_date(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause", tested_at="2026-99-99")]
    _save(path, data)


def _mutate_verified_registry_outside_path(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [
        _cap("pause", evidence_artifact="../../../../etc/passwd")
    ]
    _save(path, data)


def _mutate_verified_registry_version_mismatch(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["status"] = "verified"
    data[0]["verified_on"] = "2026-01-05"
    caps = [_cap(capability) for capability in sorted(registry.REQUIRED_CAPABILITIES)]
    caps[0] = _cap(caps[0]["capability"], product_version="9.9.9")
    data[0]["capability_records"] = caps
    _save(path, data)


def _mutate_verified_registry_duplicate_capability(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause"), _cap("pause")]
    _save(path, data)


def _mutate_verified_registry_unknown_capability(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("telepathy")]
    _save(path, data)


def _mutate_verified_registry_banana_result(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [_cap("pause", observed_result="banana")]
    _save(path, data)


def _mutate_verified_registry_typed_outcome(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [
        _cap("pause", observed_result="fixture output attached", outcome="passed")
    ]
    _save(path, data)


def _mutate_verified_registry_lifecycle_mismatch(copy: Path) -> None:
    _write_registry_fixture(copy)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["status"] = "verified"
    data[0]["verified_on"] = "2026-01-05"
    caps = [_cap(capability) for capability in sorted(registry.REQUIRED_CAPABILITIES)]
    caps[0] = _cap(
        caps[0]["capability"],
        scope={"surface": "CLI", "os": "linux", "lifecycle": "desktop-only"},
    )
    data[0]["capability_records"] = caps
    _save(path, data)


def _mutate_registry_status_list(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    data[0]["status"] = []
    _save(path, data)


def _mutate_registry_capability_status_list(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [
        {"capability": "pause", "status": []},
    ]
    _save(path, data)


def _background_capability_record(data: list[dict], product: str, capability: str) -> dict:
    entry = next(item for item in data if item.get("product") == product)
    return next(
        item
        for item in entry.get("capability_records", [])
        if item.get("test_id", "").startswith("background-proof-")
        and item.get("capability") == capability
    )


def _mutate_typed_assertion_operators(copy: Path) -> None:
    _write_registry_fixture(copy)
    evidence = {
        "equals_value": {"flag": 1},
        "equals_path_left": {"flag": True},
        "equals_path_right": {"flag": 1},
        "contains_values": [{"flag": 1}],
        "all_items": [{"flag": 1}],
    }
    _save(copy / REGISTRY_FIXTURE, evidence)
    path = _registry_path(copy)
    data = _load(path)
    data[0]["capability_records"] = [
        _cap(
            "pause",
            assertions=[
                {"path": "equals_value", "equals": {"flag": True}},
                {
                    "path": "equals_path_left",
                    "equals_path": "equals_path_right",
                },
                {"path": "contains_values", "contains": {"flag": True}},
                {"path": "all_items", "all_items_match": {"flag": True}},
            ],
        )
    ]
    _save(path, data)


def _check_typed_assertion_operators(report, copy: Path) -> None:
    entry = _load(_registry_path(copy))[0]
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        required_paths = {
            "equals_value",
            "equals_path_left",
            "contains_values",
            "all_items",
        }
        missing = [
            path
            for path in required_paths
            if not any(
                f"evidence at {path!r} contradicts" in problem
                for problem in exc.problems
            )
        ]
        if missing:
            report.fail(f"typed JSON assertion operators missed bool/int mismatches at {missing}")
    else:
        report.fail("typed JSON assertion operators accepted bool/int mismatches")


def _mutate_typed_required_assertion_membership(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "OpenCode", "separate-cli-invocation")
    for assertion in record.get("assertions", []):
        if assertion.get("path") == "child.distinct_from_parent":
            assertion["equals"] = 1
            break
    _save(path, data)


def _check_typed_required_assertion_membership(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("lacks required proof assertion" in problem for problem in exc.problems):
            report.fail("required assertion membership did not distinguish boolean from integer")
    else:
        report.fail("required assertion membership accepted boolean/integer aliasing")


def _mutate_relocated_false_required_observation(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "OpenCode", "separate-cli-invocation")
    proof_path = copy / record["evidence_artifact"]
    proof = _load(proof_path)
    proof["child"]["distinct_from_parent"] = False
    relocated = copy / "examples/background-agent/future-wave/proof.json"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    _save(relocated, proof)
    record["evidence_artifact"] = "examples/background-agent/future-wave/proof.json"
    record["assertions"] = [
        assertion
        for assertion in record.get("assertions", [])
        if assertion.get("path") != "child.distinct_from_parent"
    ]
    _save(path, data)


def _check_relocated_false_required_observation(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("lacks required proof assertion" in problem for problem in exc.problems):
            report.fail("relocated proof bypassed its required capability assertions")
    else:
        report.fail("relocated proof with a false required observation was accepted")


def _mutate_background_proof_missing_class_and_schema(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "OpenCode", "separate-cli-invocation")
    proof_path = copy / record["evidence_artifact"]
    proof = _load(proof_path)
    proof.pop("evidence_class", None)
    proof.pop("schema_version", None)
    proof["child"]["distinct_from_parent"] = False
    _save(proof_path, proof)
    record["assertions"] = []
    _save(path, data)


def _check_background_proof_missing_class_and_schema(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any(
            "versioned background proof requires explicit recognized evidence_class and schema_version" in problem
            for problem in exc.problems
        ):
            report.fail("missing proof class/schema did not fail through the explicit background-proof contract")
    else:
        report.fail("background proof without evidence_class/schema fell back to generic legacy checks")


def _mutate_background_proof_boolean_integer(copy: Path) -> None:
    path = copy / "examples/background-agent/native-contribution-wave-20260924/opencode-proof.json"
    proof = _load(path)
    proof["child"]["distinct_from_parent"] = 1
    _save(path, proof)


def _check_background_proof_boolean_integer(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any(
            "evidence at 'child.distinct_from_parent' contradicts" in problem
            for problem in exc.problems
        ):
            report.fail("background proof did not report the typed boolean/integer contradiction")
    else:
        report.fail("background proof accepted integer 1 as boolean true")


def _mutate_background_proof_malformed_adapter_profile(copy: Path) -> None:
    path = copy / "examples/background-agent/native-contribution-wave-20260924/opencode-proof.json"
    proof = _load(path)
    proof["adapter_profile"] = []
    _save(path, proof)


def _check_background_proof_malformed_adapter_profile(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("adapter_profile must be an object" in problem for problem in exc.problems):
            report.fail("malformed adapter_profile was not reported as a projection problem")
    else:
        report.fail("malformed adapter_profile did not raise CapabilityProjectionError")


def _mutate_background_proof_malformed_lifecycle(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "OpenCode", "separate-cli-invocation")
    record["scope"]["lifecycle"] = []
    _save(path, data)


def _check_background_proof_malformed_lifecycle(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("scope.lifecycle is not supported" in problem for problem in exc.problems):
            report.fail("malformed lifecycle was not reported as a projection problem")
    else:
        report.fail("malformed lifecycle did not raise CapabilityProjectionError")


def _mutate_unrecognized_proof_class(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "OpenCode", "separate-cli-invocation")
    proof = {
        "evidence_class": "future-unrecognized-proof",
        "schema_version": 1,
        "passed": True,
        "failures": [],
    }
    relocated = copy / "examples/background-agent/future-wave/unrecognized.json"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    _save(relocated, proof)
    record["evidence_artifact"] = "examples/background-agent/future-wave/unrecognized.json"
    record["probe"] = "future-unrecognized-proof"
    record["assertions"] = [{"path": "passed", "equals": True}]
    _save(path, data)


def _check_unrecognized_proof_class(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("unrecognized evidence class/schema" in problem for problem in exc.problems):
            report.fail("unrecognized proof class was not rejected explicitly")
    else:
        report.fail("generic passing text promoted an unrecognized proof class")


def _mutate_unknown_evidence_contract(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    entry = next(item for item in data if item.get("product") == "OpenCode")
    entry["evidence_contracts"][0]["contract_id"] = "future.adapter-proof.v1"
    _save(path, data)


def _check_unknown_evidence_contract(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "OpenCode"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("unrecognized evidence contract" in problem for problem in exc.problems):
            report.fail("unknown evidence contract was not rejected explicitly")
    else:
        report.fail("unknown evidence contract was accepted")


def _mutate_unknown_product_evidence_contract(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    entry = next(item for item in data if item.get("product") == "OpenCode")
    entry["product"] = "Unregistered Harness"
    _save(path, data)


def _check_unknown_product_evidence_contract(report, copy: Path) -> None:
    entry = next(
        item for item in _load(_registry_path(copy)) if item.get("product") == "Unregistered Harness"
    )
    try:
        registry.project_capability_records(copy, entry)
    except registry.CapabilityProjectionError as exc:
        if not any("no registered evidence contract for product" in problem for problem in exc.problems):
            report.fail("unknown product identity was not rejected explicitly")
    else:
        report.fail("unknown product inherited another adapter's evidence contract")


def _mutate_background_proof_scope_mismatch(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    _background_capability_record(data, "OpenCode", "parent-independence")["scope"]["profile"] = "native"
    _save(path, data)


def _mutate_background_proof_unsupported_native_scheduler(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "Codex", "native-scheduler")
    record["status"] = "verified"
    record["observed_result"] = "passed: native scheduler operation claimed"
    _save(path, data)


def _mutate_background_proof_missing_artifact(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    record = _background_capability_record(data, "Codex", "parent-independence")
    record["evidence_artifact"] = "examples/background-agent/native-contribution-wave-20260924/missing-proof.json"
    _save(path, data)


def _mutate_background_proof_conflicting_status(copy: Path) -> None:
    path = _registry_path(copy)
    data = _load(path)
    entry = next(item for item in data if item.get("product") == "OpenCode")
    source = _background_capability_record(data, "OpenCode", "parent-independence")
    conflicting = json.loads(json.dumps(source))
    conflicting["record_id"] += ":contradictory"
    conflicting["status"] = "unverified"
    conflicting.pop("observed_result", None)
    conflicting["note"] = "Contradictory status for the same exact proof scope."
    entry["capability_records"].append(conflicting)
    _save(path, data)


def _check_capability_projection(report, copy: Path) -> None:
    entries = _load(_registry_path(copy))
    contracts = {
        product: {item.get("contract_id") for item in next(
            entry for entry in entries if entry.get("product") == product
        ).get("evidence_contracts", [])}
        for product in ("OpenCode", "Codex", "Claude Code")
    }
    for product in ("OpenCode", "Codex", "Claude Code"):
        if f"{product.casefold().replace(' ', '-')}.background-agent-proof.v2" not in contracts[product]:
            report.fail(f"{product}: reusable v2 proof contract is not explicitly registered")
    claude_entry = next(item for item in entries if item.get("product") == "Claude Code")
    if claude_entry.get("status") != "documented-unverified" or claude_entry.get("capability_records") != []:
        report.fail("Claude Code documentation/adapter routing promoted unobserved capabilities")
    expected_ids = {
        "Codex": "background-proof-c81444655561472aa20ed857260f92ce",
        "OpenCode": "background-proof-ad1a6d07359d4573bf5ab25628d184fe",
    }
    expected_verified = {
        "separate-cli-invocation",
        "distinct-session-identity",
        "parent-independence",
        "resume",
        "scheduler-wakeup-and-consumption",
        "pause",
        "stop",
        "crash-recovery",
        "exact-target-control",
        "terminal-triggers-stopped",
    }
    for product, test_id in expected_ids.items():
        entry = next(item for item in entries if item.get("product") == product)
        projected = registry.project_capability_records(copy, entry)
        scoped = [record for record in projected if record.get("test_id") == test_id]
        verified = {record["capability"] for record in scoped if record["availability"] == "observed"}
        if not expected_verified.issubset(verified):
            report.fail(f"{product}: isolated controller proof is missing scoped positive records")
        if not scoped or any(
            record.get("scope", {}).get("os") != "linux"
            or record.get("scope", {}).get("profile") != "isolated"
            or record.get("probe") != "bounded-native-background-agent-proof"
            for record in scoped
        ):
            report.fail(f"{product}: projected proof lost OS/profile/probe scope")
        if any(record.get("scope", {}).get("evidence_scope") is None for record in scoped):
            report.fail(f"{product}: projected proof lost its evidence scope")
        native_scheduler = [record for record in scoped if record["capability"] == "native-scheduler"]
        if not native_scheduler or any(record["availability"] != "unknown" for record in native_scheduler):
            report.fail(f"{product}: external recurrence was elevated to a native scheduler")
        if entry.get("status") != "documented-unverified":
            report.fail(f"{product}: scoped proof changed the overall product certification status")
    opencode = next(item for item in entries if item.get("product") == "OpenCode")
    codex = next(item for item in entries if item.get("product") == "Codex")
    opencode_task = registry.project_capability_records(copy, opencode, selectors={
        "capability": "native-task-tool-operation",
        "test_id": expected_ids["OpenCode"],
        "mechanism": "native-tool",
        "scope.profile": "isolated",
    })
    codex_task = registry.project_capability_records(copy, codex, selectors={
        "capability": "native-task-tool-operation",
        "test_id": expected_ids["Codex"],
        "mechanism": "native-tool",
        "scope.profile": "isolated",
    })
    if len(opencode_task) != 1 or opencode_task[0]["availability"] != "observed":
        report.fail("OpenCode: successful native todowrite operation was not projected as scoped evidence")
    if len(codex_task) != 1 or codex_task[0]["availability"] != "unknown":
        report.fail("Codex: missing native task operation was not kept unknown")
    registry.check_registry(report, copy)


CAPABILITY_PROJECTION_SCENARIOS = [
    ("scoped_capability_projection", lambda copy: None, _check_capability_projection, False),
    (
        "typed_assertion_operators",
        _mutate_typed_assertion_operators,
        _check_typed_assertion_operators,
        False,
    ),
    (
        "typed_required_assertion_membership",
        _mutate_typed_required_assertion_membership,
        _check_typed_required_assertion_membership,
        False,
    ),
    (
        "relocated_false_required_observation",
        _mutate_relocated_false_required_observation,
        _check_relocated_false_required_observation,
        False,
    ),
    (
        "background_proof_missing_class_and_schema",
        _mutate_background_proof_missing_class_and_schema,
        _check_background_proof_missing_class_and_schema,
        False,
    ),
    (
        "background_proof_boolean_integer",
        _mutate_background_proof_boolean_integer,
        _check_background_proof_boolean_integer,
        False,
    ),
    (
        "background_proof_malformed_adapter_profile",
        _mutate_background_proof_malformed_adapter_profile,
        _check_background_proof_malformed_adapter_profile,
        False,
    ),
    (
        "background_proof_malformed_lifecycle",
        _mutate_background_proof_malformed_lifecycle,
        _check_background_proof_malformed_lifecycle,
        False,
    ),
    (
        "unrecognized_proof_class",
        _mutate_unrecognized_proof_class,
        _check_unrecognized_proof_class,
        False,
    ),
    (
        "unknown_evidence_contract",
        _mutate_unknown_evidence_contract,
        _check_unknown_evidence_contract,
        False,
    ),
    (
        "unknown_product_evidence_contract",
        _mutate_unknown_product_evidence_contract,
        _check_unknown_product_evidence_contract,
        False,
    ),
    ("background_proof_scope_mismatch", _mutate_background_proof_scope_mismatch, registry.check_registry, True),
    (
        "background_proof_unsupported_native_scheduler",
        _mutate_background_proof_unsupported_native_scheduler,
        registry.check_registry,
        True,
    ),
    ("background_proof_missing_artifact", _mutate_background_proof_missing_artifact, registry.check_registry, True),
    ("background_proof_conflicting_status", _mutate_background_proof_conflicting_status, registry.check_registry, True),
]


def run_capability_projection_selftests(report, root: Path) -> None:
    """Run only the positive/negative regressions for scoped capabilities."""
    behaved = 0
    for name, mutate, check, expect_failure in CAPABILITY_PROJECTION_SCENARIOS:
        if _run_scenario(report, root, name, mutate, check, expect_failure):
            behaved += 1
    report.note(
        f"selftest: {behaved}/{len(CAPABILITY_PROJECTION_SCENARIOS)} capability projection scenarios behaved as expected"
    )


def _check_records(report, copy: Path) -> None:
    campaign.check_records(report, copy)


# --- fresh-agent evals / opportunity map / compatibility regressions ---------


def _mutate_evals_network_allowed(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    data["isolation"]["network"] = "allowed"
    _save(path, data)


def _mutate_evals_prompt_hides_case_id(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    data["cases"][0]["prompt"] = "Fix the docs. Leave evidence."
    _save(path, data)


def _mutate_evals_hides_enum_alternative(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    case = next(item for item in data["cases"] if item["case_id"] == "discovery-research")
    case["prompt"] = case["prompt"].replace(
        "permitted_only_in_separate_session | ",
        "",
        1,
    )
    _save(path, data)


def _mutate_evals_selected_task_contract_missing(copy: Path, case_id: str) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    case = next(item for item in data["cases"] if item["case_id"] == case_id)
    contract = "selected_task (string):"
    if contract not in case["prompt"]:
        raise ValueError(f"{case_id}: selected_task contract is not present to mutate")
    case["prompt"] = case["prompt"].replace(contract, "selected_route (string):", 1)
    _save(path, data)


def _mutate_evals_case_without_trace_pattern(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    data["cases"][0]["expected_actions"] = [{"action": "fix the docs", "evidence": "self-reported"}]
    _save(path, data)


def _mutate_evals_fixture_noop(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/fixtures/docs-fix/fixture.json"
    data = _load(path)
    data["overlay_patches"][0]["find"] = "text-that-does-not-exist-in-the-page"
    _save(path, data)


UNSUPPORTED_ROUTE_REL = (
    "src/openforage/skills/agent_runtime_integration/references/unsupported.md"
)


def _check_unsupported_probe_guidance(report, root: Path) -> None:
    path = root / UNSUPPORTED_ROUTE_REL
    if not path.is_file():
        report.fail(f"unsupported harness route missing: {UNSUPPORTED_ROUTE_REL}")
        return
    text = path.read_text(encoding="utf-8").casefold()
    for required in (
        "documented, bounded, benign capability probes are permitted",
        "solely to establish evidence",
        "unverified unattended campaign commands remain blocked",
        "ordinary repository contributions",
        "offline research",
    ):
        if required not in text:
            report.fail(f"{UNSUPPORTED_ROUTE_REL}: missing explicit probe-routing rule {required!r}")
    if "do not publish or use spawn, pause, resume, stop, or capability-probe commands for an unverified capability" in text:
        report.fail(f"{UNSUPPORTED_ROUTE_REL}: circular blanket ban on evidence probes remains")


def _mutate_unsupported_probe_ban(copy: Path) -> None:
    path = copy / UNSUPPORTED_ROUTE_REL
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "Documented, bounded, benign capability probes are permitted",
        "Documented, bounded, benign capability probes are prohibited",
        1,
    )
    path.write_text(text, encoding="utf-8")


def _mutate_compat_missing_cli(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    del data["expected_surface"]["cli"]
    _save(path, data)


def _mutate_compat_merged_default_labels(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    entry = data["expected_surface"]["cli"]["documented_default_algorithm"]
    entry["cli_label"] = entry["label"]
    _save(path, data)


def _mutate_compat_empty_unavailable(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    data["expected_surface"]["unavailable_or_unidentified"] = []
    _save(path, data)


def _mutate_evals_missing_case(copy: Path) -> None:
    path = copy / "examples/fresh-agent-evals/tasks.json"
    data = _load(path)
    data["cases"] = [case for case in data["cases"] if case["case_id"] != "recovery"]
    _save(path, data)


def _mutate_opportunity_missing_source(copy: Path) -> None:
    path = copy / "docs/agents/research-opportunities.json"
    data = _load(path)
    del data["opportunities"][0]["primary_source"]
    _save(path, data)


def _mutate_opportunity_superiority_claim(copy: Path) -> None:
    path = copy / "docs/agents/research-opportunities.json"
    data = _load(path)
    data["opportunities"][0]["mechanism"] = (
        "This mechanism proves it is superior to random_weighted on every era."
    )
    _save(path, data)


def _mutate_opportunity_fabricated_negative(copy: Path) -> None:
    path = copy / "docs/agents/research-opportunities.json"
    data = _load(path)
    data["negative_results"] = [{"opportunity_id": "coverage-stratified-sampling"}]
    _save(path, data)


def _mutate_compatibility_missing_callables(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    data["expected_surface"]["required_callables"] = {}
    _save(path, data)


def _mutate_compatibility_verified_harness(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    data["harness_versions"]["opencode"]["registry_status"] = "verified"
    _save(path, data)


def _mutate_compatibility_latest_promise(copy: Path) -> None:
    path = copy / "docs/agents/references/compatibility.json"
    data = _load(path)
    data["latest_observation"]["promise"] = "tested-supported"
    _save(path, data)


def run_selftests(report, root: Path) -> None:
    scenarios = [
        ("clean_records", lambda copy: None, _check_records, False),
        ("filled_measured_values", _mutate_filled_measured_values, _check_records, False),
        ("valid_initial_bootstrapping", _mutate_valid_initial_bootstrapping, _check_records, False),
        ("valid_resume_history", _mutate_valid_resume_history, _check_records, False),
        ("valid_multi_hold", _mutate_valid_multi_hold, _check_records, False),
        ("valid_exploratory_comparison", _mutate_valid_exploratory_comparison, _check_records, False),
        ("valid_exhaustion_immediate", _mutate_valid_exhaustion_immediate, _check_records, False),
        ("real_completed_evidence", _mutate_real_completed_evidence, _check_records, False),
        ("real_adapter_typed_outcome", _mutate_real_adapter_typed_outcome, _check_records, False),
        ("real_independent_reverification", _mutate_real_independent_reverification, _check_records, False),
        ("partial_capability_evidence", _mutate_partial_capability_evidence, registry.check_registry, False),
        ("verified_registry_fixture", _mutate_verified_registry_fixture, registry.check_registry, False),
        ("verified_registry_typed_outcome", _mutate_verified_registry_typed_outcome, registry.check_registry, False),
        ("illegal_transition", _mutate_illegal_transition, _check_records, True),
        ("stopped_resume", _mutate_stopped_resume, _check_records, True),
        ("terminal_live_work", _mutate_terminal_live_work, _check_records, True),
        ("paused_live_triggers", _mutate_paused_live_triggers, _check_records, True),
        ("malformed_record", _mutate_malformed_inventory, _check_records, True),
        ("same_revision_history", _mutate_same_revision, _check_records, True),
        ("wrong_resume_target", _mutate_wrong_resume_target, _check_records, True),
        ("manifest_resume_mismatch", _mutate_manifest_resume_mismatch, _check_records, True),
        ("owner_mismatch", _mutate_owner_mismatch, _check_records, True),
        ("revision_type", _mutate_revision_type, _check_records, True),
        ("initial_empty_history_bad_state", _mutate_initial_empty_history_bad_state, _check_records, True),
        ("transition_target_list", _mutate_transition_target_list, _check_records, True),
        ("transition_source_list", _mutate_transition_source_list, _check_records, True),
        ("frozen_baseline_unsupported", _mutate_frozen_baseline_unsupported, _check_records, True),
        ("compatible_baseline_unfrozen", _mutate_compatible_baseline_unfrozen, _check_records, True),
        ("enforced_without_mechanism", _mutate_enforced_without_mechanism, _check_records, True),
        ("negative_resource_value", _mutate_negative_resource_value, _check_records, True),
        ("unknown_resource_kind", _mutate_unknown_resource_kind, _check_records, True),
        ("resource_kind_list", _mutate_resource_kind_list, _check_records, True),
        ("terminal_relaunch_false", _mutate_terminal_relaunch_false, _check_records, True),
        ("embedded_placeholder", _mutate_embedded_placeholder, _check_records, True),
        ("provenance_null_object", _mutate_provenance_null_object, _check_records, True),
        ("registered_prereg_missing", _mutate_registered_prereg_missing, _check_records, True),
        ("complete_inventory_template_rows", _mutate_complete_inventory_template_rows, _check_records, True),
        ("completion_claim_without_evidence", _mutate_completion_claim_without_evidence, _check_records, True),
        ("completion_claim_zero_count", _mutate_completion_claim_zero_count, _check_records, True),
        ("completion_claim_partial_count", _mutate_completion_claim_partial_count, _check_records, True),
        ("completion_claim_fractional_count", _mutate_completion_claim_fractional_count, _check_records, True),
        ("completion_claim_unstructured_artifact", _mutate_completion_claim_unstructured_artifact, _check_records, True),
        ("completion_claim_mismatched_evidence", _mutate_completion_claim_mismatched_evidence, _check_records, True),
        ("adapter_claim_without_evidence", _mutate_adapter_claim_without_evidence, _check_records, True),
        ("adapter_claim_failed_result", _mutate_adapter_claim_failed_result, _check_records, True),
        ("adapter_claim_banana_result", _mutate_adapter_claim_banana_result, _check_records, True),
        ("independent_reverification_missing_evidence", _mutate_independent_reverification_missing_evidence, _check_records, True),
        ("independent_reverification_mismatch", _mutate_independent_reverification_mismatch, _check_records, True),
        ("independent_reverification_template_claim", _mutate_independent_reverification_template_claim, _check_records, True),
        ("deleted_protocol_field", _mutate_deleted_protocol_field, _check_records, True),
        ("verified_adapter_missing_evidence", _mutate_verified_adapter_missing_evidence, registry.check_registry, True),
        ("verified_registry_failed_result", _mutate_verified_registry_failed_result, registry.check_registry, True),
        ("verified_registry_banana_result", _mutate_verified_registry_banana_result, registry.check_registry, True),
        ("verified_registry_scope_string", _mutate_verified_registry_scope_string, registry.check_registry, True),
        ("verified_registry_bad_date", _mutate_verified_registry_bad_date, registry.check_registry, True),
        ("verified_registry_outside_path", _mutate_verified_registry_outside_path, registry.check_registry, True),
        ("verified_registry_version_mismatch", _mutate_verified_registry_version_mismatch, registry.check_registry, True),
        ("verified_registry_lifecycle_mismatch", _mutate_verified_registry_lifecycle_mismatch, registry.check_registry, True),
        ("verified_registry_duplicate_capability", _mutate_verified_registry_duplicate_capability, registry.check_registry, True),
        ("verified_registry_unknown_capability", _mutate_verified_registry_unknown_capability, registry.check_registry, True),
        ("registry_status_list", _mutate_registry_status_list, registry.check_registry, True),
        ("registry_capability_status_list", _mutate_registry_capability_status_list, registry.check_registry, True),
        ("missing_onboarding_target", _mutate_missing_onboarding_target, first_read.check_first_read, True),
        ("stale_algorithm_exclusivity", _mutate_stale_algorithm_exclusivity, first_read.check_first_read, True),
        (
            "truthfulness_stale_search_settings_algorithm",
            _mutate_stale_search_settings_algorithm,
            _check_stale_search_settings_algorithm,
            True,
        ),
        ("missing_entry_session_boundary", _mutate_missing_entry_session_boundary, first_read.check_first_read, True),
        ("offline_work_registration_gate", _mutate_offline_registration_gate, first_read.check_first_read, True),
        ("portable_onboarding_route", lambda copy: None, guidance.check_onboarding, False),
        ("unsupported_probe_permission", lambda copy: None, _check_unsupported_probe_guidance, False),
        ("unsupported_probe_ban", _mutate_unsupported_probe_ban, _check_unsupported_probe_guidance, True),
        ("generated_onboarding_drift", _mutate_generated_onboarding_drift, guidance.check_onboarding, True),
        ("onboarding_default_misroute", _mutate_onboarding_default_route, guidance.check_onboarding, True),
        ("main_session_search_fallback", _mutate_main_session_search_fallback, guidance.check_onboarding, True),
        ("automatic_campaign_launch", _mutate_automatic_campaign_launch, guidance.check_onboarding, True),
        ("excluded_control_field", _mutate_excluded_control_field, controls.check_controls, True),
        ("auth_token_safety_allowed", _mutate_auth_safety_wording, controls.check_controls, False),
        ("broken_internal_anchor", _mutate_broken_anchor, anchors.check_links, True),
        ("missing_state_machine", _mutate_missing_state_machine, campaign.check_state_machine_definition, True),
        ("evals_network_allowed", _mutate_evals_network_allowed, evals.check_evals, True),
        ("evals_missing_case", _mutate_evals_missing_case, evals.check_evals, True),
        ("evals_prompt_hides_case_id", _mutate_evals_prompt_hides_case_id, evals.check_evals, True),
        ("evals_hides_enum_alternative", _mutate_evals_hides_enum_alternative, evals.check_evals, True),
        (
            "evals_research_selected_task_contract_missing",
            lambda copy: _mutate_evals_selected_task_contract_missing(copy, "discovery-research"),
            evals.check_evals,
            True,
        ),
        (
            "evals_contribution_selected_task_contract_missing",
            lambda copy: _mutate_evals_selected_task_contract_missing(
                copy, "discovery-contribution-cold-start"
            ),
            evals.check_evals,
            True,
        ),
        ("evals_case_without_trace_pattern", _mutate_evals_case_without_trace_pattern, evals.check_evals, True),
        ("evals_fixture_noop", _mutate_evals_fixture_noop, evals.check_evals, True),
        ("opportunity_missing_source", _mutate_opportunity_missing_source, opportunities.check_opportunities, True),
        ("opportunity_superiority_claim", _mutate_opportunity_superiority_claim, opportunities.check_opportunities, True),
        ("opportunity_fabricated_negative", _mutate_opportunity_fabricated_negative, opportunities.check_opportunities, True),
        ("compat_missing_callables", _mutate_compatibility_missing_callables, compatibility.check_compatibility, True),
        ("compat_missing_cli", _mutate_compat_missing_cli, compatibility.check_compatibility, True),
        ("compat_merged_default_labels", _mutate_compat_merged_default_labels, compatibility.check_compatibility, True),
        ("compat_empty_unavailable", _mutate_compat_empty_unavailable, compatibility.check_compatibility, True),
        ("compat_verified_harness_claim", _mutate_compatibility_verified_harness, compatibility.check_compatibility, True),
        ("compat_latest_as_supported", _mutate_compatibility_latest_promise, compatibility.check_compatibility, True),
    ]
    scenarios.extend(CAPABILITY_PROJECTION_SCENARIOS)

    behaved = 0
    for name, mutate, check, expect_failure in scenarios:
        if _run_scenario(report, root, name, mutate, check, expect_failure):
            behaved += 1
    report.note(
        f"selftest: {behaved}/{len(scenarios)} scenarios behaved as expected"
    )
    run_link_portability_selftests(report, root)
