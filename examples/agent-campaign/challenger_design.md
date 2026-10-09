# Coverage-Oriented Challenger Design

> Design record for a campaign. This is not a measured result and not a
> supported API contract. Replace `<placeholder>` values with campaign
> evidence, and re-verify every call against your own installed version.
>
> Executable companion: [`coverage_challenger.py`](coverage_challenger.py)
> has an offline smoke mode (explicitly synthetic fixture data) and an
> explicit live mode. Its results are exploratory until the campaign protocol
> in [`docs/agents/research-campaign.md`](../../docs/agents/research-campaign.md)
> is followed.

Goal: widen valid **feature × rotation** coverage while keeping the hard
depth-6 cap, and record sparse **dependency** coverage honestly instead of
inferring it from the full catalogue.

## Verified installed surface

The checks below were run against the installed public wheel
`openforage==0.2.36` on Python 3.12 (Linux x86_64) by importing the package
and observing callables. A different wheel version may differ; re-run the
checks before relying on any of this.

| Call | Observed behavior | Status |
|---|---|---|
| `openforage.templates.random_weighted` | Importable generator proxy; docstring `random_weighted(ctx)`; signature is `(*args, **kwargs)` (doc-only contract). | Observed comparator surface; no full benchmark measured. |
| `openforage.templates.genetic` | Importable callable with an empty docstring and `(*args, **kwargs)`; accepted by `openforage start --algorithm genetic` (actual `--help` probe). | Public callable/CLI observed; internal optimizer implementation not inspected; installed-but-unbenchmarked. |
| `openforage.search` | Introspected signature `(*args, named_universe=None, signal_pool_name, **kwargs)`; docstring `search(template=None, *, named_universe=None, signal_pool_name=None, n_jobs=None, settings_path=None)`. | README-documented; `signal_pool_name` is required by the installed signature. |
| `openforage.catalog.function_loader.load_function_catalog()` | Returns a dict; on 0.2.36: 442 entries — 437 `function_type == "rotation"`, 5 `reduction`; 34 rotation subfamilies. No auth required. | Importable, not README-documented. |
| `openforage.catalog.features.feature_names_from_runtime_vocabulary(vocabulary)` | Signature returns `tuple[str, ...]` of feature names for a runtime vocabulary. | Importable, needs a live context. |
| `SearchContext.vocabulary` | Attribute present on the context passed to templates. | Importable, needs a live context. |
| `openforage.search_templates.measure_graph_complexity(graph, *, max_depth=None, max_node_count=None)` | Returns `GraphComplexity`; raises `GraphComplexityLimitExceeded` when a supplied limit is proven exceeded. | Importable, not README-documented. |
| `openforage.search_analytics.AggregateBy.FUNCTION_SUBFAMILY_FEATURE_SUBFAMILY` | Exists; `get_search_stats(aggregate_by=...)` accepts it. | Importable, not README-documented. |
| `openforage.get_search_stats / get_search_patterns / get_search_velocity / get_yield_analysis / get_correlation_patterns` | Present with the README §L return shapes. | README-documented. |
| `openforage.search_templates.compose_next_batch` | Contract partially observed offline: a factory returning one mapping yields batch copies with `lineage='fresh_random'`; a generator-returning factory yields an empty batch; `prior` entries must support `.get`; non-graph payloads are dropped when `max_depth` is supplied. | Advanced; full call contract unproved, so it is **not used** in any runnable path. |

## Missing API (do not invent methods)

- **Per-era loaded rotation set.** No documented API enumerates which
  rotation functions are loaded/allowed for a specific era and config.
  `load_function_catalog()` is the installed catalog, not an era-scoped
  allow-list.
- **Offline catalogue feature enumeration.**
  `openforage.catalog.features.feature_catalog_records()` with no arguments
  raises `TypeError: runtime-derived feature names are required` on 0.2.36, so
  the catalogue cannot be enumerated without a runtime vocabulary from a live
  context.
- **Candidate leaf introspection.** `expr.depth()` is documented, but no
  public API enumerates the feature leaves and rotation functions of a
  yielded graph. Do not read private attributes or assume a graph shape; use
  analytics or your own bookkeeping when your fork constructs candidates.
- **Seed control.** `random_weighted` has no public seed CLI knob; control
  the seed in the Python process or harness and save generated
  expressions/configs. The live policy owns and records its own RNG state,
  but no full deterministic evaluator resume is claimed.
- **Genetic implementation not inspected.** `genetic` is a public callable
  and CLI-selectable; its internal optimizer implementation was not inspected
  and no full GA benchmark has run. Call it only as a generator factory
  (`next(stream)`) and record it as installed-but-unbenchmarked.
- **Offline graph construction.** `generation_core.generate_random_signal_expression`
  is present with a documented signature, but the public
  `load_function_catalog()` output on 0.2.36 lacks the internal
  `data_params` key the builder reads, so explicit graph construction cannot
  be exercised from the public loader output. Record the limitation; do not
  fabricate a builder call.
- **Direct module execution.** `python -m openforage` fails on 0.2.36 (no
  `openforage/__main__.py`); use the console script or
  `python -m openforage.cli`.
- **Pending server outcomes.** There is no documented local API for
  per-submission server outcomes; results are delayed and observed through
  the submission/status surfaces. Record `pending` separately and never count
  it as accepted.

If your installed version exposes one of these, record the observed
signature and evidence in the campaign manifest before using it. If it does
not, record the gap.

## Design

1. **Baseline.** Use a compatible frozen baseline for the selected era/config,
   or create a real one: freeze the configuration before the run, freeze the
   evidence after it, and record a distinct id and lineage. Incomplete stays
   incomplete. The live runner defaults to the installed `genetic` template
   with `random_weighted` as the explicit alternative; no full GA benchmark
   has run, so any genetic comparison stays exploratory.
2. **Valid rotation side.** Enumerate `function_type == "rotation"` entries
   and their subfamilies from the installed catalog (verified above).
3. **Valid feature side.** Read the loaded feature names from the live
   context with `feature_names_from_runtime_vocabulary(ctx.vocabulary)`.
   Record `catalogue_features` and `loaded_features` separately; only loaded
   features may be sampled, evaluated, or counted.
4. **Challenger generator.** The live callback implements generator-source
   stratification over the documented generator callables (`random_weighted`,
   `genetic`) with observable descriptors (measured depth) and one shared
   accepted-proposal accounting method for both smoke and live: graph-key
   history with a set index, per-target-cell counts, descriptor counts,
   exhausted-cell scheduling state, and bounded rejection caps. Canonical graph
   identity uses a known mapping shape or a public canonical serializer; any
   other payload is an explicit unsupported-identity rejection, never a repr
   address. It wraps each factory as a generator, takes one graph per
   `next(stream)` (never mistaking the generator for its yielded graph),
   self-rejects depth over `min(6, ctx.depth)` using
   `measure_graph_complexity`, and stops loudly after bounded rejections
   instead of spinning forever. This is generator-source stratification, not
   measured feature x rotation coverage.
   `coverage_challenger.py` and `stratified_policy.py` are the runnable
   implementation; the offline smoke mode runs the same policy/accounting
   core on synthetic fixture data, and `--stub-check` exercises the live
   callback with controlled stub streams (labeled a stub-stream integration
   test, never a library evaluation).
5. **Coverage attribution.** Because per-candidate leaf introspection is a
   missing API, mark exact feature attribution `unavailable` with a reason
   rather than recording the whole catalogue or fake cells; attribute
   coverage later from `get_search_stats` grouped by function subfamily ×
   feature subfamily and by feature, and mark any unverifiable pair
   `missing_api_gap` in `coverage.jsonl`.
6. **Matched comparison.** Baseline and variant run under the same budget and
   seed set with isolated `signal_pool_name` histories; counts are
   snapshotted only after `stop()`. Otherwise label the comparison
   **exploratory**. Segment results by era and config and never pool across
   era boundaries.
7. **Delayed outcomes.** Track `submitted` / `pending` / `accepted` /
   `rejected` and `server_results_through`. Pending is not accepted.

### Pseudocode

> **Not runnable.** Design sketch only; the runnable implementation is
> `coverage_challenger.py` + `stratified_policy.py`. The block below mirrors
> its structure and is not a supported API contract.

```python
# Design sketch — the runnable implementation is stratified_policy.py.
# [verified 0.2.36]: imported and observed on the installed wheel.
# [missing API]: not available; do not invent a replacement.

import random
from openforage.search_templates import measure_graph_complexity  # [verified 0.2.36]

MAX_DEPTH = 6
LIVE_CELLS = ("random_weighted", "genetic")   # documented/CLI choices; genetic is opaque

def live_challenger(ctx):
    templates = openforage.templates
    cap = min(MAX_DEPTH, ctx.depth) if isinstance(getattr(ctx, "depth", None), int) else MAX_DEPTH
    streams = {name: getattr(templates, name)(ctx) for name in LIVE_CELLS}  # factories, not graphs
    policy = StratifiedPolicy(list(LIVE_CELLS), draw=lambda cell, rng: next(streams[cell]), ...)
    while True:
        proposal = policy.propose(ledger)          # least-covered source; bounded attempts
        if proposal is None:
            return                                 # stop; never spin forever
        try:
            measure_graph_complexity(proposal.payload, max_depth=cap)  # [verified 0.2.36]
        except GraphComplexityLimitExceeded:       # only this counts as a depth rejection
            ledger.depth_rejected += 1
            continue
        # [missing API] exact per-candidate feature/rotation leaf attribution.
        # Record `feature_attribution: unavailable` with a reason instead of
        # the full catalogue or fabricated cells.
        yield proposal.payload
```

### Live invocation (single arm per process)

Pool names are server/era-defined identities: the runner never rewrites them.
Run each arm as its own process with the exact caller-supplied pool and
settings, then ingest the two captures.

```bash
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

### Coverage measurement

```python
import openforage
from openforage.search_analytics import AggregateBy  # [verified 0.2.36, not README-documented]

stats = openforage.get_search_stats(
    market_id=None,                # fill from the campaign's era/config
    era_number=None,
    named_universe=None,
    aggregate_by=AggregateBy.FUNCTION_SUBFAMILY_FEATURE_SUBFAMILY,
)
# For each row, write evaluated/dependency counts to coverage.jsonl, then
# compare against valid_rotations x loaded feature prefixes for the gaps.
```

## What would make this measured

- A preregistration protocol recorded and hashed before results, with
  metrics, direction and minimum meaningful effect, comparator, seeds and
  repetitions, allocations, exclusions, look schedule, and stopping criteria.
- All five pipeline stages recorded (paper → mechanism → hypothesis →
  custom algorithm → measured comparison).
- Matched budgets and seeds for baseline and variant, with the unit of
  analysis being independent paired search runs.
- Per-era, per-config result rows with the coverage ledger attached, and no
  filler graphs or coverage-cell gaming.
- Effect size and uncertainty, a declared multiple-comparison family or an
  exploratory label, and the complete trial inventory.
- Provenance, execution resources, and usefulness metrics recorded with
  availability and provenance.
- Negative findings recorded with the same rigor as positive ones, and a
  classification of `measured` only when every condition above holds.

## Verification appendix

Commands run while preparing this template (documentation only; do not paste
private paths):

```text
python -m venv <venv>
<venv>/bin/pip install openforage==0.2.36
<venv>/bin/python -c "import openforage; print(openforage.__version__)"
# observed: 0.2.36
<venv>/bin/python -m openforage.cli --help
# observed commands: init, register, start, status, earnings, stop, _worker, callbacks, serve, local
<venv>/bin/python -m openforage.cli start --help
# observed: --algorithm {random_weighted,genetic}
<venv>/bin/python -m openforage --help
# observed: failure, no openforage/__main__.py on 0.2.36
<venv>/bin/python -c "import inspect, openforage; print(inspect.signature(openforage.search))"
# observed: (*args, named_universe=None, signal_pool_name, **kwargs)
<venv>/bin/python -c "from openforage.catalog.function_loader import load_function_catalog as f; c=f(); print(len(c))"
# observed: 442 entries; 437 rotation, 5 reduction; 34 rotation subfamilies
<venv>/bin/python -c "from openforage.catalog.features import feature_catalog_records as f; f()"
# observed: TypeError: runtime-derived feature names are required
```
