#!/usr/bin/env python3
"""Executable baseline and coverage-oriented challenger example.

This is a worked example, not an OpenForage library feature. Modes:

  --smoke / --selfcheck   offline, no credentials, explicitly synthetic
                          fixture data. Uses the same shared policy/accounting
                          core as the live callback.
  --stub-check            offline integration test of the actual live
                          callback with controlled documented-generator
                          streams. Labeled a stub-stream integration test,
                          never a library evaluation.
  --live-check            probe the installed public surface with real
                          callability/signature evidence and fail on a
                          missing required call.
  --live-run --arm ...    explicit single-arm live mode. One process runs one
                          arm with the exact caller-supplied signal pool name
                          and settings path; independent state/history for the
                          two arms is a documented caller requirement, not a
                          fabricated guarantee. Run the baseline and
                          challenger arms as separate invocations, then
                          `--ingest` the two captures.
  --ingest                validate two captures: types, signs, zero counts,
                          run identity/lineage, and immutable observed
                          conditions must match; unknown conditions can never
                          compare equal, even when raw counts match.

Honesty rules encoded here:

  * The synthetic evaluator is NOT an OpenForage result. Smoke reports carry
    data_origin=synthetic-fixture and claim=not-a-measured-result.
  * Accepted proposals, rejections, and evaluator completions are separate
    counters in one shared ledger method.
  * `random_weighted` is the observed comparator surface and is never labeled
    a GA. No full benchmark of either algorithm has run in this packet.
    The installed surface exposes a callable `genetic` template and the CLI
    accepts `--algorithm genetic`; GA is the intended 100,000-completed-
    simulation reference, but its internal optimizer implementation was not
    inspected and no full GA benchmark has run in this packet.
  * The retired `compose_next_batch` guess is not used in any runnable path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from stratified_policy import (
    MAX_DEPTH,
    Ledger,
    StratifiedPolicy,
    UnsupportedIdentity,
    canonical_payload_key,
    checkpoint_from_ledger,
    decode_rng_state,
    new_ledger_from_checkpoint,
)

SMOKE_DEFAULT_EVALUATIONS = 2000
SEED = 20260923
ROTATIONS = (
    "mean_reversion", "momentum", "volatility", "trend",
    "carry", "seasonality", "liquidity", "dispersion",
)
FEATURES = ("close", "volume", "spread", "open_interest", "basis", "flow")
LIVE_CELLS = ("random_weighted", "genetic")
GA_STATUS = (
    "installed surface exposes callable templates.random_weighted and templates.genetic and "
    "start --algorithm {random_weighted,genetic}; random_weighted is the observed comparator "
    "surface with no measured full reference; the "
    "genetic public callable and CLI choice are observed, its internal optimizer implementation was "
    "not inspected, and no full GA benchmark has run in this packet"
)


@dataclass
class Candidate:
    construction_key: str
    rotation: str
    feature: str
    depth: int
    seed: int
    proposed: bool = True


class SyntheticEvaluator:
    """Deterministic synthetic fixture evaluator. Not an OpenForage result."""

    def __init__(self, beneficial_cells: set[str]):
        self.beneficial_cells = beneficial_cells

    def evaluate(self, candidate: Candidate) -> bool:
        cell = f"{candidate.rotation}|{candidate.feature}"
        base = 0.02 if cell in self.beneficial_cells else 0.002
        digest = int(hashlib.sha256(candidate.construction_key.encode("utf-8")).hexdigest()[:8], 16)
        return (digest / 0xFFFFFFFF) < base


def _candidate_key(rotation: str, feature: str, depth: int, seed: int) -> str:
    return hashlib.sha256(f"{rotation}/{feature}/{depth}/{seed}".encode("utf-8")).hexdigest()[:16]


def _draw_uniform(cell: str, rng: random.Random) -> Candidate:
    rotation = rng.choice(ROTATIONS)
    feature = rng.choice(FEATURES)
    depth = rng.randint(2, 8)
    return Candidate(_candidate_key(rotation, feature, depth, rng.randrange(1, 2**31)),
                     rotation, feature, depth, rng.randrange(1, 2**31))


def _draw_cell(cell: str, rng: random.Random) -> Candidate:
    rotation, feature = cell.split("|")
    depth = rng.randint(2, 8)
    return Candidate(_candidate_key(rotation, feature, depth, rng.randrange(1, 2**31)),
                     rotation, feature, depth, rng.randrange(1, 2**31))


def _candidate_key_of(cell: str, payload: Candidate) -> str:
    return payload.construction_key


def _candidate_descriptor(cell: str, payload: Candidate) -> str:
    return f"{cell}|depth{payload.depth}"


def _depth_validator(cap: int):
    def validate(payload) -> str | None:
        if isinstance(payload, Candidate) and payload.depth > cap:
            return "depth"
        return None
    return validate


def make_baseline_policy(rng: random.Random, cap: int = MAX_DEPTH) -> StratifiedPolicy:
    return StratifiedPolicy(["uniform"], _draw_uniform, _candidate_descriptor, _candidate_key_of, rng,
                            validate=_depth_validator(cap))


def make_challenger_policy(rng: random.Random, cap: int = MAX_DEPTH) -> StratifiedPolicy:
    cells = [f"{rotation}|{feature}" for rotation in ROTATIONS for feature in FEATURES]
    return StratifiedPolicy(cells, _draw_cell, _candidate_descriptor, _candidate_key_of, rng,
                            validate=_depth_validator(cap))


def run_policy(policy: StratifiedPolicy, evaluator: SyntheticEvaluator, target_evaluations: int,
               ledger: Ledger) -> Ledger:
    """Run one policy until `target_evaluations` completed evaluations."""
    while ledger.evaluations < target_evaluations:
        proposal = policy.propose(ledger)
        if proposal is None:
            break
        passed = evaluator.evaluate(proposal.payload)
        ledger.evaluations += 1
        ledger.passed += int(passed)
    return ledger


def compare_equal_counts(baseline: Ledger, challenger: Ledger) -> dict:
    equal = baseline.evaluations == challenger.evaluations
    result = {
        "unit": "synthetic completed evaluations (fixture only)",
        "baseline_evaluations": baseline.evaluations,
        "challenger_evaluations": challenger.evaluations,
        "equal_completed_evaluations": equal,
        "baseline_passed": baseline.passed,
        "challenger_passed": challenger.passed,
        "baseline_yield_rate": round(baseline.passed / baseline.evaluations, 6) if baseline.evaluations else None,
        "challenger_yield_rate": round(challenger.passed / challenger.evaluations, 6) if challenger.evaluations else None,
        "classification": "exploratory" if equal else "not_comparable_unequal_evaluations",
        "claim": "not-a-measured-result",
    }
    result["absolute_yield_difference"] = (
        round(challenger.passed / challenger.evaluations - baseline.passed / baseline.evaluations, 6)
        if equal and baseline.evaluations else None
    )
    return result


def run_smoke(target_evaluations: int, checkpoint_dir: Path | None) -> dict:
    evaluator = SyntheticEvaluator(beneficial_cells={"momentum|flow", "carry|basis"})
    baseline = run_policy(make_baseline_policy(random.Random(SEED)), evaluator, target_evaluations, Ledger())
    challenger = run_policy(make_challenger_policy(random.Random(SEED)), evaluator, target_evaluations, Ledger())

    resume_check = {"performed": False}
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        half = max(1, target_evaluations // 2)
        interrupted_policy = make_challenger_policy(random.Random(SEED + 1))
        interrupted = run_policy(interrupted_policy, evaluator, half, Ledger())
        checkpoint = checkpoint_from_ledger(interrupted_policy, interrupted, extra={"seed": SEED + 1})
        checkpoint_path = checkpoint_dir / "challenger-checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")

        uninterrupted = run_policy(make_challenger_policy(random.Random(SEED + 1)), evaluator,
                                   target_evaluations, Ledger())
        resumed_policy = make_challenger_policy(random.Random(SEED + 1))
        resumed_ledger = new_ledger_from_checkpoint(checkpoint, resumed_policy)
        if checkpoint.get("rng_state") is not None:
            resumed_policy.rng.setstate(decode_rng_state(checkpoint["rng_state"]))
        resumed = run_policy(resumed_policy, evaluator, target_evaluations, resumed_ledger)

        resume_check = {
            "performed": True,
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_evaluations_completed": checkpoint["evaluations_completed"],
            "interrupted_evaluations": interrupted.evaluations,
            "uninterrupted_evaluations": uninterrupted.evaluations,
            "resumed_evaluations": resumed.evaluations,
            "target_evaluations": target_evaluations,
            "stream_reproduced": resumed.history_hashes == uninterrupted.history_hashes,
            "cumulative_totals_match": (
                resumed.evaluations == uninterrupted.evaluations == target_evaluations
                and resumed.passed == uninterrupted.passed
                and resumed.history_hashes == uninterrupted.history_hashes
                and resumed.cell_counts == uninterrupted.cell_counts
                and resumed.descriptor_counts == uninterrupted.descriptor_counts
                and resumed.exhausted_cells == uninterrupted.exhausted_cells
            ),
            "note": "Cumulative totals, cell/descriptor counts, and exhausted scheduling state are compared.",
        }
        if not resume_check["cumulative_totals_match"]:
            resume_check["mismatch"] = {
                "resumed_passed": resumed.passed,
                "uninterrupted_passed": uninterrupted.passed,
                "resumed_cells": resumed.cell_counts,
                "uninterrupted_cells": uninterrupted.cell_counts,
            }

    report = {
        "tool": "coverage_challenger.py",
        "mode": "smoke",
        "data_origin": "synthetic-fixture",
        "claim": "not-a-measured-result",
        "target_completed_evaluations": target_evaluations,
        "reference_target_note": "The campaign reference target is 100000 completed simulations; smoke never claims it.",
        "depth_cap": MAX_DEPTH,
        "policy_core": {
            "shared_by_smoke_and_live": True,
            "module": "stratified_policy.py",
            "accounting_method": "Ledger.accept (one shared accepted-proposal method)",
            "live_policy": "generator-source stratification (not measured feature x rotation coverage)",
            "challenger_cells": len(ROTATIONS) * len(FEATURES),
            "baseline_cells": 1,
            "depth_source": "construction-time descriptor",
            "canonical_identity": "known mapping shape or public serializer; no repro addresses",
        },
        "seed_control_limits": {
            "synthetic_fixture": "seed is explicit for the fixture policies",
            "live": "templates yield opaque graphs; the live policy owns its RNG and records explicit "
                    "checkpoints, but no full deterministic evaluator resume is claimed",
        },
        "dependency_bookkeeping": {
            "recorded_at_construction_time": True,
            "rotation_families": len(ROTATIONS),
            "feature_families": len(FEATURES),
            "cells": len(ROTATIONS) * len(FEATURES),
            "baseline_target_cell_counts": baseline.cell_counts_snapshot(),
            "challenger_target_cell_counts": challenger.cell_counts_snapshot(),
            "challenger_descriptor_counts": challenger.descriptor_counts_snapshot(),
            "challenger_duplicates_rejected": challenger.duplicates_rejected,
            "challenger_depth_rejected": challenger.depth_rejected,
            "challenger_unavailable_rejected": challenger.unavailable_rejected,
            "challenger_exhausted_cells": list(challenger.exhausted_cells),
            "challenger_stopped_reason": challenger.stopped_reason,
            "feature_attribution": "available in the synthetic fixture only; the live surface cannot "
                                   "attribute leaves, so live marks it unavailable",
        },
        "deduplication": {
            "baseline_unique_keys": len(set(baseline.seen_keys)),
            "challenger_unique_keys": len(set(challenger.seen_keys)),
            "challenger_history_hashes": len(challenger.history_hashes),
            "challenger_seen_index_size": len(challenger.seen_index),
        },
        "comparison": compare_equal_counts(baseline, challenger),
        "checkpoint_resume": resume_check,
        "proposals_vs_evaluations": {
            "baseline_accepted_proposals": baseline.proposals,
            "baseline_completed_evaluations": baseline.evaluations,
            "challenger_accepted_proposals": challenger.proposals,
            "challenger_completed_evaluations": challenger.evaluations,
            "challenger_duplicates_rejected": challenger.duplicates_rejected,
            "challenger_depth_rejected": challenger.depth_rejected,
            "challenger_other_rejected": challenger.other_rejected,
            "rule": "rejected, duplicate, unsupported-identity, or depth-over-cap proposals never count "
                    "as accepted proposals or completed evaluations",
        },
        "ga_status": GA_STATUS,
    }
    return report


# ---------------------------------------------------------------------------
# live surface probing
# ---------------------------------------------------------------------------

REQUIRED_LIVE_CALLS = (
    ("openforage", "templates.random_weighted"),
    ("openforage", "templates.genetic"),
    ("openforage", "search"),
    ("openforage", "get_search_stats"),
    ("openforage", "get_yield_analysis"),
    ("openforage", "search_status"),
)
OBSERVED_LIVE_CALLS = (
    ("openforage.search_templates", "measure_graph_complexity"),
    ("openforage.search_templates", "compose_next_batch"),
    ("openforage.catalog.function_loader", "load_function_catalog"),
    ("openforage", "simulate_signal"),
    ("openforage", "SimulationHandle"),
)


def _resolve(module_name: str, dotted: str):
    module = importlib.import_module(module_name)
    current = module
    for part in dotted.split("."):
        current = getattr(current, part)
    return current


def _observation(module_name: str, dotted: str) -> dict:
    try:
        obj = _resolve(module_name, dotted)
    except Exception as exc:  # noqa: BLE001
        return {"call": f"{module_name}.{dotted}", "present": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        signature = "<no signature exposed>"
    doc = (inspect.getdoc(obj) or "").strip().splitlines()
    opaque = signature.replace(" ", "") == "(*args,**kwargs)"
    return {
        "call": f"{module_name}.{dotted}",
        "present": True,
        "callable": callable(obj),
        "signature": signature,
        "doc_signature": doc[0] if doc else "",
        "opaque_proxy": opaque,
        "contract_evidence": "doc-only" if opaque else "introspection",
    }


def live_check() -> tuple[int, dict]:
    report = {
        "mode": "live-check",
        "python": sys.version.split()[0],
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": [],
    }
    failures = []
    try:
        import openforage

        report["openforage_version"] = getattr(openforage, "__version__", "unknown")
    except ImportError as exc:
        return 2, {
            "mode": "live-check",
            "error": f"openforage is not importable in this interpreter: {exc}",
            "remedy": "Install the supported wheel into the environment you run the campaign from.",
        }
    for module_name, dotted in REQUIRED_LIVE_CALLS:
        item = _observation(module_name, dotted)
        item["required"] = True
        report["checks"].append(item)
        if not item["present"] or not item.get("callable"):
            failures.append(item)
    for module_name, dotted in OBSERVED_LIVE_CALLS:
        item = _observation(module_name, dotted)
        item["required"] = False
        report["checks"].append(item)
        if not item["present"]:
            item["status"] = "unavailable-observed-helper"
    report["required_failures"] = failures
    if failures:
        report["result"] = "mismatch"
        report["remedy"] = (
            "The installed package does not expose a required callable. Do not substitute private "
            "attributes; pin a supported wheel (see docs/agents/references/compatibility.json) or "
            "record the gap and stop."
        )
        return 2, report
    report["result"] = "ok"
    return 0, report


# ---------------------------------------------------------------------------
# live challenger callback (shared accounting, documented generator sources)
# ---------------------------------------------------------------------------

GENERATOR_CONTRACT_CAVEAT = (
    "random_weighted(yielded graphs) is documented; genetic is a public callable with an observed CLI "
    "choice whose internal optimizer implementation was not inspected and which is unbenchmarked here. "
    "Both are called only as generator factories yielding one graph per iteration (`next(stream)`), "
    "never mistaken for their yielded graph."
)


def _measure_depth(graph, cap: int) -> tuple[int | None, str | None]:
    """Return (depth, rejection_reason). Only the documented limit error is a depth rejection."""
    import openforage.search_templates as st

    measure = getattr(st, "measure_graph_complexity", None)
    if not callable(measure):
        return None, "measure_graph_complexity unavailable; depth cap cannot be proven"
    try:
        complexity = measure(graph, max_depth=cap)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "GraphComplexityLimitExceeded":
            return None, "depth-limit-exceeded"
        return None, f"measure_graph_complexity raised {type(exc).__name__}: {exc}"
    depth = getattr(complexity, "depth", None)
    return (depth if isinstance(depth, int) else None), None


def _canonical_key(payload) -> str | None:
    """Known mapping/serializer identity; None means explicit unsupported identity."""
    try:
        key, _kind = canonical_payload_key(payload)
        return key
    except UnsupportedIdentity:
        return None


def _default_stream_factory(ctx) -> dict:
    import openforage

    templates = openforage.templates
    streams: dict = {}
    for name in LIVE_CELLS:
        factory = getattr(templates, name, None)
        if not callable(factory):
            raise RuntimeError(f"documented generator openforage.templates.{name} is not callable in this install")
        stream = factory(ctx)
        if not hasattr(stream, "__next__") and not inspect.isgenerator(stream):
            raise RuntimeError(
                f"openforage.templates.{name}(ctx) did not return an iterator/generator; "
                "refusing to treat the generator object as its yielded graph"
            )
        streams[name] = stream
    return streams


def _ledger_snapshot(ledger: Ledger) -> dict:
    return {
        "evaluations": ledger.evaluations,
        "passed": ledger.passed,
        "accepted_proposals": ledger.proposals,
        "duplicates_rejected": ledger.duplicates_rejected,
        "depth_rejected": ledger.depth_rejected,
        "unavailable_rejected": ledger.unavailable_rejected,
        "other_rejected": ledger.other_rejected,
        "cell_counts": ledger.cell_counts_snapshot(),
        "descriptor_counts": ledger.descriptor_counts_snapshot(),
        "exhausted_cells": list(ledger.exhausted_cells),
        "seen_keys_count": len(ledger.seen_keys),
        "history_count": len(ledger.history_hashes),
        "identity_notes": list(ledger.identity_notes),
        "validation_notes": dict(ledger.validation_notes),
        "stopped_reason": ledger.stopped_reason,
    }


def _record_live_dependencies(ledger_path: Path, record: dict) -> None:
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        record["ledger_write_error"] = str(exc)


def _make_challenger_template(ledger_path: Path | None = None, stream_factory=None,
                              depth_fn=None, ledger_out: dict | None = None):
    """Live challenger: generator-source stratification over public generators.

    Shared accounting lives in ``StratifiedPolicy``/``Ledger``; this callback
    never re-implements counters. ``stream_factory``/``depth_fn`` are injection
    points for the controlled stub-stream integration test.
    """
    build_streams = stream_factory or _default_stream_factory
    default_depth_fn = depth_fn

    def challenger(ctx):
        era_depth = getattr(ctx, "depth", None)
        cap = min(MAX_DEPTH, era_depth) if isinstance(era_depth, int) and era_depth > 0 else MAX_DEPTH
        try:
            streams = build_streams(ctx)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"generator stream setup failed: {type(exc).__name__}: {exc}") from exc
        rng = random.Random(SEED)

        def draw_cell(cell: str, _rng: random.Random):
            return next(streams[cell])

        def validate(payload) -> str | None:
            if default_depth_fn is not None:
                depth, reason = default_depth_fn(payload, cap)
            else:
                depth, reason = _measure_depth(payload, cap)
            if reason == "depth-limit-exceeded":
                return "depth"
            if reason:
                raise RuntimeError(f"depth measurement failed for a generator graph: {reason}")
            return None

        def describe(cell: str, payload) -> str:
            if default_depth_fn is not None:
                depth, _reason = default_depth_fn(payload, cap)
            else:
                depth, _reason = _measure_depth(payload, cap)
            return f"depth{depth if depth is not None else 'unknown'}"

        policy = StratifiedPolicy(list(LIVE_CELLS), draw_cell, describe, lambda cell, payload: _canonical_key(payload),
                                  rng, validate=validate)
        ledger = Ledger()

        def finish(reason: str | None = None) -> None:
            if ledger_out is not None:
                ledger_out.update(_ledger_snapshot(ledger))
                ledger_out["depth_cap"] = cap
                ledger_out["depth_cap_source"] = (
                    "min(6, ctx.depth)" if isinstance(era_depth, int) else "6 (era depth unavailable)"
                )
                ledger_out["generator_sources"] = {name: GENERATOR_CONTRACT_CAVEAT for name in LIVE_CELLS}
                ledger_out["feature_attribution"] = "unavailable"
                ledger_out["feature_attribution_reason"] = (
                    "per-candidate public leaf introspection is not exposed"
                )
                ledger_out["stopped_reason"] = reason or ledger.stopped_reason
            if ledger_path is not None:
                _record_live_dependencies(ledger_path, {
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "recorded_at_construction_time": True,
                    **_ledger_snapshot(ledger),
                    "depth_cap": cap,
                    "generator_sources": {name: GENERATOR_CONTRACT_CAVEAT for name in LIVE_CELLS},
                    "feature_attribution": "unavailable",
                    "feature_attribution_reason": "per-candidate public leaf introspection is not exposed",
                })

        while True:
            try:
                proposal = policy.propose(ledger)
            except RuntimeError as exc:
                finish(f"error: {exc}")
                raise
            if proposal is None:
                finish()
                return
            yield proposal.payload

    return challenger


# ---------------------------------------------------------------------------
# stub-stream integration test (never a library evaluation)
# ---------------------------------------------------------------------------

class _RecordingStream:
    """Test-only iterator that records which source was drawn."""

    def __init__(self, label: str, items: list, order: list):
        self.label = label
        self._iter = iter(items)
        self._order = order

    def __iter__(self):
        return self

    def __next__(self):
        value = next(self._iter)
        self._order.append(self.label)
        return value


def run_stub_check() -> dict:
    """Exercise the actual live callback with controlled generator streams."""
    draw_order: list[str] = []

    def make_streams(_ctx):
        return {
            "genetic": _RecordingStream("genetic", [{"graph_id": "g1"}, {"graph_id": "g1"},
                                                    {"graph_id": "g2"}, {"graph_id": "g1"}], draw_order),
            "random_weighted": _RecordingStream("random_weighted", [{"graph_id": "r1"}, {"graph_id": "g1"}], draw_order),
        }

    depth_two = lambda payload, cap: (2, None)
    ledger_out: dict = {}
    template = _make_challenger_template(stream_factory=make_streams, depth_fn=depth_two, ledger_out=ledger_out)
    yielded = list(template(SimpleNamespace(depth=6)))
    accepted = ledger_out.get("accepted_proposals", 0)
    history = ledger_out.get("history_count", -1)
    cells = ledger_out.get("cell_counts", {})

    counter = {"n": 0}

    def make_depth_only_streams(_ctx):
        def generate():
            while True:
                counter["n"] += 1
                yield {"unique": counter["n"]}
        return {"genetic": generate(), "random_weighted": generate()}

    depth_only_ledger: dict = {}
    depth_only_template = _make_challenger_template(
        stream_factory=make_depth_only_streams,
        depth_fn=lambda payload, cap: (None, "depth-limit-exceeded"),
        ledger_out=depth_only_ledger,
    )
    depth_only_yielded = list(depth_only_template(SimpleNamespace(depth=6)))

    checkpoint_negatives = []
    base_checkpoint = {
        "policy": "stratified-coverage",
        "cell_identity": list(LIVE_CELLS),
        "evaluations_completed": 1,
        "proposals": 1,
        "seen_keys": ["a"],
        "exhausted_cells": [],
    }
    probe_policy = StratifiedPolicy(
        list(LIVE_CELLS), lambda cell, rng: {"x": 1}, lambda cell, payload: "d1",
        lambda cell, payload: "k1", random.Random(0))
    for name, mutation in (
        ("bool-count", lambda data: data.update(evaluations_completed=True)),
        ("negative-count", lambda data: data.update(proposals=-1)),
        ("fraction-count", lambda data: data.update(duplicates_rejected=1.5)),
        ("incompatible-cells", lambda data: data.update(cell_identity=["other"])),
        ("incompatible-policy", lambda data: data.update(policy="other-policy")),
    ):
        candidate = json.loads(json.dumps(base_checkpoint))
        mutation(candidate)
        try:
            new_ledger_from_checkpoint(candidate, probe_policy)
        except ValueError as exc:
            checkpoint_negatives.append({"case": name, "rejected": True, "reason": str(exc)[:120]})
        else:
            checkpoint_negatives.append({"case": name, "rejected": False})

    analytics_calls: list[dict] = []

    class _FakeAnalyticsModule:
        @staticmethod
        def get_search_stats(**kwargs):
            analytics_calls.append(kwargs)
            raise TypeError("scoped signature only")

        @staticmethod
        def get_yield_analysis(**kwargs):
            analytics_calls.append(kwargs)
            return {"ok": True}

    capture_probe = _capture_analytics(_FakeAnalyticsModule, "probe-pool", None, {"run_id": "r"})
    analytics_check = (
        bool(analytics_calls)
        and all(call.get("signal_pool_name") == "probe-pool" for call in analytics_calls)
        and capture_probe["get_search_stats"].get("available") is False
        and str(capture_probe["get_search_stats"].get("error", "")).startswith("TypeError")
    )

    # Arm completion gating with fake handles: no live library evaluation.
    def fake_args(**overrides):
        base = dict(signal_pool_name="probe-pool-a", named_universe=None, settings_path=None,
                    history_protocol="stub-v1", target_evaluations=100, stall_seconds=120.0,
                    max_poll_seconds=30.0, poll_seconds=0.0, stop_timeout=1.0, arm="baseline",
                    data_dir=None)
        base.update(overrides)
        return SimpleNamespace(**base)

    class _FakeStatus:
        def __init__(self, total, running):
            self.total_evaluated = total
            self.total_found = 1 if total else 0
            self.running = running
            self.errors = []

    class _FakeHandle:
        def __init__(self, total, running, stop_error=False):
            self._total = total
            self._running = running
            self._stop_error = stop_error
            self.product_run_id = "product-run-123"

        def status(self):
            return _FakeStatus(self._total, self._running)

        def stop(self, join_timeout=None):
            if self._stop_error:
                raise RuntimeError("stop failed")

    class _FakeOpenforage:
        def __init__(self, handle):
            self._handle = handle

        def search(self, template, **kwargs):
            return self._handle

    arm_probes = {}
    for name, handle, overrides, expected in (
        ("complete", _FakeHandle(100, False), {}, True),
        ("missing_count", _FakeHandle(None, True), {}, False),
        ("stop_error", _FakeHandle(100, False, stop_error=True), {}, False),
        ("still_running", _FakeHandle(50, True), {"stall_seconds": 0.0}, False),
    ):
        probe = _run_one(_FakeOpenforage(handle), lambda ctx: iter(()), "stub-arm", fake_args(**overrides), None)
        arm_probes[name] = {
            "complete": probe.get("complete"),
            "status": probe.get("status"),
            "handle_product_run_id": probe.get("handle_product_run_id"),
        }
        if probe.get("complete") is not expected:
            arm_probes[name]["expected_complete"] = expected
    arm_gating_ok = (
        arm_probes["complete"]["complete"] is True
        and arm_probes["missing_count"]["complete"] is False
        and arm_probes["stop_error"]["complete"] is False
        and arm_probes["still_running"]["complete"] is False
        and arm_probes["complete"]["handle_product_run_id"] == "product-run-123"
    )

    checks = {
        "both_sources_observed": sorted(cells) == sorted(LIVE_CELLS) and all(cells.values()),
        "mixing_observed": len(cells) == 2 and accepted >= 3,
        "draw_order_mixes_sources": len(set(draw_order)) == 2,
        "cross_source_duplicate_rejected": ledger_out.get("duplicates_rejected", 0) >= 1,
        "accepted_counts_match_history": accepted == history == len(yielded),
        "finite_streams_terminated": ledger_out.get("stopped_reason") == "all-cells-exhausted",
        "depth_only_stream_terminated": (
            depth_only_ledger.get("stopped_reason") == "max-consecutive-rejections"
            and depth_only_ledger.get("depth_rejected", 0) > 0
            and depth_only_yielded == []
        ),
        "checkpoint_validation_rejects_invalid": all(item["rejected"] for item in checkpoint_negatives),
        "analytics_scoped_only": analytics_check,
        "arm_completion_gating": arm_gating_ok,
    }
    return {
        "mode": "stub-check",
        "claim": "stub-stream-integration-not-a-library-evaluation",
        "data_origin": "controlled-stub-streams",
        "checks": checks,
        "all_passed": all(checks.values()),
        "yielded": len(yielded),
        "draw_order": draw_order,
        "ledger": ledger_out,
        "depth_only_ledger": depth_only_ledger,
        "checkpoint_negatives": checkpoint_negatives,
        "analytics_calls": analytics_calls,
        "arm_probes": arm_probes,
        "note": "Controlled documented-generator stubs exercise the live callback's shared accounting; "
                "this is not a real library evaluation.",
    }


# ---------------------------------------------------------------------------
# explicit single-arm live mode
# ---------------------------------------------------------------------------

RUN_IDENTITY_FIELDS = ("run_id", "algorithm", "signal_pool_name", "named_universe", "started_at")
REQUIRED_CONDITION_FIELDS = (
    "signal_pool_name", "named_universe", "data_dir", "settings_path", "settings_sha256",
    "era_number", "market_id", "eval_type", "declared_history_protocol",
)


def _file_sha256(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def _observed_condition(handle, status_obj, key: str):
    for source in (status_obj, handle):
        value = getattr(source, key, None)
        if value is not None and not callable(value):
            return value
    return None


def _capture_conditions(args, pool: str, handle, status_obj) -> dict:
    conditions = {
        "signal_pool_name": pool,
        "named_universe": args.named_universe if args.named_universe is not None else "<default>",
        "data_dir": args.data_dir if args.data_dir is not None else "<default>",
        "settings_path": args.settings_path if args.settings_path is not None else "<default>",
        "settings_sha256": _file_sha256(args.settings_path) or "<none>",
        "era_number": _observed_condition(handle, status_obj, "era_number"),
        "market_id": _observed_condition(handle, status_obj, "market_id"),
        "eval_type": _observed_condition(handle, status_obj, "eval_type"),
        "declared_history_protocol": args.history_protocol,
        "declaration_note": "declared_history_protocol and independent state/history are caller-declared, "
                            "not verified by this runner",
    }
    conditions["conditions_unknown"] = [
        key for key in ("era_number", "market_id", "eval_type") if conditions.get(key) is None
    ]
    return conditions


def _capture_analytics(openforage, pool: str, capture_dir: Path | None, identity: dict) -> dict:
    capture: dict = {"run_identity": identity, "conditions": identity.copy()}
    for name in ("get_search_stats", "get_yield_analysis"):
        func = getattr(openforage, name, None)
        if not callable(func):
            capture[name] = {"available": False, "reason": "not callable in this install"}
            continue
        try:
            capture[name] = func(signal_pool_name=pool)
        except Exception as exc:  # noqa: BLE001
            capture[name] = {"available": False, "error": f"{type(exc).__name__}: {exc}",
                             "note": "scoped call failed; no unscoped fallback is attempted"}
    if capture_dir is not None:
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / f"{identity['algorithm']}.capture.json"
        path.write_text(json.dumps(capture, indent=2, default=str) + "\n", encoding="utf-8")
        capture["capture_path"] = str(path)
    return capture


def _status_counts(status_obj) -> dict:
    counts = {}
    for key in ("total_evaluated", "total_found", "errors"):
        value = getattr(status_obj, key, None)
        if isinstance(value, bool):
            value = None
        if isinstance(value, (int, list)):
            counts[key] = value
    return counts


def _run_one(openforage, template, algorithm: str, args, capture_dir: Path | None) -> dict:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    identity = {
        "run_id": f"{algorithm}-{uuid.uuid4().hex[:12]}",
        "algorithm": algorithm,
        "signal_pool_name": args.signal_pool_name,
        "named_universe": args.named_universe,
        "started_at": started_at,
    }
    kwargs = {"n_jobs": 1}
    if args.named_universe is not None:
        kwargs["named_universe"] = args.named_universe
    if args.settings_path is not None:
        kwargs["settings_path"] = args.settings_path
    result: dict = {
        "arm": args.arm,
        "algorithm": algorithm,
        "run_identity": identity,
        "handle_product_run_id": None,
        "conditions": None,
        "target_completed_evaluations": args.target_evaluations,
        "status": "unknown",
    }
    handle = None
    try:
        handle = openforage.search(template, signal_pool_name=args.signal_pool_name, **kwargs)
        result["handle_product_run_id"] = getattr(handle, "product_run_id", None)
        last_count = None
        last_change = time.time()
        poll_deadline = time.time() + args.max_poll_seconds
        while time.time() < poll_deadline:
            status_obj = handle.status()
            counts = _status_counts(status_obj)
            total = counts.get("total_evaluated")
            if total is None:
                result["status"] = "unknown_total_evaluated"
                break
            if total >= args.target_evaluations:
                result["status"] = "target_reached"
                break
            if total != last_count:
                last_count = total
                last_change = time.time()
            elif time.time() - last_change > args.stall_seconds:
                result["status"] = "stalled"
                break
            running = getattr(status_obj, "running", True)
            if running is False:
                result["status"] = "template_exhausted"
                break
            time.sleep(args.poll_seconds)
        else:
            result["status"] = "poll_timeout"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if handle is not None:
            try:
                handle.stop(join_timeout=args.stop_timeout)
            except Exception as exc:  # noqa: BLE001
                result["stop_error"] = f"{type(exc).__name__}: {exc}"
            try:
                final_status = handle.status()
                counts = _status_counts(final_status)
                result["final_counts"] = counts
                result["final_running"] = getattr(final_status, "running", None)
                total = counts.get("total_evaluated")
                result["overshot_target"] = isinstance(total, int) and total > args.target_evaluations
                result["conditions"] = _capture_conditions(args, args.signal_pool_name, handle, final_status)
            except Exception as exc:  # noqa: BLE001
                result["final_status_error"] = f"{type(exc).__name__}: {exc}"
        result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["capture"] = _capture_analytics(openforage, args.signal_pool_name, capture_dir, identity)
        total = (result.get("final_counts") or {}).get("total_evaluated")
        result["complete"] = (
            result.get("status") in {"target_reached", "template_exhausted"}
            and isinstance(total, int) and total > 0
            and result.get("final_running") is not True
            and "stop_error" not in result
            and "error" not in result
        )
        if result.get("conditions") is None:
            result["conditions"] = _capture_conditions(args, args.signal_pool_name, handle, None)
        if capture_dir is not None:
            capture_dir.mkdir(parents=True, exist_ok=True)
            summary = {
                "run_identity": identity,
                "handle_product_run_id": result.get("handle_product_run_id"),
                "conditions": result["conditions"],
                "total_evaluations": total,
                "total_found": (result.get("final_counts") or {}).get("total_found"),
                "complete": result["complete"],
                "source": "coverage_challenger.py --live-run single-arm snapshot after stop()",
            }
            (capture_dir / f"{algorithm}.summary.json").write_text(
                json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def live_run(args) -> int:
    if not args.confirm_live:
        print("FAIL: --live-run requires --confirm-live; this mode starts real local evaluation and may submit signals.")
        return 2
    if not args.signal_pool_name:
        print("FAIL: --live-run requires --signal-pool-name (the actual public search signature requires it).")
        return 2
    if not args.arm:
        print("FAIL: --live-run requires --arm baseline|challenger; one process runs one arm only.")
        return 2
    if not args.history_protocol:
        print("FAIL: --live-run requires --history-protocol: independent state/history for the two arms is "
              "a caller requirement and is recorded, not fabricated.")
        return 2
    status, check = live_check()
    print(json.dumps(check, indent=2))
    if status != 0:
        return status
    import openforage

    if args.data_dir:
        openforage.configure(data_dir=args.data_dir)
    templates = openforage.templates
    if args.arm == "baseline":
        algorithm = args.baseline_algorithm
        if not callable(getattr(templates, algorithm, None)):
            print(f"FAIL: baseline template openforage.templates.{algorithm} is not callable in this install")
            return 2
        template = getattr(templates, algorithm)
    else:
        algorithm = "coverage-stratified"
        template = _make_challenger_template(
            Path(args.dependency_ledger) if args.dependency_ledger else None)
    capture_dir = Path(args.capture_dir) if args.capture_dir else None
    result = {
        "mode": "live-run",
        "arm": args.arm,
        "data_dir": args.data_dir,
        "target_completed_evaluations": args.target_evaluations,
        "allowed_signal_pool_name": args.signal_pool_name,
        "declared_history_protocol": args.history_protocol,
        "classification": "exploratory",
        "ga_status": GA_STATUS,
        "generator_contract_caveat": GENERATOR_CONTRACT_CAVEAT,
        "claim_boundary": (
            "Exploratory single-arm live run. Independent state/history for the two arms is caller-declared "
            "and recorded, not verified here; no pool name is ever rewritten. The genetic internal optimizer "
            "implementation was not inspected and no full GA benchmark has run; matched budgets/seeds and "
            "coverage evidence required by docs/agents/research-campaign.md are not established here."
        ),
        "run": _run_one(openforage, template, algorithm, args, capture_dir),
    }
    print(json.dumps(result, indent=2, default=str))
    if not result["run"].get("complete"):
        print("FAIL: live arm did not complete cleanly; no comparison or second arm is started.", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------
# ingest validation
# ---------------------------------------------------------------------------

def _validate_capture(data, role: str) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, f"{role}: payload is not a JSON object"
    if "run_identity" not in data:
        capture = data.get("capture")
        if isinstance(capture, dict) and "run_identity" in capture:
            data = {**data, **capture}
    missing = [field for field in ("run_identity", "conditions") if field not in data]
    if missing:
        return None, f"{role}: missing required capture fields {missing}"
    identity = data.get("run_identity")
    if not isinstance(identity, dict) or not all(
        isinstance(identity.get(key), str) for key in ("run_id", "algorithm", "signal_pool_name")
    ):
        return None, f"{role}: run_identity must name run_id, algorithm, and signal_pool_name"
    conditions = data.get("conditions")
    if not isinstance(conditions, dict):
        return None, f"{role}: conditions must be an object"
    missing_conditions = [key for key in REQUIRED_CONDITION_FIELDS if key not in conditions]
    if missing_conditions:
        return None, f"{role}: conditions missing evidence fields {missing_conditions}"
    unknown = conditions.get("conditions_unknown")
    if not isinstance(unknown, list):
        return None, f"{role}: conditions.conditions_unknown must be a list"
    value = data.get("total_evaluations")
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{role}: total_evaluations must be an integer (booleans/fractions rejected)"
    if value < 0:
        return None, f"{role}: total_evaluations must not be negative"
    if value == 0:
        return None, f"{role}: zero completed evaluations cannot support a comparison"
    return data, None


IMMUTABLE_CONDITION_FIELDS = (
    "named_universe", "data_dir", "settings_path", "settings_sha256", "era_number", "market_id", "eval_type",
)


def _conditions_report(baseline: dict, challenger: dict) -> dict:
    base_conditions = baseline["conditions"]
    chall_conditions = challenger["conditions"]
    unknown = {
        "baseline": list(base_conditions.get("conditions_unknown") or []),
        "challenger": list(chall_conditions.get("conditions_unknown") or []),
    }
    if unknown["baseline"] or unknown["challenger"]:
        return {"status": "not_comparable_unknown_conditions", "unknown": unknown}
    if base_conditions.get("declared_history_protocol") != chall_conditions.get("declared_history_protocol"):
        return {"status": "not_comparable_history_protocol",
                "baseline": base_conditions.get("declared_history_protocol"),
                "challenger": chall_conditions.get("declared_history_protocol")}
    if base_conditions.get("signal_pool_name") == chall_conditions.get("signal_pool_name"):
        return {"status": "not_comparable_same_pool",
                "signal_pool_name": base_conditions.get("signal_pool_name"),
                "note": "the two arms must not share a signal pool/history identity"}
    differences = []
    for key in IMMUTABLE_CONDITION_FIELDS:
        if base_conditions.get(key) != chall_conditions.get(key):
            differences.append(f"{key}: {base_conditions.get(key)!r} != {chall_conditions.get(key)!r}")
    if differences:
        return {"status": "not_comparable_conditions_mismatch", "differences": differences}
    return {"status": "conditions_matched",
            "signal_pool_names": [base_conditions.get("signal_pool_name"),
                                  chall_conditions.get("signal_pool_name")],
            "declared_history_protocol": base_conditions.get("declared_history_protocol")}


def ingest_report(args) -> int:
    report = {"mode": "ingest", "claim": "not-a-measured-result", "comparison": None}
    try:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        challenger = json.loads(Path(args.challenger).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read comparison inputs: {exc}")
        return 2
    base, base_error = _validate_capture(baseline, "baseline")
    chall, chall_error = _validate_capture(challenger, "challenger")
    if base_error or chall_error:
        report["comparison"] = {"status": "invalid_input", "reason": base_error or chall_error}
        print(json.dumps(report, indent=2))
        return 2
    condition_report = _conditions_report(base, chall)
    report["counts"] = {
        "baseline_total_evaluations": base["total_evaluations"],
        "challenger_total_evaluations": chall["total_evaluations"],
    }
    report["run_identities"] = {
        "baseline": base["run_identity"],
        "challenger": chall["run_identity"],
    }
    if condition_report["status"] != "conditions_matched":
        report["comparison"] = condition_report
    elif base["total_evaluations"] != chall["total_evaluations"]:
        report["comparison"] = {
            "status": "not_comparable_unequal_counts",
            "reason": "completed evaluation counts differ; do not compare yields across unequal counts",
        }
    else:
        report["comparison"] = {
            "status": "equal_counts_ready",
            "conditions": condition_report,
            "baseline_total_found": base.get("total_found"),
            "challenger_total_found": chall.get("total_found"),
            "baseline_lifetime_yield_rate": base.get("lifetime_yield_rate"),
            "challenger_lifetime_yield_rate": chall.get("lifetime_yield_rate"),
            "note": "Equal integers alone are not evidence; observed conditions matched and identities were recorded.",
        }
    print(json.dumps(report, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", "--selfcheck", dest="smoke", action="store_true")
    mode.add_argument("--stub-check", action="store_true",
                      help="controlled stub-stream integration test of the live callback (offline)")
    mode.add_argument("--live-check", action="store_true")
    mode.add_argument("--live-run", action="store_true")
    mode.add_argument("--ingest", action="store_true")
    parser.add_argument("--target-evaluations", type=int, default=SMOKE_DEFAULT_EVALUATIONS)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--challenger", default=None)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--arm", choices=("baseline", "challenger"), default=None)
    parser.add_argument("--history-protocol", default=None,
                        help="caller-declared independent state/history protocol label (recorded, not verified)")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--signal-pool-name", default=None)
    parser.add_argument("--named-universe", default=None)
    parser.add_argument("--settings-path", default=None)
    parser.add_argument("--baseline-algorithm", choices=("genetic", "random_weighted"), default="genetic")
    parser.add_argument("--dependency-ledger", default=None)
    parser.add_argument("--capture-dir", default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--stall-seconds", type=float, default=120.0)
    parser.add_argument("--max-poll-seconds", type=float, default=3600.0)
    parser.add_argument("--stop-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if not any((args.smoke, args.stub_check, args.live_check, args.live_run, args.ingest)):
        parser.print_help()
        return 2
    if args.stub_check:
        report = run_stub_check()
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["all_passed"] else 1
    if args.live_check:
        status, report = live_check()
        print(json.dumps(report, indent=2))
        return status
    if args.live_run:
        return live_run(args)
    if args.ingest:
        if not args.baseline or not args.challenger:
            print("FAIL: --ingest requires --baseline and --challenger JSON files")
            return 2
        return ingest_report(args)
    if args.target_evaluations <= 0:
        print("FAIL: --target-evaluations must be positive")
        return 2
    report = run_smoke(args.target_evaluations, Path(args.checkpoint_dir) if args.checkpoint_dir else None)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
