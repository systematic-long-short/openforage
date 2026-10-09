---
name: search-settings
description: Use this whenever an agent needs to read, change, or scope OpenForage search and worker settings. Trigger for search settings, .openforage/settings.yaml, algorithm selection, n_jobs, data directory scoping, evaluation budgets, seeds, settings drift across runtimes, or deciding where campaign metadata lives.
---

# Search Settings

> **Campaign path.** If your task is a campaign bootstrap and you have not
> read the OpenForage Library `README.md` §0, §A, and §A.1, read them first;
> documentation, template, and validation contributions do not need those
> sections.
> This skill is depth reference for settings changes around `T-OF-start-search-bg`
> and `T-OF-improvement-loop`, and for the README §A.1 background research agent
> contract.

Settings describe how OpenForage runs for one runtime/venv/data directory.
Research records are a different thing: campaign metadata belongs to the
campaign, not to OpenForage configuration.

## Native Controls First

Use the owning runtime's native goals, task stores, schedules, memory,
delegation, and background controls to decide, record, and apply a settings
change. Plan records such as Codex `update_plan` are plan records only and
**never schedulers** — they do not fire ticks or apply changes.

The background research agent owns the settings for its run, per the README
§A.1 contract. The parent must not edit settings, restart the worker, or
change the algorithm while a live child owns the `--data-dir`.

## Where Settings Live

- OpenForage settings belong to the `--data-dir` the CLI or Python session
  was opened with; the default is `~/.openforage`. The CLI examples in this
  README use `.openforage-state`.
- `.openforage/settings.yaml` is the library-owned settings file for that
  data directory. Treat it as OpenForage configuration. Read it when you
  need to know what the installed version persisted; do not repurpose it.
- Data directories are coupled to platform and library version (manifest
  checks, backtester binaries, locks, `min_library_version`). Do not share
  one `--data-dir` or settings file across different venvs, OpenForage
  versions, platforms, or agent runtimes — see README §E.2. Use one
  data directory per runtime/venv and pass it explicitly.

Known, documented settings surfaces in this repository:

- Algorithm selection: the observed `0.2.36` CLI choices are
  `random_weighted` and `genetic`; `genetic` is the documented default, while
  `start --help` prints no explicit CLI default. The CLI rejects unsupported
  strings before the worker starts. See the canonical
  [compatibility record](/docs/agents/references/compatibility.json).
- Worker parallelism: the Python `n_jobs` parameter on
  `openforage.search(...)`. Keep `n_jobs=1` during closed beta per the
  README load policy.
- State location: `--data-dir` on the CLI and `data_dir=` on Python calls,
  or `openforage.configure(data_dir=...)` for a session.
- Era-pinned simulation settings are not agent settings: they change on era
  transitions and the library resyncs automatically. See
  [Simulation Settings](/docs/agents/simulation-settings.md) and
  [Eras](/docs/agents/eras.md).

Do not guess additional YAML keys, CLI flags, or environment variables. If
the installed version documents a settings surface this skill does not
list, use the documented surface and record it in the campaign manifest;
otherwise treat the setting as unresolved and leave it alone.

## Campaign Baseline And Custom Algorithm Extension

Research campaigns change algorithms as a **Python-side research
procedure**, not as a new CLI setting:

- **Frozen reference.** Baseline-vs-variant comparisons cite the frozen
  record of an explicitly modeled 100,000-completed-simulation target.
  Record the reference's availability, identifier/hash when available, the
  `100000` target versus the observed completed count, and its actual
  algorithm identity with an unavailable reason where applicable; never
  regenerate or silently replace a recorded reference. The installed surface
  exposes a callable `genetic` choice and the CLI accepts
  `--algorithm genetic`, but no full GA benchmark has run in this packet;
  `random_weighted` is the observed comparator surface, no full benchmark of
  any algorithm has run, and no other flag may be assumed.
- **Compatible baseline creation.** If no compatible baseline exists for the
  campaign's era/config, create a real baseline as a campaign run: freeze the
  configuration before the run, freeze the evidence after it, give it its own
  id and lineage, and never silently overwrite a recorded baseline. An
  incomplete baseline stays incomplete; a terminal campaign's follow-on may
  reuse compatible frozen evidence with a new reference id and lineage.
- **Custom algorithm extension.** The observed CLI algorithm surface is
  `openforage start --algorithm {random_weighted,genetic}` on `0.2.36`
  (README §H). The genetic public callable and CLI choice are observed, but
  its internal optimizer implementation was not inspected and no full GA
  benchmark has run in this packet, so do not claim
  a measured GA result and do not invent any other `--algorithm` value: the
  CLI rejects unsupported strings before the worker starts.
  Extending the algorithm means forking the template through the Python API
  following the README §G procedure, rejecting every candidate with
  compute-graph depth greater than 6, and comparing the fork against
  `random_weighted` under matched budgets and seeds. The fork is a campaign
  artifact, not an `--algorithm` value.
- **Campaign metadata stays with the campaign.** Baseline references,
  preregistration, budget matches, seed sets, config hashes, execution
  resources, provenance, shared results, and comparison outcomes live in the
  campaign manifest (`.openforage-research/` by convention), never in
  `.openforage/settings.yaml` or the OpenForage data directory. Campaign
  state follows the authoritative
  [state machine](/docs/agents/references/campaign-state-machine.json).

## Change Procedure

1. Stop the owning worker and confirm quiescence (README §E.3, §J).
2. Change one knob at a time. Record the before value, the after value, the
   command or API call used, and why.
3. Restart with the same `--data-dir`, then compare on the analytics
   surface (README §L) against the previously recorded run.
4. Persist the comparison and any negative finding in the campaign
   manifest, not in `.openforage/settings.yaml`.
5. Never generate, evaluate, or submit candidates above compute-graph
   depth 6, regardless of the era's `max_depth` (README §G Hard Rules).

## Campaign Metadata Is Separate

Campaign metadata — campaign ID, child registry, capability report,
state machine and transition history, frozen reference and compatible
baseline, preregistration protocol, checkpoint ledger, coverage ledger,
usefulness metrics, era/config segments, budgets, seed sets, submissions,
server results, execution resources, provenance, shared results, negative
findings — belongs to the campaign and is stored with it
(`.openforage-research/` by convention).

It is **separate from `.openforage/settings.yaml`** and MUST NOT be written
into the OpenForage data directory or its settings file. Mixing the two
makes a campaign non-reproducible when the data directory is rebuilt for a
new era, platform, or library version.

See `search_research` for the campaign record fields and accounting rules.

## Reproducibility Notes

- `random_weighted` has no public seed CLI knob. It uses Python's module
  `random` internally. For reproducible comparisons, control the random
  seed in the Python process or test harness, and save the generated
  expressions/configs you want to compare (README §G).
- Matched budgets and seeds are required for any baseline-vs-variant
  comparison — see `search_research`.

## Adjacent Skills

- `openforage_quickstart` — read before `T-OF-register` (install, wallet, durable state directory).
- `background_search_loops` — read before `T-OF-start-search-bg` (worker lifecycle and ownership).
- `agent_runtime_integration` — read before `T-OF-improvement-loop` (native controls, background research agent contract).
- `scheduled_improvement_loops` — read before `T-OF-schedule` (native schedules, never plan records).
- `search_research` — read before a research campaign (frozen reference, checkpoint accounting, honest OOS language).
