# Deterministic next-action helper

`python scripts/research_next.py` is an optional, stdlib-only, read-only
selector. With no inputs—or only the record templates—it immediately proposes
one offline paper/mechanism/hypothesis design task. It needs no invite,
installed package, daemon, worker, live service, or native-tool API. It never
changes an input, launches work, or creates a task/schedule.

```bash
python scripts/research_next.py
python scripts/research_next.py --format json
python scripts/research_next.py --campaign .openforage-research/campaign.json \
  --coverage .openforage-research/coverage.jsonl \
  --experiments .openforage-research/experiments.jsonl \
  --context .openforage-research/next-action-context.json
python scripts/research_next.py --selfcheck
```

`--experiments` (also `--experiment`) accepts one `experiment_record.json`, a
JSON array of records, or a `.jsonl` trial inventory. Every supplied file is
read only. The output uses stable action ids and includes one action, its
reason, canonical source refs, SHA-256 digests for the map/state graph/template
and supplied evidence, explicit unknowns, inputs, a done-when artifact, a
relevant check, and native-handoff text. External input paths are not echoed.
JSON count values use `null` with status `unknown` when evidence is absent,
malformed, boolean, incomplete, or incompatible; `100000` is separately
labeled a target, never an observation.

## Context contract

The optional context is a small helper-specific observation record; campaign
fields remain authoritative where available. [`context-template.json`](context-template.json) is an
ignored synthetic template, not evidence or authorization. For a real context,
set `schema_version` to integer `1` and remove `_template`. All progress ids
must exist in the canonical opportunity map.

```json
{
  "schema_version": 1,
  "authorization": {
    "explicit_live_authorization": false,
    "evidence_ref": null
  },
  "observations": {
    "live_access_available": false,
    "package_installed": false,
    "genetic_callable_observed": false,
    "separate_same_harness_cli_observed": false,
    "distinct_session_identity_observed": false,
    "source_ref": null,
    "observed_at": null
  },
  "scope_observation": null,
  "research_progress": {
    "investigated_opportunity_ids": [],
    "negative_approach_ids": [],
    "analyzed_experiment_ids": [],
    "active_opportunity_id": null,
    "pending_ablation": null
  }
}
```

For a live-baseline proposal, explicit authorization and every live observation
must be exact JSON `true`, carry non-secret evidence references and timestamps,
and match the manifest's real era, config, and loaded-vocabulary scope; the
manifest must also contain a verified ownership claim and fencing evidence.
When observed, `scope_observation` is an object with `era_id`, `config_hash`,
`loaded_vocabulary_hash`, `source_ref`, and a timezone-qualified ISO-8601
`observed_at`. `observations` uses the boolean keys shown above plus its own
`source_ref` and `observed_at`.
This record is operator-supplied evidence, not a permission grant. Keep it with
the campaign, separate from `.openforage/settings.yaml`. Never put invite codes,
credentials, private host paths, or tokens in it. If any gate is
absent, false, template, or mismatched, the helper continues with offline research.

`research_progress.pending_ablation`, when present, has `opportunity_id`,
`basis_ref`, and `component` strings. It requests ablation design only; it does
not assert that the basis result is real or positive. Use
`active_opportunity_id` to focus a fully preregistered coverage task on
`coverage-stratified-sampling`; update investigated/negative/analyzed ids when
new evidence is actually recorded so repeated calls move to the next direction.

## Selection and evidence rules

1. `paused` returns reconciliation; `needs-input` returns a blocker/user-input
   task. `stopped`, `completed`, and `exhausted` return terminal-report/no-
   relaunch work. These states take precedence over all experiment evidence.
 2. A non-template manifest must have a real id, canonical state, typed baseline
    records, required campaign objects, and a latest transition matching its
    canonical edge/owner revision (empty history only at bootstrapping).
    Malformed real manifests fail clearly; no missing state defaults to active.
3. A GA baseline is ready only with exact `genetic` identity, frozen reference
   id/hash, at least 100000 observed completed evaluator simulations, and a
    frozen compatible baseline with an explicit id/lineage link for the manifest's real era/config. A missing or
    partial baseline proposes the real 100000-completion preparation only in
    bootstrapping/baseline state and only after every explicit live/scope gate;
    otherwise paper/mechanism work remains available offline. Reuse an existing
    reference only when exact scope compatibility is evidenced; otherwise use a
    distinct id/lineage and never overwrite or silently regenerate a reference.
4. Paper sources are candidates, never superiority claims. Non-template
   experiment evidence and context progress are used to avoid repeating
   investigated/negative approaches. After a baseline, incomplete
   paper/mechanism/hypothesis/algorithm stages precede preregistration.
   Recorded ablation requests precede another challenger; a coverage cell is
   selected only for an active, preregistered coverage direction.
 5. Comparison analysis requires real independent paired search runs with
    matched budgets and seeds, exact equal `completed_evaluator_simulations`
    counts per arm (separate from requested `budget_evaluations`), effect and
    uncertainty evidence, and one era/config segment. Candidate evaluations
    are not replications. Unknown measurements never become zero, and an action
    to analyze is not a conclusion.
6. A coverage row is selectable only when it matches manifest era/config and
   records `feature_set=loaded`, `valid_pair=true`, explicit
   `analytics`/`candidate_bookkeeping` attribution plus provenance, no filler
   or transformed duplicate, depth cap at most 6, and an exact non-negative
   integer evaluation count. Identical rows deduplicate; conflicting pair keys
   are excluded and reported. Mixed eras/configs never pool. Unknown counts do
   not rank as zero; observed rows do not establish the eligible denominator.
7. Construction descriptors stay descriptors. Structural co-occurrence is
   not return correlation. Unknown out-of-sample usefulness remains unknown.
   No filler, transformed duplicates, weakened acceptance gates, guessed APIs,
   or new rotation availability are recommended.

## Selfcheck scenarios

`python scripts/research_next.py --selfcheck` creates temporary synthetic
fixtures (then removes them) covering no context and templates, missing/partial baseline gates,
paper progress and negative findings, challenger/preregistration/ablation and
coverage selection, independent-run versus unknown measurements, era/config
isolation, matching and mixed-era coverage, invalid/unloaded/duplicate/conflicting rows, paused and
terminal states, and malformed manifests. Synthetic fixtures are regression
inputs only; they are not campaign evidence or benchmark results.

For any action handoff, the harness owns native goals, tasks, memory,
scheduling, and execution. The helper output is neither a scheduler nor a task
database.
