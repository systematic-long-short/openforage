# Research Opportunity Map

<!-- BEGIN GENERATED ENTRY GUIDANCE -->
**Offline first.** Read the research-opportunity map, choose one proposed direction, and write down its source claim, a falsifiable mechanism/hypothesis, the public capabilities it needs, and the evidence gaps. Keep this offline until the user explicitly authorizes a live campaign. For documentation work, use [`docs/agents/first-contribution.md`](first-contribution.md). Reading, research, and contributions need no invite, wallet, installed package, daemon, or worker. Source: [`docs/agents/research-opportunities.json`](research-opportunities.json).
Without Python or automatic root-instruction loading, read or paste [`docs/agents/start-here.md`](start-here.md).
**Authorized live path.** A live campaign requires explicit user authorization; follow [`README.md`](../../README.md) for its bootstrap. Continuous search requires an independently started CLI process of the same harness with a distinct session identity. If that identity is not established, keep work offline; never use the main conversation. Python search snippets below are for that child only.
**Canonical algorithm/reference facts.** Tested-supported `0.2.36` records templates `genetic`, `random_weighted` and CLI choices `random_weighted`, `genetic`. It documents `genetic` as default; CLI help prints no default. The GA reference target is `genetic` with 100,000 completed simulations—target only, with no full GA benchmark measured. Sources: [`docs/agents/references/compatibility.json`](references/compatibility.json), [`examples/agent-campaign/campaign.json`](../../examples/agent-campaign/campaign.json).
<!-- END GENERATED ENTRY GUIDANCE -->

The authoritative structured index is
[`research-opportunities.json`](research-opportunities.json). This page is
the readable rendering; when the two disagree, the JSON wins. Entries are
**proposals and directions, not results** and not superiority claims. A
citation names a primary source and a starting point, never proof that a
mechanism works on OpenForage. Every primary source URL in the index was
reopened on 2026-09-23 and matched the cited title; reopen it again before
relying on its mechanism.

## First step: offline source, hypothesis, and evidence gaps

Start with the public proposal map and its attributed primary source. Choose
one falsifiable research question, summarize what the source claims, describe
the mechanism that could answer it, and list the public capabilities and
evidence that are still missing. A paper motivates a hypothesis; it does not
prove that the mechanism works on OpenForage. This work can proceed before
checking an installed package or any campaign access.

**Optional one-action aid:** `python scripts/research_next.py` emits one
deterministic read-only proposal from the public map; add `--campaign`,
`--coverage`, `--experiments`, and/or `--context` only for existing evidence.
No inputs or template-only inputs still produce useful offline paper/mechanism
work. The helper never mutates a campaign or starts work, and its output is
not a scheduler or task database; the selected harness owns native handoff,
goals, tasks, memory, scheduling, and execution. Run
`python scripts/research_next.py --selfcheck` for offline regressions.

The intended reference is `genetic` (GA) with 100,000 completed simulations
as a target, not an observed result. Compare substantive paper-backed
challengers with matched budgets and completed-evaluation counts. Seek broad
valid feature × rotation coverage only from public observations, and report
out-of-sample correlation and usefulness only when those measurements are
exposed; otherwise keep them unknown.

## How to pick an opportunity

1. Read the case's `required_public_capabilities` and the public
   compatibility record; do not invent an unavailable call. Re-verify the
   installed surface only if your next step depends on installed behavior and
   the package is already available. A missing package does not block this
   offline research step.
2. Read the primary source again before relying on its mechanism. Label
   every claim `documented`, `observed`, `unverified`, `unavailable`, or
   `exploratory`.
3. Follow [research-campaign.md](research-campaign.md) and the
   `search_research` skill: preregister before results, freeze a compatible
   baseline, match budgets and seeds, compare at equal completed
   evaluations, account for pending server outcomes separately, and record
   the complete trial inventory including negative and abandoned runs.
4. Missing metrics stay `unknown`. Reopen the source and fix the map entry
   when new evidence arrives.

Depth cap: no generated, evaluated, or submitted candidate may exceed a
compute-graph depth of 6, regardless of the era's `max_depth`.

## Families at a glance

| Opportunity | Family | Status | Public capability gate |
|---|---|---|---|
| [Coverage-stratified sampling](#coverage-stratified-sampling) | quality-diversity | proposed | catalog + runtime vocabulary + analytics |
| [Diversity-weighted batch composition](#diversity-archive-batch-composition) | quality-diversity | proposed | `compose_next_batch` mapping-factory behavior observed, full contract unproved, not used in any runnable path |
| [Archive-guided novelty pressure](#novelty-pressure-archive) | novelty search | proposed | construction-time bookkeeping only |
| [Staged budget screening](#staged-budget-screening) | budget allocation | proposed | in-process `SearchHandle` + analytics |
| [Parsimony pressure](#parsimony-pressure) | complexity control | proposed | `measure_graph_complexity` |
| [Surrogate-guided acquisition](#surrogate-acquisition) | surrogate model | proposed | observed `simulate_signal`/`SimulationHandle` handle loop; live permissions/data/behavior untested |

## coverage-stratified-sampling

- **Question.** Does steering generation toward under-evaluated construction
  cells find qualifying signals that uniform `random_weighted` sampling
  misses at the same completed-evaluation count?
- **Mechanism.** Keep a construction-time coverage ledger over enumerable
  cells (the runnable implementation stratifies over the documented generator
  callables and their measured depths; the synthetic fixture stratifies over
  rotation x feature cells). Target the least-covered cell, bound duplicate
  attempts, count duplicates and depth rejections separately from completed
  evaluations, and checkpoint the policy RNG/history. If exact feature
  attribution is not observable, record it as `unavailable` with a reason
  instead of the full catalogue or fabricated cells.
- **Primary source.** [MAP-Elites](https://arxiv.org/abs/1504.04909)
  (starting point, not superiority proof).
- **Design.** Matched baseline and challenger on the same era/config, seed
  set, and equal completed evaluations; the live runner defaults to the
  installed `genetic` template with `random_weighted` as the explicit
  alternative, isolates each arm by `signal_pool_name`, and snapshots counts
  only after `stop()`. Compare `get_search_stats` rows and
  `get_yield_analysis`; per-era segments only.
- **Runnable companion.** `examples/agent-campaign/coverage_challenger.py`
  with `stratified_policy.py` (`--smoke` offline; `--live-run --confirm-live
  --signal-pool-name <pool> --baseline-algorithm genetic|random_weighted`
  explicitly opt-in).
- **Acceptance evidence.** Preregistration, paired runs, equal-count
  comparison, coverage ledger checkpoints, complete trial inventory.
- **Status / next action.** `proposed`; run the compatibility check, then
  preregister and freeze a compatible baseline before the first run.

## diversity-archive-batch-composition

- **Question.** Does composing batches with explicit diversity and parsimony
  pressure improve yield at equal completed evaluations?
- **Mechanism.** The offline probe of
  `openforage.search_templates.compose_next_batch` established that a
  factory must return one mapping (batch entries are copies plus
  `lineage='fresh_random'`), a generator-returning factory yields an empty
  batch, `prior` entries must support `.get`, and non-graph payloads are
  dropped when `max_depth` is supplied. The full call contract remains
  unproved, so the composer is **not used in any runnable path**; the live
  challenger uses documented generator callables directly.
- **Primary source.** [Quality and Diversity Optimization: A Unifying Modular Framework](https://arxiv.org/abs/1708.09251)
  (starting point only).
- **Design.** Baseline versus composed-batch template under matched budgets
  and seeds; `get_search_patterns` depth distribution as a secondary
  diagnostic.
- **Acceptance evidence.** A fully observed composer contract, proven depth-6
  self-rejection, preregistered comparison, equal-count statistics.
- **Status / next action.** `proposed`; keep using the stratified generator
  path (or prove the remaining composer contract with real graph objects)
  before wiring the composer into a live run; record the limitation either
  way.

## novelty-pressure-archive

- **Question.** Does rewarding novelty of the construction key rather than
  the evaluator score widen valid coverage without lowering lifetime yield
  past the preregistered minimum effect?
- **Mechanism.** Archive construction keys and de-prioritize dense keys.
  Per-candidate leaf introspection is not a public API, so only
  construction-time bookkeeping is used.
- **Primary source.** [Novelty search](https://doi.org/10.1162/EVCO_a_00025).
- **Design.** Equal-count comparison against the campaign baseline (installed
  `genetic` by default, or `random_weighted` as the documented alternative;
  no full GA benchmark has run) with a preregistered minimum meaningful
  effect; exploratory unless the full measured protocol holds.
- **Acceptance evidence.** Key distribution per checkpoint, proven depth
  cap, equal-count comparison, usefulness metrics with provenance.
- **Status / next action.** `proposed`; define the construction-key schema
  and preregister the novelty weight before sampling.

## staged-budget-screening

- **Question.** Can staged promotion of promising configurations reach the
  same qualifying-signal count with fewer completed evaluations than flat
  allocation?
- **Mechanism.** Split matched budgets into preliminary and confirmation
  stages; promote only variants clearing a preregistered early threshold.
  Resource limits cover simulations, time, CPU, RAM, storage, and
  concurrency only.
- **Primary source.** [A System for Massively Parallel Hyperparameter Tuning (ASHA)](https://arxiv.org/abs/1810.05934).
- **Design.** Flat-budget runs versus staged screening with total simulation
  budgets matched; compare at equal completed-evaluation totals, record
  wall-clock separately.
- **Acceptance evidence.** Preregistered stages and thresholds, per-stage
  checkpoints, equal total budget accounting, abandoned variants retained.
- **Status / next action.** `proposed`; preregister stage sizes and verify
  the analytics can segment stages before running.

## parsimony-pressure

- **Question.** Does preferring lower node-count graphs inside the depth-6
  cap change lifetime yield or submission quality at equal completed
  evaluations?
- **Mechanism.** Order candidates within a batch by
  `measure_graph_complexity` node count as a tie-breaker; depth stays <= 6.
- **Primary source.** [MAP-Elites](https://arxiv.org/abs/1504.04909) as the
  design ancestor; not evidence for this specific effect.
- **Design.** Same template with and without parsimony ordering, matched
  budgets and seeds; depth distribution reported as a control.
- **Acceptance evidence.** Per-candidate complexity recorded, proven depth
  cap, matched comparison, effect size and uncertainty or an explicit
  exploratory label.
- **Status / next action.** `proposed`; confirm node counts are returned on
  yielded objects in your install first.

## surrogate-acquisition

- **Question.** Can a cheap surrogate over construction parameters propose
  batches with a higher qualifying rate at equal completed evaluations?
- **Mechanism.** Fit a surrogate on completed local outcomes and choose next
  construction parameters by acquisition score. The observed public surface
  supports an explicit per-candidate loop:
  `openforage.simulate_signal(graph, *, market_id, era_number,
  signal_pool_name, record=True, ...)` returns a `SimulationHandle`
  immediately, and the handle exposes `status()`, `result()` (block until
  completion, `BacktestResult` or raise), and `cancel()`. Presence of these
  signatures/docstrings is observed; live permissions, data availability, and
  behavior are untested, and the campaign must supply the real era/config/pool.
  Do not assume a successful simulation.
- **Primary source.** [Practical Bayesian Optimization](https://arxiv.org/abs/1206.2944)
  (starting point, not a measured claim).
- **Design.** Equal-count comparison against the campaign baseline (installed
  `genetic` by default; `random_weighted` explicit), with the acquisition
  fitted only on training-segment outcomes, once the handle loop's live
  behavior is observed in a user-scoped campaign.
- **Acceptance evidence.** Per-candidate outcomes with timestamps from an
  observed `simulate_signal`/`result` loop, a user-supplied real
  era/config/pool, held-out segments, and preregistered acquisition
  parameters. API presence alone is not evidence of live permissions, data
  access, or behavior.
- **Status / next action.** `proposed`; probe one bounded `simulate_signal`
  call in a user-approved campaign and record status/result/cancel behavior
  and permissions before any unattended use. Until then, keep surrogate work
  exploratory and never claim a successful simulation.

## Negative results

The structured index carries an empty `negative_results` list. Empty means
"nothing observed yet in this packet", not "every direction succeeds".
Negative and inconclusive campaign findings are recorded with the same rigor
as positive ones in the trial inventory, then linked here with evidence.
