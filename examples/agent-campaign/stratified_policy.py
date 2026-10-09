#!/usr/bin/env python3
"""Shared construction-stratified sampling policy for the challenger example.

One policy/accounting core is used by both the offline smoke fixture and the
live challenger callback:

  * cells are enumerable sources (the live callback stratifies over documented
    generator sources; the synthetic fixture stratifies over rotation x
    feature cells);
  * the least-covered cell is targeted; ties break deterministically;
  * accepted proposals update all shared accounting in exactly one method:
    a set-indexed graph key history, per-target-cell counts, descriptor counts,
    accepted-proposal history, and exhausted-cell scheduling state;
  * rejected proposals (duplicate, depth-over-cap, unsupported canonical
    identity, validation failure) update rejection counters only and never
    advance accepted proposals or evaluator completions;
  * rejection caps and per-cell attempt caps are bounded across duplicate and
    validation rejections, so an all-duplicate or all-too-deep finite stream
    terminates loudly instead of spinning;
  * an exhausted cell is scheduling state, never fake coverage;
  * checkpoints persist a deterministic representation (keys as a list, an
    in-memory set index rebuilt on load) and reject bool/negative/fraction
    counts or incompatible policy/cell identities.

Canonical graph identity uses a known mapping shape or a public canonical
serializer when one exists. Anything else is an explicit unsupported-identity
result, never a repro address that would not be reproducible.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Callable

MAX_DEPTH = 6
CHECKPOINT_VERSION = 2
IDENTITY_MAPPING = "mapping-json-sorted"
IDENTITY_SERIALIZER_PREFIX = "public-serializer:"
IDENTITY_UNSUPPORTED = "unsupported-canonical-identity"


@dataclass
class Proposal:
    construction_key: str
    cell: str
    descriptor: str
    payload: object
    identity_kind: str


@dataclass
class Ledger:
    """Shared construction-time accounting, never inferred from private state.

    ``evaluations``/``passed`` are evaluator completions. ``proposals`` counts
    accepted proposals only; rejected proposals live in the rejection counters.
    """

    evaluations: int = 0
    passed: int = 0
    proposals: int = 0
    duplicates_rejected: int = 0
    depth_rejected: int = 0
    unavailable_rejected: int = 0
    other_rejected: int = 0
    cell_counts: dict = field(default_factory=dict)
    descriptor_counts: dict = field(default_factory=dict)
    exhausted_cells: list = field(default_factory=list)
    seen_keys: list = field(default_factory=list)
    history_hashes: list = field(default_factory=list)
    identity_notes: list = field(default_factory=list)
    validation_notes: dict = field(default_factory=dict)
    stopped_reason: str | None = None
    rng_state: list | None = None
    seen_index: set = field(default_factory=set, repr=False, compare=False)

    def accept(self, proposal: Proposal) -> None:
        """The single shared accepted-proposal accounting method."""
        self.proposals += 1
        self.seen_keys.append(proposal.construction_key)
        self.seen_index.add(proposal.construction_key)
        self.history_hashes.append(proposal.construction_key)
        self.cell_counts[proposal.cell] = self.cell_counts.get(proposal.cell, 0) + 1
        self.descriptor_counts[proposal.descriptor] = self.descriptor_counts.get(proposal.descriptor, 0) + 1
        if proposal.identity_kind not in self.identity_notes:
            self.identity_notes.append(proposal.identity_kind)

    def reject(self, reason: str) -> None:
        if reason == "depth":
            self.depth_rejected += 1
        elif reason == "duplicate":
            self.duplicates_rejected += 1
        elif reason in ("unavailable", "unsupported-identity"):
            self.unavailable_rejected += 1
        else:
            self.other_rejected += 1
        self.validation_notes[reason] = self.validation_notes.get(reason, 0) + 1

    def cell_counts_snapshot(self) -> dict:
        return {cell: self.cell_counts.get(cell, 0) for cell in sorted(self.cell_counts)}

    def descriptor_counts_snapshot(self) -> dict:
        return {key: self.descriptor_counts.get(key, 0) for key in sorted(self.descriptor_counts)}


class UnsupportedIdentity(ValueError):
    """Canonical graph identity is not available for this payload type."""


def canonical_payload_key(payload) -> tuple[str, str]:
    """Return (canonical key, identity kind) or raise UnsupportedIdentity.

    Known mapping shapes are serialized with sorted keys. A public canonical
    serializer (``to_dict``/``to_json``/``canonical``/``serialize``) is used
    when the payload exposes one. Bare object reprs are never hashed.
    """
    if isinstance(payload, dict):
        return _hash_canonical(_canonicalize(payload)), IDENTITY_MAPPING
    for name in ("to_dict", "to_json", "canonical", "serialize"):
        serializer = getattr(payload, name, None)
        if callable(serializer):
            try:
                rendered = serializer()
            except Exception as exc:  # noqa: BLE001
                raise UnsupportedIdentity(f"public serializer {name}() failed: {exc}") from exc
            if isinstance(rendered, str):
                return hashlib.sha256(rendered.encode("utf-8")).hexdigest(), IDENTITY_SERIALIZER_PREFIX + name
            if isinstance(rendered, dict):
                return _hash_canonical(_canonicalize(rendered)), IDENTITY_SERIALIZER_PREFIX + name
            raise UnsupportedIdentity(f"public serializer {name}() returned {type(rendered).__name__}")
    raise UnsupportedIdentity(f"no canonical mapping shape for {type(payload).__name__}")


def _canonicalize(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    raise UnsupportedIdentity(f"unsupported value type {type(value).__name__}")


def _hash_canonical(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StratifiedPolicy:
    """Coverage-stratified policy over enumerable descriptor cells."""

    name = "stratified-coverage"

    def __init__(self, cells: list[str], draw_cell: Callable[[str, random.Random], object],
                 describe: Callable[[str, object], str], key_of: Callable[[str, object], str],
                 rng: random.Random, validate: Callable[[object], str | None] | None = None,
                 max_attempts: int = 64, max_consecutive_rejections: int = 128):
        if not cells:
            raise ValueError("stratified policy needs at least one cell")
        self.cells = list(cells)
        self.draw_cell = draw_cell
        self.describe = describe
        self.key_of = key_of
        self.rng = rng
        self.validate = validate
        self.max_attempts = max_attempts
        self.max_consecutive_rejections = max_consecutive_rejections

    @property
    def cell_identity(self) -> list[str]:
        return list(self.cells)

    def _target_cell(self, ledger: Ledger) -> str | None:
        available = [cell for cell in self.cells if cell not in ledger.exhausted_cells]
        if not available:
            return None
        return min(available, key=lambda cell: (ledger.cell_counts.get(cell, 0), cell))

    def _exhaust(self, ledger: Ledger, cell: str, reason: str) -> None:
        if cell not in ledger.exhausted_cells:
            ledger.exhausted_cells.append(cell)
        ledger.validation_notes.setdefault(f"exhausted:{reason}", 0)
        ledger.validation_notes[f"exhausted:{reason}"] += 1

    def propose(self, ledger: Ledger) -> Proposal | None:
        consecutive = 0
        while consecutive < self.max_consecutive_rejections:
            cell = self._target_cell(ledger)
            if cell is None:
                ledger.stopped_reason = "all-cells-exhausted"
                return None
            for _ in range(self.max_attempts):
                try:
                    payload = self.draw_cell(cell, self.rng)
                except StopIteration:
                    self._exhaust(ledger, cell, "stream-exhausted")
                    consecutive += 1
                    break
                try:
                    key = self.key_of(cell, payload)
                    if key is None:
                        raise UnsupportedIdentity("key function returned no identity")
                except UnsupportedIdentity:
                    ledger.reject("unsupported-identity")
                    consecutive += 1
                    continue
                if key in ledger.seen_index:
                    ledger.reject("duplicate")
                    consecutive += 1
                    continue
                reason = self.validate(payload) if self.validate else None
                if reason:
                    ledger.reject(reason)
                    consecutive += 1
                    continue
                proposal = Proposal(key, cell, self.describe(cell, payload), payload,
                                    _identity_kind_for(payload))
                ledger.accept(proposal)
                return proposal
            else:
                # Every attempt in this cell was rejected/duplicated: freeze the
                # cell as scheduling state; never fake coverage for it.
                self._exhaust(ledger, cell, "attempt-cap")
                consecutive += 1
        ledger.stopped_reason = "max-consecutive-rejections"
        return None


def _identity_kind_for(payload) -> str:
    if isinstance(payload, dict):
        return IDENTITY_MAPPING
    for name in ("to_dict", "to_json", "canonical", "serialize"):
        if callable(getattr(payload, name, None)):
            return IDENTITY_SERIALIZER_PREFIX + name
    return IDENTITY_UNSUPPORTED


def encode_rng_state(state) -> list:
    version, internal, gauss = state
    return [version, list(internal), gauss]


def decode_rng_state(state: list):
    version, internal, gauss = state
    return version, tuple(internal), gauss


def _require_count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"checkpoint {name} must be a non-negative integer, found {value!r}")
    return value


def new_ledger_from_checkpoint(checkpoint: dict, policy: StratifiedPolicy | None = None) -> Ledger:
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint must be an object")
    if policy is not None:
        if checkpoint.get("policy") != policy.name:
            raise ValueError(
                f"checkpoint policy {checkpoint.get('policy')!r} is incompatible with {policy.name!r}"
            )
        if checkpoint.get("cell_identity") != policy.cell_identity:
            raise ValueError("checkpoint cell identity is incompatible with this policy")
    ledger = Ledger(
        evaluations=_require_count(checkpoint.get("evaluations_completed", 0), "evaluations_completed"),
        passed=_require_count(checkpoint.get("passed_completed", 0), "passed_completed"),
        proposals=_require_count(checkpoint.get("proposals", 0), "proposals"),
        duplicates_rejected=_require_count(checkpoint.get("duplicates_rejected", 0), "duplicates_rejected"),
        depth_rejected=_require_count(checkpoint.get("depth_rejected", 0), "depth_rejected"),
        unavailable_rejected=_require_count(checkpoint.get("unavailable_rejected", 0), "unavailable_rejected"),
        other_rejected=_require_count(checkpoint.get("other_rejected", 0), "other_rejected"),
        cell_counts=dict(checkpoint.get("cell_counts", {})),
        descriptor_counts=dict(checkpoint.get("descriptor_counts", {})),
        exhausted_cells=list(checkpoint.get("exhausted_cells", [])),
        seen_keys=list(checkpoint.get("seen_keys", [])),
        history_hashes=list(checkpoint.get("history_hashes", [])),
        identity_notes=list(checkpoint.get("identity_notes", [])),
    )
    if checkpoint.get("rng_state") is not None:
        ledger.rng_state = list(checkpoint["rng_state"])
    ledger.seen_index = set(ledger.seen_keys)
    return ledger


def checkpoint_from_ledger(policy: StratifiedPolicy, ledger: Ledger, extra: dict | None = None) -> dict:
    data = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "policy": policy.name,
        "cell_identity": policy.cell_identity,
        "evaluations_completed": ledger.evaluations,
        "passed_completed": ledger.passed,
        "proposals": ledger.proposals,
        "duplicates_rejected": ledger.duplicates_rejected,
        "depth_rejected": ledger.depth_rejected,
        "unavailable_rejected": ledger.unavailable_rejected,
        "other_rejected": ledger.other_rejected,
        "cell_counts": dict(ledger.cell_counts),
        "descriptor_counts": dict(ledger.descriptor_counts),
        "exhausted_cells": list(ledger.exhausted_cells),
        "seen_keys": list(ledger.seen_keys),
        "history_hashes": list(ledger.history_hashes),
        "identity_notes": list(ledger.identity_notes),
        "stopped_reason": ledger.stopped_reason,
        "rng_state": encode_rng_state(policy.rng.getstate()),
    }
    if extra:
        data.update(extra)
    return data
