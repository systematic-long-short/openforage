# Agent Campaign Records — Templates

These are **record templates**, not measured results. Nothing in this
directory is a benchmark outcome, a campaign completion, or a claim about
the installed library's behavior. Copy the directory, replace every
`<placeholder>` / `null` with values from your own campaign, and keep the
honest labels below.

The record contract is in
[`docs/agents/research-campaign.md`](../../docs/agents/research-campaign.md),
the authoritative states and edges are in
[`docs/agents/references/campaign-state-machine.json`](../../docs/agents/references/campaign-state-machine.json),
and the `search_research` skill
(`src/openforage/skills/search_research/SKILL.md`) is the condensed operating
reference. Campaign metadata lives with the campaign — `.openforage-research/`
by convention — and is **never** written into `.openforage/settings.yaml` or
the OpenForage data directory.

## Files

| File | Record |
|---|---|
| `campaign.json` | Campaign manifest: identity, state machine and append-only transitions, frozen reference and compatible baseline, preregistration, statistics, pipeline stages, budgets/seeds, coverage ledger, usefulness metrics, era/config segments, submissions, checkpoints, execution resources, provenance, shared results, negative findings. |
| `research.md` | Narrative walkthrough of **paper → mechanism → hypothesis → custom algorithm → measured comparison**, including preregistration, statistics, usefulness metrics, coverage, resources, provenance, security, shared results, and honest OOS language. |
| `experiments.jsonl` | Complete trial inventory: one JSON object per line per trial, including failed, negative, and abandoned runs, with matched-budget/seed metadata, per-arm completed evaluator counts separate from budgets, and a measured/exploratory classification. |
| `coverage.jsonl` | One JSON object per line: per-era/config feature × rotation pair records plus era summary records (valid, evaluated, dependency, gaps), with attribution provenance and no filler/duplicate graphs. |
| `benchmark_record.json` | Frozen reference record: explicit availability, identifier/hash when available, the `100000` completion target versus the observed count, recorded algorithm identity, and compatible-baseline creation rules. A reference record, not a run. |
| `experiment_record.json` | Single experiment record with preregistration reference, hypothesis, statistics, matched budgets/seeds, segments, resources, provenance, pending/ambiguous server outcomes, and negative findings. |
| `challenger_design.md` | Coverage-oriented challenger design. The implementation is limited to the installed public API that was verified; missing APIs are stated instead of invented, and pseudocode is labeled not runnable. |
| [`next-action/`](next-action/README.md) | Optional deterministic, read-only next-action helper contract, context template, and synthetic regression coverage. |
| `coverage_challenger.py` | Executable baseline/challenger entry point. `--smoke` (offline, explicitly synthetic fixture data), `--stub-check` (offline controlled stub-stream integration test of the live callback, never a library evaluation), `--live-check` (public API probe), `--live-run --arm baseline\|challenger` (explicit one-arm live mode using the exact caller pool/settings), `--ingest` (validate and compare two captures at equal completed counts). |
| `stratified_policy.py` | Shared construction-stratified policy/accounting core used by both smoke and the live challenger callback: enumerable cells, least-covered targeting, bounded attempts, duplicate accounting, checkpointable RNG/history. |

The executable example is the runnable companion to `challenger_design.md`:

```bash
python examples/agent-campaign/coverage_challenger.py --help
python examples/agent-campaign/coverage_challenger.py --smoke --checkpoint-dir .tmp/challenger-checkpoint
python examples/agent-campaign/coverage_challenger.py --stub-check   # controlled stub streams, offline
python examples/agent-campaign/coverage_challenger.py --live-check   # requires an installed openforage
```

Offline smoke output is **synthetic fixture data**, labeled
`data_origin=synthetic-fixture` and `claim=not-a-measured-result`. It never
claims the 100000-completion target and never reports `.tmp` output as real
performance. `--stub-check` is a controlled stub-stream integration test of
the live callback's shared accounting, labeled
`stub-stream-integration-not-a-library-evaluation`.

The optional next-action helper is independent of the challenger executable:
`python scripts/research_next.py --selfcheck` uses synthetic in-memory records
only and performs no package, network, worker, or model calls.

Live mode is single-arm and never rewrites pool names:

```bash
# One process per arm, exact caller-supplied pool and settings; independent
# state/history for the two arms is a documented caller requirement.
python examples/agent-campaign/coverage_challenger.py --live-run --arm baseline --confirm-live \
  --signal-pool-name <baseline-pool> --history-protocol independent-state-v1 \
  --baseline-algorithm genetic --capture-dir .tmp/captures
python examples/agent-campaign/coverage_challenger.py --live-run --arm challenger --confirm-live \
  --signal-pool-name <challenger-pool> --history-protocol independent-state-v1 \
  --settings-path <settings> --capture-dir .tmp/captures
python examples/agent-campaign/coverage_challenger.py --ingest \
  --baseline .tmp/captures/genetic.summary.json \
  --challenger .tmp/captures/coverage-stratified.summary.json
```

It uses the actual public signature
`openforage.search(template, named_universe=..., signal_pool_name=...,
n_jobs=1, settings_path=...)`, snapshots counts only after `stop()`, and
reports unequal/unknown accounting as such. Ingest requires observed immutable
conditions (era/market/eval, universe, data identity, settings digest) and a
matching caller-declared history protocol; unknown or mismatched conditions
are never comparison-ready, even when raw counts match. Where the public
custom-template callback supports generation but not synchronous evaluator
observation, the run is labeled exploratory and the limitation is printed
instead of an invented result. The retired `compose_next_batch` guess is not
used in any runnable path.

## Public-API safety

- The templates and the challenger design were checked against the installed
  public wheel `openforage==0.2.36` (Python 3.12, Linux x86_64). Re-verify
  every call against your own installed version before relying on it.
- Only README-documented APIs are used for the standard loop
  (`openforage.search`, `openforage.templates.random_weighted`,
  `openforage.status` is **not** an API — use `openforage.search_status`;
  analytics are `openforage.get_search_*`). The challenger design also names
  importable helpers from the installed wheel and marks which of them are not
  documented in the README.
- The source-only `search_research` skill may not be present in your
  installed wheel. Run `openforage.list_skills()` and read exactly what is
  installed.
- Harness spawn, pause, resume, stop, and probe commands are **unresolved**:
  no adapter in this packet is verified. The background research agent must
  be a separate CLI invocation of the same harness with a **distinct session
  identity** (`.openforage-research/agents.json` by convention); a Python
  worker, same-session subagent, main-session heartbeat, or resumed main
  session does not qualify.
- **Native tooling first.** The child uses the runtime's native goals, task
  stores, schedules, memory, delegation, and background controls. A plan
  record (for example Codex `update_plan`) is never a scheduler; register the
  `run_due_callbacks()` tick with a native or verified external scheduler.

## Honesty labels to keep

- **In-sample vs out-of-sample.** Local evaluator results are in-sample
  simulation results. Only the protocol's server-side evaluation is
  out-of-sample.
- **Measured vs exploratory.** A comparison is measured only when it covers
  all five pipeline stages under matched budgets and seeds and follows the
  preregistration. Anything else is exploratory — but **exploratory never
  means unmeasured**: a result with real measurements is still measured data
  and is labeled exploratory, never described as "not measured".
- **Pending is not accepted.** Track `submitted`, `pending`, `accepted`,
  `rejected`, `ambiguous`, and the `server_results_through` cutoff. An
  ambiguous acknowledgment is neither accepted nor rejected and is never
  blindly resubmitted.
- **Coverage is sparse.** Record valid, evaluated, and dependency pairs
  separately; do not generalize from observed dependencies to the catalogue.
  No filler graphs, transformed duplicates, or coverage-cell gaming.
- **Catalogue vs loaded.** `catalogue_features` and `loaded_features` are
  measured per era/config; only loaded features can be evaluated.
- **Frozen reference and compatible baselines.** The `100000` count is a
  completion target, not an observed run. Model reference availability
  explicitly: a missing reference keeps null id/hash/observed count and a
  stated reason, and a real compatible baseline is created if none exists,
  frozen before the run and evidenced after, with distinct ids for
  additional runs. Do not regenerate or replace a recorded reference, never
  claim a measured GA result (the genetic callable is installed but no full
  GA benchmark has run), and do not present the
  template as having completed anything.
- **State is authoritative.** Transitions follow
  `docs/agents/references/campaign-state-machine.json`; failed guards change
  nothing, resume targets are preserved, and terminal outcomes do not
  silently reactivate.
- **Limits are declared honestly.** A resource limit with no enforcement
  mechanism is recorded as unenforced, not unlimited.
- **Usefulness metrics.** `unavailable` carries a reason and a null value,
  never `0`. Structural co-occurrence is not return correlation.
- **Hashes attest integrity, not truth.** Sanitize exports; provenance and
  hashes never include secrets.
- **Depth 6.** No generated, evaluated, or submitted candidate may exceed
  compute-graph depth 6, regardless of the era's `max_depth`.

## Validating a filled-in copy

Create the isolated docs environment first (never install globally; some
interpreters are externally managed under PEP 668), then run the validator:

```bash
python -m venv .venv-docs
. .venv-docs/bin/activate                 # Windows (PowerShell): .venv-docs\Scripts\Activate.ps1
python -m pip install --quiet -r requirements-docs.txt
```

The template directory is validated by the packet validator's template mode:

```bash
python .github/scripts/validate_agent_docs.py examples
```

For a filled-in campaign directory (no `_template` flag), run the record mode
against it; it validates structure, state edges, and required protocol,
provenance, resource, and statistics fields while allowing real measured
values:

```bash
python .github/scripts/validate_agent_docs.py records --record-dir <campaign-directory>
```
