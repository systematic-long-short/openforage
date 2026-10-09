"""Campaign state machine definition and append-only history validation.

Owner of the state/history rule set:

- the authoritative JSON definition check;
- transition-chain legality, including per-hold resume targets derived from
  the state each hold left (or from an explicit transition field);
- ownership/revision consistency and chronological ordering.
"""

from __future__ import annotations

import json
from pathlib import Path

from .common import (
    STATE_MACHINE_PATH,
    is_iso_datetime,
    is_number,
    is_placeholder,
    load_json,
    parsed_iso,
)

TERMINAL_STATES = {"exhausted", "stopped", "completed"}
ACTIVE_STATES = {"bootstrapping", "baseline", "research", "experiment", "comparison"}
HOLD_STATES = {"paused", "needs-input"}
STATES = ACTIVE_STATES | HOLD_STATES | TERMINAL_STATES


def load_state_machine(report, root: Path):
    path = root / STATE_MACHINE_PATH
    if not path.is_file():
        report.fail(f"campaign state machine missing: {STATE_MACHINE_PATH}")
        return None
    data = load_json(report, path)
    if data is None:
        return None
    if not isinstance(data, dict):
        report.fail(f"{STATE_MACHINE_PATH}: top level must be an object")
        return None
    return data


def machine_index(report, machine) -> dict:
    index = {
        "states": set(),
        "terminal": set(),
        "initial": None,
        "edges": set(),
        "resume_edges": {},
        "resume_targets": set(),
        "transition_fields": [],
    }
    if not isinstance(machine, dict):
        return index
    states = machine.get("states")
    if isinstance(states, list):
        for state in states:
            if isinstance(state, dict) and isinstance(state.get("id"), str) and state.get("id"):
                index["states"].add(state["id"])
                if state.get("terminal"):
                    index["terminal"].add(state["id"])
    index["initial"] = machine.get("initial_state")
    transition_record = machine.get("transition_record")
    if isinstance(transition_record, dict):
        fields = transition_record.get("required_fields")
        if isinstance(fields, list):
            index["transition_fields"] = [str(f) for f in fields]
    transitions = machine.get("transitions")
    if isinstance(transitions, list):
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            to_state = transition.get("to")
            from_states = transition.get("from")
            from_states = from_states if isinstance(from_states, list) else [from_states]
            from_states = [state for state in from_states if isinstance(state, str)]
            if to_state == "resume_target":
                resolves = transition.get("resolves_to")
                resolves = (
                    {target for target in resolves if isinstance(target, str)}
                    if isinstance(resolves, list)
                    else set()
                )
                index["resume_targets"].update(resolves)
                for from_state in from_states:
                    index["resume_edges"].setdefault(from_state, set()).update(resolves)
            elif isinstance(to_state, str):
                for from_state in from_states:
                    index["edges"].add((from_state, to_state))
    declared_terminal = machine.get("terminal_states")
    if isinstance(declared_terminal, list):
        declared = {state for state in declared_terminal if isinstance(state, str)}
        index["terminal"] = declared | index["terminal"]
    return index


def check_state_machine_definition(report, root: Path) -> None:
    machine = load_state_machine(report, root)
    if machine is None:
        return
    where = STATE_MACHINE_PATH
    index = machine_index(report, machine)

    if not index["states"]:
        report.fail(f"{where}: no states defined")
    initial = index["initial"]
    if not isinstance(initial, str) or initial not in index["states"]:
        report.fail(f"{where}: initial_state {initial!r} is not a defined state")
    if not TERMINAL_STATES <= index["states"]:
        report.fail(
            f"{where}: terminal states {sorted(TERMINAL_STATES - index['states'])} "
            "must be defined"
        )
    if index["terminal"] != TERMINAL_STATES:
        report.fail(
            f"{where}: terminal_states must be exactly {sorted(TERMINAL_STATES)}; "
            f"found {sorted(index['terminal'])}"
        )
    if not index["transition_fields"]:
        report.fail(f"{where}: transition_record.required_fields must be a non-empty list")

    states = machine.get("states")
    for state in states if isinstance(states, list) else []:
        if not isinstance(state, dict):
            report.fail(f"{where}: every state must be an object")
            continue
        state_id = state.get("id")
        if not isinstance(state_id, str):
            report.fail(f"{where}: every state id must be a string; found {state_id!r}")
            continue
        if state_id not in STATES:
            report.fail(f"{where}: unknown state id {state_id!r}")
        if state_id in TERMINAL_STATES and not state.get("terminal"):
            report.fail(f"{where}: terminal state {state_id!r} must set \"terminal\": true")
        if state_id in HOLD_STATES and state.get("requires_resume_target") is not True:
            report.fail(f"{where}: hold state {state_id!r} must require a resume target")
        if state_id in ACTIVE_STATES and state.get("terminal"):
            report.fail(f"{where}: active state {state_id!r} must not be terminal")

    transitions = machine.get("transitions")
    exhaustion_sources: set[str] = set()
    for transition in transitions if isinstance(transitions, list) else []:
        if not isinstance(transition, dict):
            report.fail(f"{where}: every transition must be an object")
            continue
        for key in ("id", "from", "to", "guard", "effect"):
            if not transition.get(key):
                report.fail(f"{where}: transition missing {key!r}")
        from_states = transition.get("from")
        from_states = from_states if isinstance(from_states, list) else [from_states]
        for from_state in from_states:
            if not isinstance(from_state, str):
                report.fail(f"{where}: transition source must be a state id string; found {from_state!r}")
            elif from_state not in index["states"]:
                report.fail(f"{where}: transition source {from_state!r} is not a state")
        to_state = transition.get("to")
        if to_state != "resume_target":
            if not isinstance(to_state, str):
                report.fail(f"{where}: transition target must be a state id string; found {to_state!r}")
            elif to_state not in index["states"]:
                report.fail(f"{where}: transition target {to_state!r} is not a state")
        if transition.get("id") == "T-exhausted":
            exhaustion_sources = {
                state for state in from_states if isinstance(state, str)
            }

    if exhaustion_sources and exhaustion_sources != ACTIVE_STATES:
        report.fail(
            f"{where}: T-exhausted must be reachable from every active phase "
            f"({sorted(ACTIVE_STATES)}); found {sorted(exhaustion_sources)}"
        )
    if "T-exhausted" in json.dumps(transitions) and not any(
        isinstance(t, dict)
        and t.get("id") == "T-exhausted"
        and "plateau" in json.dumps(t.get("guard", "")).lower()
        and ("breach" in json.dumps(t.get("guard", "")).lower())
        for t in (transitions if isinstance(transitions, list) else [])
    ):
        report.fail(
            f"{where}: T-exhausted guard must distinguish a hard resource "
            "breach/exhaustion (immediate) from a statistical plateau"
        )

    for state in index["states"]:
        if state not in index["terminal"]:
            continue
        outgoing = {edge for edge in index["edges"] if edge[0] == state}
        if outgoing or state in index["resume_edges"]:
            report.fail(f"{where}: terminal state {state!r} must have no outgoing transitions")

    invariant_text = json.dumps(machine.get("invariants", [])).lower()
    if not machine.get("invariants") or "incomplete" not in invariant_text:
        report.fail(
            f"{where}: invariants must state at least that an incomplete evaluation "
            "budget stays incomplete"
        )
    if "plateau" not in invariant_text:
        report.fail(
            f"{where}: invariants must state the plateau-versus-breach handling rule"
        )
    ownership = machine.get("ownership")
    if not isinstance(ownership, dict) or not ownership.get("claim", {}).get("mechanism"):
        report.fail(f"{where}: ownership.claim.mechanism must describe the claim/lock requirement")
    submission = ownership.get("submission_reconciliation") if isinstance(ownership, dict) else None
    if not isinstance(submission, dict) or "ambiguous" not in json.dumps(submission).lower():
        report.fail(
            f"{where}: ownership.submission_reconciliation must define the "
            "ambiguous-acknowledgment rule"
        )
    if not isinstance(submission, dict) or not submission.get("no_exactly_once"):
        report.fail(
            f"{where}: ownership.submission_reconciliation.no_exactly_once must state "
            "that no exactly-once guarantee exists"
        )


def _transition_revision(report, spot: str, transition) -> int | None:
    revision = transition.get("owner_revision")
    if not is_number(revision) or not float(revision).is_integer():
        report.fail(f"{spot}: owner_revision must be an integer (bool is not accepted)")
        return None
    return int(revision)


def check_history(report, where: str, data, machine, template: bool) -> None:
    """Validate the append-only transition history and resume-target behavior.

    The preserved resume target for a hold is derived from the state the hold
    left, or from an explicit ``resume_target`` field on the hold transition.
    Each hold is compared independently, so multiple holds at different phases
    are supported. The manifest ``resume_target`` is only compared when the
    current state is itself a hold state.
    """
    index = machine_index(report, machine)
    state_machine = data.get("state_machine")
    if not isinstance(state_machine, dict):
        report.fail(f"{where}.state_machine: expected an object")
        return
    transitions = data.get("transitions")
    if not isinstance(transitions, list):
        report.fail(f"{where}.transitions: must be an append-only list")
        return

    state = state_machine.get("state")
    revision = state_machine.get("revision")
    ownership = state_machine.get("ownership")
    if not isinstance(ownership, dict):
        ownership = {}
    manifest_target = state_machine.get("resume_target")

    if not is_number(revision) or not float(revision).is_integer() or revision < 0:
        report.fail(f"{where}.state_machine.revision must be a non-negative integer")
        revision = None
    else:
        revision = int(revision)
    owner_revision = ownership.get("owner_revision")
    if not is_number(owner_revision) or not float(owner_revision).is_integer():
        report.fail(
            f"{where}.state_machine.ownership.owner_revision must be an integer"
        )
    elif revision is not None and int(owner_revision) != revision:
        report.fail(
            f"{where}: state_machine.revision ({revision}) must match "
            f"ownership.owner_revision ({int(owner_revision)})"
        )

    if not transitions:
        if state != index["initial"]:
            report.fail(
                f"{where}.transitions: empty history is only legal for an initial "
                f"{index['initial']!r} record; found state {state!r}"
            )
        if revision not in (0,):
            report.fail(
                f"{where}: an initial record with empty history must use revision 0"
            )
        if manifest_target is not None:
            report.fail(
                f"{where}: an initial record with empty history must not carry a resume_target"
            )
        return

    seen_ids: set[str] = set()
    previous_to = None
    last_revision: int | None = None
    last_owner = None
    last_timestamp = None
    hold_target: str | None = None
    hold_open = False

    for position, transition in enumerate(transitions):
        spot = f"{where}.transitions[{position}]"
        if not isinstance(transition, dict):
            report.fail(f"{spot}: must be an object")
            continue
        for field in index["transition_fields"]:
            if field not in transition:
                report.fail(f"{spot}: missing transition field {field!r}")

        transition_id = transition.get("transition_id")
        if not isinstance(transition_id, str) or not transition_id.strip():
            report.fail(f"{spot}: transition_id must be a non-empty string")
        elif transition_id in seen_ids:
            report.fail(f"{spot}: duplicate transition_id {transition_id!r}")
        else:
            seen_ids.add(transition_id)

        if transition.get("guard_passed") is not True:
            report.fail(f"{spot}: guard_passed must be true for a recorded transition")

        source = transition.get("from")
        target = transition.get("to")
        source_ok = isinstance(source, str)
        target_ok = isinstance(target, str)
        if not source_ok or not target_ok:
            report.fail(
                f"{spot}: transition from/to must be state id strings; "
                f"found from={source!r}, to={target!r}"
            )
        if position == 0 and source != index["initial"]:
            report.fail(
                f"{spot}: first transition must start at {index['initial']!r}; "
                f"found {source!r}"
            )
        if previous_to is not None and source != previous_to:
            report.fail(
                f"{spot}: transition chain is broken; expected from {previous_to!r}, "
                f"found {source!r}"
            )

        legal = False
        if source_ok and target_ok:
            legal = (source, target) in index["edges"]
            if not legal and source in index["resume_edges"]:
                legal = (
                    hold_open
                    and hold_target is not None
                    and target == hold_target
                    and target in index["resume_edges"][source]
                )
        if not legal and source_ok and target_ok:
            report.fail(f"{spot}: illegal transition {source!r} -> {target!r}")

        timestamp = transition.get("timestamp")
        if not template and not is_placeholder(timestamp):
            if not is_iso_datetime(timestamp):
                report.fail(f"{spot}: timestamp must be a valid ISO-8601 datetime: {timestamp!r}")
            else:
                parsed = parsed_iso(timestamp)
                if last_timestamp is not None and parsed is not None and parsed < last_timestamp:
                    report.fail(f"{spot}: timestamps must be non-decreasing down the history")
                if parsed is not None:
                    last_timestamp = parsed

        revision_value = _transition_revision(report, spot, transition)
        if revision_value is not None:
            if last_revision is not None and revision_value <= last_revision:
                report.fail(
                    f"{spot}: owner_revision must strictly advance by at least one "
                    f"(previous {last_revision}, found {revision_value})"
                )
            last_revision = revision_value

        owner = transition.get("owner_session_identity")
        if not isinstance(owner, str) or not owner.strip():
            report.fail(f"{spot}: owner_session_identity must be a non-empty string")
        elif not template and not is_placeholder(owner):
            last_owner = owner

        if target_ok and target in HOLD_STATES:
            declared = transition.get("resume_target")
            if declared is not None and not isinstance(declared, str):
                report.fail(
                    f"{spot}: explicit resume_target must be a state id string; "
                    f"found {declared!r}"
                )
                declared = None
            preserved = declared if declared is not None else source
            if declared is not None and declared not in ACTIVE_STATES:
                report.fail(
                    f"{spot}: explicit resume_target {declared!r} must be an active state"
                )
            if declared is not None and source_ok and source in ACTIVE_STATES and declared != source:
                report.fail(
                    f"{spot}: explicit resume_target {declared!r} must match the "
                    f"state the hold left ({source!r})"
                )
            if not isinstance(preserved, str) or preserved not in ACTIVE_STATES:
                report.fail(
                    f"{spot}: hold transition must preserve an active resume target; "
                    f"found {preserved!r}"
                )
            hold_target = preserved if isinstance(preserved, str) else None
            hold_open = True
        elif source_ok and source in HOLD_STATES:
            hold_target = None
            hold_open = False

        previous_to = target

    if not isinstance(state, str):
        report.fail(f"{where}.state_machine.state must be a state id string; found {state!r}")
    elif state not in index["states"]:
        report.fail(f"{where}.state_machine.state: unknown state {state!r}")
    elif previous_to != state:
        report.fail(
            f"{where}: current state {state!r} does not match the last transition "
            f"target {previous_to!r}"
        )

    if last_revision is not None and revision is not None and revision != last_revision:
        report.fail(
            f"{where}: state_machine.revision ({revision}) must match the last "
            f"transition owner_revision ({last_revision})"
        )
    if last_owner is not None:
        owner = ownership.get("owner_session_identity")
        if owner != last_owner:
            report.fail(
                f"{where}: ownership.owner_session_identity ({owner!r}) must match "
                f"the last transition owner ({last_owner!r})"
            )

    if isinstance(state, str) and state in HOLD_STATES:
        if not hold_open or hold_target is None:
            report.fail(
                f"{where}: state {state!r} requires a hold transition that preserves "
                "a resume target"
            )
        elif manifest_target != hold_target:
            report.fail(
                f"{where}: state {state!r} requires resume_target {hold_target!r}; "
                f"found {manifest_target!r}"
            )
    elif manifest_target is not None:
        report.fail(
            f"{where}: only paused/needs-input may carry a resume_target; "
            f"state {state!r} has {manifest_target!r}"
        )
