"""Render shared first-use guidance from the existing onboarding authorities."""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any

BEGIN_MARKER = "<!-- BEGIN GENERATED ENTRY GUIDANCE -->"
END_MARKER = "<!-- END GENERATED ENTRY GUIDANCE -->"

ENTRY_FRAGMENT_OUTPUTS = {
    "quickstart": "docs/agents/quickstart.md",
    "first_search": "docs/agents/first-search.md",
    "install_authenticate": "docs/agents/install-authenticate.md",
    "research_opportunities": "docs/agents/research-opportunities.md",
    "first_contribution": "docs/agents/first-contribution.md",
    "onboard_agent": "docs/start-here/onboard-your-agent.md",
}


def _link(from_path: str, target: str, label: str | None = None) -> str:
    source_dir = PurePosixPath(from_path).parent.as_posix()
    relative = posixpath.relpath(target, source_dir)
    return f"[`{label or target}`]({relative})"


def render_entry_fragment(
    target_path: str,
    onboarding: dict[str, Any],
    compatibility: dict[str, Any],
    campaign_template: dict[str, Any],
) -> str:
    """Render a compact offline/live/algorithm capsule for a first-use page."""
    sources = onboarding.get("source_paths", {})
    runtime_rules = onboarding.get("runtime_rules", {})
    if not isinstance(sources, dict) or not isinstance(runtime_rules, dict):
        raise ValueError("onboarding source_paths and runtime_rules must be objects")
    access_rule = onboarding.get("access_rule")
    offline_task = onboarding.get("tasks", {}).get("research", {})
    research_action = offline_task.get("immediate_action") if isinstance(offline_task, dict) else None
    if not isinstance(access_rule, str) or not isinstance(research_action, str):
        raise ValueError("canonical onboarding access rule and research action are required")

    try:
        cli = compatibility["expected_surface"]["cli"]
        default_algorithm = cli["documented_default_algorithm"]["value"]
        choices = cli["start_algorithm_choices"]
        callables = compatibility["expected_surface"]["required_callables"]
        version = compatibility["supported"]["openforage"]["version"]
        target_count = campaign_template["frozen_baseline"]["target_completed_simulations"]
    except (KeyError, TypeError) as exc:
        raise ValueError("compatibility or campaign source is missing canonical algorithm facts") from exc
    if (
        not isinstance(default_algorithm, str)
        or not isinstance(version, str)
        or not isinstance(target_count, int)
        or isinstance(target_count, bool)
        or not isinstance(choices, list)
        or any(not isinstance(item, str) for item in choices)
        or not isinstance(callables, dict)
    ):
        raise ValueError("compatibility or campaign algorithm facts have invalid types")

    callable_names = sorted(
        key.removeprefix("openforage.templates.")
        for key, value in callables.items()
        if key.startswith("openforage.templates.")
        and isinstance(value, dict)
        and value.get("observable") is True
    )
    if not callable_names:
        raise ValueError("compatibility record has no observable algorithm templates")
    genetic = callables.get("openforage.templates.genetic")
    if not isinstance(genetic, dict) or genetic.get("observable") is not True:
        raise ValueError("compatibility record must expose the genetic callable for the GA target")

    compatibility_path = sources.get("compatibility", "docs/agents/references/compatibility.json")
    campaign_path = sources.get("campaign_template", "examples/agent-campaign/campaign.json")
    research_path = sources.get("research_opportunities", "docs/agents/research-opportunities.md")
    contribution_path = sources.get("first_contribution", "docs/agents/first-contribution.md")
    packet_path = onboarding.get("generated_entrypoints", {}).get(
        "portable_packet", "docs/agents/start-here.md"
    )
    readme_path = "README.md"
    if not all(isinstance(path, str) for path in (
        compatibility_path, campaign_path, research_path, contribution_path, packet_path
    )):
        raise ValueError("entry guidance source paths must be strings")

    choices_text = ", ".join(f"`{name}`" for name in choices)
    callables_text = ", ".join(f"`{name}`" for name in callable_names)
    continuous_rule = runtime_rules.get("continuous_search")
    if not isinstance(continuous_rule, str):
        raise ValueError("continuous_search rule is required")
    continuous_lower = continuous_rule.casefold()
    if not all(token in continuous_lower for token in ("same harness", "distinct session", "never fall back")):
        raise ValueError("canonical continuous-search rule is missing its process/identity/fallback boundary")
    compact_continuous_rule = (
        "Continuous search requires an independently started CLI process of the same harness with a distinct session identity. "
        "If that identity is not established, keep work offline; never use the main conversation. "
        "Python search snippets below are for that child only."
    )

    if target_path == ENTRY_FRAGMENT_OUTPUTS["first_contribution"]:
        offline_first = (
            "**Offline first.** Choose the row matching the explicit request in this guide, "
            "open its authoritative file, and run its listed baseline validation before editing. "
            "No invite, wallet, installed package, daemon, or worker is needed."
        )
    else:
        offline_first = (
            f"**Offline first.** {research_action} For documentation work, use "
            f"{_link(target_path, contribution_path)}. Reading, research, and contributions need no "
            "invite, wallet, installed package, daemon, or worker. "
            f"Source: {_link(target_path, research_path)}."
        )

    lines = [
        BEGIN_MARKER,
        offline_first,
        f"Without Python or automatic root-instruction loading, read or paste {_link(target_path, packet_path)}.",
        f"**Authorized live path.** A live campaign requires explicit user authorization; follow {_link(target_path, readme_path)} for its bootstrap. {compact_continuous_rule}",
        f"**Canonical algorithm/reference facts.** Tested-supported `{version}` records templates {callables_text} and CLI choices {choices_text}. It documents `{default_algorithm}` as default; CLI help prints no default. The GA reference target is `genetic` with {target_count:,} completed simulations—target only, with no full GA benchmark measured. Sources: {_link(target_path, compatibility_path)}, {_link(target_path, campaign_path)}.",
        END_MARKER,
    ]
    return "\n".join(lines)


def replace_entry_fragment(text: str, replacement: str, relpath: str) -> str:
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValueError(f"{relpath} must contain exactly one generated entry-guidance marker pair")
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    if end < start:
        raise ValueError(f"{relpath} generated entry-guidance markers are reversed")
    return text[:start] + replacement + text[end:]
