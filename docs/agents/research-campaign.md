# Research Campaign

A research campaign is the durable, reproducible record of a background research agent's work: what was tried, at what budget, over which era, and what was measured. The campaign is owned by exactly one background research agent — a separate CLI invocation of the same harness with a distinct session identity. See [Background Agent](/docs/agents/background-agent.md) and [README §A.1](../../README.md).

Before choosing a direction, read the [research opportunity map](research-opportunities.json) (authoritative) or its [rendered guide](research-opportunities.md). An entry is a proposal with a primary source and an acceptance-evidence bar, not a result; pick only entries whose required public capabilities exist in your installed version. Missing metrics stay `unknown` until a campaign measures them.

The optional stdlib-only `python scripts/research_next.py` command returns one
deterministic next-action proposal. Its practical input/output and context
contract is in [`examples/agent-campaign/next-action/README.md`](../../examples/agent-campaign/next-action/README.md).
It is read-only, useful without a campaign or install, and is not a scheduler,
task database, or execution permission. Use the harness's documented native
goals/tasks/memory/scheduling for any handoff.

Local evaluator results are **in-sample**. Only the protocol's server-side evaluation is out-of-sample. Never blur the two in a campaign report.

## Campaign metadata is separate from settings

Campaign metadata belongs to the campaign and is stored with it — `.openforage-research/` by convention, with `campaign.json` as the manifest. It is **separate from `.openforage/settings.yaml`** and MUST NOT be written into the OpenForage data directory or its settings file. See the `search_settings` skill under `src/openforage/skills/search_settings/SKILL.md` for what settings cover.

## Campaign state machine

The authoritative machine-readable definition is
[`docs/agents/references/campaign-state-machine.json`](references/campaign-state-machine.json).
This section is the human-readable contract; the JSON is the authority for
state ids and edges, and campaign records reference it.

| State | Meaning | Legal next states |
|---|---|---|
| `bootstrapping` | Ownership claim and scope fixing; no baseline frozen yet. | `baseline`, `research`, `paused`, `needs-input`, `exhausted`, `stopped` |
| `baseline` | Establishing or referencing the compatible baseline; configuration frozen before the run, evidence frozen after. | `research`, `paused`, `needs-input`, `exhausted`, `stopped` |
| `research` | Mechanism, hypothesis, pilots, and custom-algorithm development, counted separately from registered allocations. | `experiment`, `comparison`, `paused`, `needs-input`, `exhausted`, `stopped` |
| `experiment` | Running the preregistered protocol under its declared horizon and repetitions. | `comparison`, `paused`, `needs-input`, `exhausted`, `stopped` |
| `comparison` | Analyzing the registered result, the complete trial inventory, and delayed server outcomes. | `research` (pivot), `completed`, `paused`, `needs-input`, `exhausted`, `stopped` |
| `paused` | Intentionally halted; workers stopped and recurring triggers disabled or drained; resume target preserved. | recorded `resume_target`, `stopped` |
| `needs-input` | Blocked on missing input or unavailable infrastructure after bounded retries. | recorded `resume_target`, `stopped` |
| `exhausted` | Hard resource breach or preregistered patience/exhaustion reached. Terminal. | — |
| `stopped` | Explicit user stop. Terminal. | — |
| `completed` | Protocol closed with a terminal report. Terminal. | — |

Rules:

- Every accepted transition is appended to an append-only `transitions` list
  with `transition_id`, `from`, `to`, `timestamp`, `reason`,
  `owner_session_identity`, `owner_revision`, and `guard_passed`.
- A failed guard leaves the previous state unchanged; it is logged outside
  the transition history, never recorded as a transition.
- `owner_revision` is non-decreasing and advances on every accepted
  transition. Timestamps are non-decreasing down the history.
- `paused` and `needs-input` carry a preserved `resume_target`. Each hold
  preserves its own target from the state it left (or an explicit
  `resume_target` transition field); the manifest target is only compared
  against the current hold, and it clears after a resume. Resume may only
  restore that target. A different target is an illegal transition.
- Transition ids are unique, `owner_revision` values strictly advance as
  integers (a bool is not an integer), the current revision and owner match
  the last transition, and filled records use valid, non-decreasing ISO-8601
  timestamps. An initial record may have empty history only at the initial
  state.
- Terminal states have no outgoing transitions. `stopped`, `completed`, and
  `exhausted` never silently reactivate; continuing requires a new campaign
  id with a new protocol version and a freshly recorded baseline.
- A hard resource breach or the preregistered exhaustion criterion reaches
  `exhausted` immediately from any active state, including bootstrapping.
  Research-or-pivot-first applies only to a statistical plateau while
  resources remain.
- Corrections are append-only amendments. Recorded history is never edited or
  deleted in place.

### Ownership, locking, and fencing

Exactly one live owner may own a campaign id or a `--data-dir`. The
campaign's tooling must take an atomic ownership claim — for example an
`O_EXCL` creation of an ownership record — or an equivalent verified lock,
and must advance a fencing token on every transfer. A writer or worker
holding a stale token must refuse to write.

The OpenForage library does not provide this claim, the lock, or the fencing
by itself. Never claim the library supplies exactly-once transitions,
exactly-once submissions, or exactly-once worker launches. Before launching,
read the child registry and `openforage status --data-dir <dir> --json`; if
either shows a live owner, do not launch. `openforage start` refuses a second
worker per data directory, and the registry extends that one-owner rule to
the agent layer.

When a submission acknowledgment is ambiguous, record it as ambiguous, do
not blindly resubmit, and resolve it through the submission/status surface
before any retry. Reconcile the registry, status, events, and checkpoints on
every resume so a dead process entry neither blocks a safe ownership transfer
nor licenses a duplicate launch.

Manifest updates are atomic (write-temp-then-rename, or an equivalent
verified atomic update) and the transition history is append-only, so a
crash cannot leave a partially written record. A crash is not a state
transition: on recovery, reconcile from status, events, and checkpoints, and
when the pre-crash state cannot be proven, mark the affected segment
incomplete instead of assuming a transition happened.

### Pause, resume, and fresh children

- **Pause** stops the worker, disables or drains every recurring trigger and
  scheduled prompt for the campaign, writes a checkpoint and a transition
  record, and reports `paused` with `resume_target` set.
- **Resume** may use the original child session identity or a fresh child,
  provided the resuming child is a separate CLI invocation of the same
  harness with its own session identity and is **not** the parent. Ownership
  must be transferred safely under the verified claim/lock with an advanced
  fencing token, and the registry, status, events, and checkpoints must be
  reconciled before the worker restarts. A dead child process alone is not a
  resume.
- **Stop** is explicit and terminal: stop workers, disable or drain
  triggers, flush the terminal report, and mark the campaign `stopped`.
- The parent MUST NOT treat a dead child process as a resume, and MUST NOT
  mutate the data directory while a live child owns it.

## Baseline: frozen reference and compatible baselines

Two different things are called the "baseline":

1. **The frozen reference** is the frozen record of the 100,000-completed
   simulation target — when one has actually been established.
   - A simulation counts only when the evaluator returned a final result
     (pass, fail, or score). A final evaluation that fails the quality
     threshold is still a completed evaluation; runtime errors are counted
     separately as `failed`.
   - Started, queued, checkpointed, and cache-hit simulations do not count as
     completed.
   - Model availability explicitly. `target_completed_simulations` is the
     `100000` budget; `observed_completed_simulations` is the measured count
     and may be null or smaller. A record where the reference has not been
     established carries `availability` (`unavailable`/`pending`/`unknown`),
     a `null` id/hash/observed count, and a stated reason. Never report a
     target as if it were an observed completion.
   - Do not regenerate, re-run, or silently replace a recorded reference.
     Record its identifier/hash when available. If the recorded identifier is
     unavailable, say so; do not invent numbers.
   - The algorithm that produced a reference may not be identifiable from
     the installed surface. Record its actual algorithm identity, or record
     it as `missing`/`unavailable` with a reason. The installed surface
     exposes a callable `genetic` choice and the CLI accepts
     `--algorithm genetic`, but no full GA benchmark has run in this packet:
     do not label `random_weighted` as GA, do not assume a measured GA
     reference exists, and do not invent any other CLI flag or template.
2. **A compatible campaign baseline** is the measured baseline for your
   selected era/config under matched budgets and seeds.

If no compatible frozen baseline exists for the selected era/config, create
a real baseline for the campaign:

- freeze the baseline configuration before the run and freeze the evidence
  after it;
- record the baseline's own id, lineage, and artifact hashes; never silently
  overwrite a recorded baseline;
- keep an incomplete baseline incomplete — never round it up to the
  `100000` target;
- give every additional paired baseline run a distinct id and lineage.

When a compatible frozen baseline with usable evidence already exists, a
terminal campaign's follow-on may reuse it with a new reference id and a
recorded lineage instead of rerunning the same measurement; record the reuse
explicitly and do not mandate a needless rerun. A fresh measurement is still
required when the era, config, vocabulary, or provenance does not match.

`random_weighted` is the verified installed comparator for this packet.
Record the identity of any reference-producing algorithm honestly, or record
the missing implementation instead of assuming it exists.

## Checkpoint and completion accounting

- Count only completed evaluator simulations toward the frozen budget.
- A checkpoint is resumable state, not progress. Resuming reconciles against
  the checkpoint and must not double-count completed simulations.
- Track `attempted`, `completed`, `in_flight`, and `failed` separately, and
  reconcile on every resume. Cache hits are not completed evaluator work;
  runtime errors are `failed`, not `completed`.
- Report completion as `completed / 100000`, never as checkpoints,
  submissions, or elapsed time. The default target is 100,000 completed
  evaluations; incomplete stays incomplete.

## Preregistration

A preregistered protocol is recorded and hashed **before any result exists**,
including pilots. Record at least:

- protocol id, version, recording timestamp, and hash of the protocol
  artifact;
- the hypothesis and the mechanism it tests;
- primary metric and secondary metrics;
- direction of the effect and the minimum meaningful effect;
- comparator, including the comparator's recorded algorithm identity;
- predeclared seeds and repetitions, with the seed-control mechanism;
- evaluation allocations for baseline and variant, counted separately from
  pilots and development runs;
- data scope: era, config, history window, and loaded vocabulary;
- exclusions;
- the look schedule (fixed horizon by default; a sequential method needs its
  method and assumptions recorded);
- decision criteria and stopping criteria, including the patience/exhaustion
  rule that later justifies `exhausted`;
- holdout handling: held-out data is used only where actually exposed and
  authorized; server-side out-of-sample evaluation is not user-tunable.

Pilots and development runs are counted separately and never silently
reclassified as registered experiment allocations. A change made after
results were seen is a new protocol version or an explicitly exploratory
result; the previous version is preserved. For the same reason, keep
selection criteria themselves honest: they can overfit the data they select
on — see Cawley & Talbot, ["On Over-fitting in Model Selection and
Subsequent Selection Bias in Performance Evaluation"](https://www.jmlr.org/papers/v11/cawley10a.html),
JMLR 11 (2010).

Classification and measurement presence are different things: a comparison
that fails preregistration or matching is classified **exploratory**, but the
numbers it contains are still real measurements. Never write "not measured"
about a result that has measurements; write "exploratory".

## Statistical units and inference

- The unit of analysis is an **independent paired search run**, not a
  candidate evaluation. 100,000 completed candidate evaluations are not
  100,000 independent replications: evaluations inside one run share search
  state, era data, configuration, and selection pressure.
- Use dependence-aware inference that accounts for serial correlation in
  returns and shared data windows, and state the method's assumptions and
  the data window it applies to.
- Report effect sizes with uncertainty. A point estimate without an
  uncertainty statement is insufficient to establish a confirmed
  comparison; it does not mean the underlying numbers were never measured.
- Predeclare the number of paired runs. Do not invent repetitions after
  seeing results.
- Record the complete trial inventory: every variant tried, including
  failed, negative, and abandoned pilot runs. Silent omission is a review
  blocker.
- Define the multiple-comparison family and the correction used, or label
  the whole exercise explicitly exploratory.
- Fixed-horizon is the default. Sequential or optional-stopping inference is
  allowed only with a justified method and stated assumptions. Time-uniform
  confidence sequences control error under their stated assumptions — see
  [Howard et al., arXiv:1810.08240](https://arxiv.org/abs/1810.08240) (2021
  journal reference) — but they do not by themselves validate arbitrary
  optional stopping on dependent financial observations.
- Rerunning an evaluation on the same data is not new out-of-sample
  evidence. Same-data reruns never upgrade a result to out-of-sample.
- Never promise statistical significance, and never report a financial
  metric the public evidence does not expose.

## Signal usefulness metrics

Report a usefulness metric only when the public/installed surface actually
exposes the data behind it. Otherwise record `unavailable` with a reason —
never `0`, and never a proxy presented as the metric.

| Metric | Availability rule | Provenance | Unit | Window |
|---|---|---|---|---|
| Local quality pass/fail and score | Always available from the evaluator result. | Local evaluator. | Boolean / score. | The era's in-sample evaluation window. |
| Known server acceptance | Only after the server result arrives; `pending` means unknown, not zero. | Protocol server, observed through submission/status surfaces. | Count. | Through `server_results_through`. |
| Breadth / concentration | Only when the analytics or candidate-bookkeeping surface exposes the grouping; otherwise `unavailable`. | Analytics surface or campaign bookkeeping, named in the record. | Count / share. | Stated evaluation window. |
| Behavioral redundancy | Only when comparable exposed series exist; otherwise `unavailable`. | The exposed series, named. | Correlation / overlap. | Stated window. |
| Turnover / stability / capacity | Only when public evidence exposes the necessary data; otherwise `unavailable` with a reason. Never fabricate or backfill `0`. | Public evidence only. | Metric-specific, stated. | Stated window. |

Additional rules:

- A missing API is a recorded gap, not a guarantee and not a proxy. State
  which surface was unavailable and what it blocks; do not replace it with a
  promise, an approximation presented as the metric, or an invented method.
- Structural co-occurrence is not return correlation.
  `get_correlation_patterns()` reports function pairs that co-occur in
  passing evaluations; that is a structural statistic, not evidence that
  their returns correlate.
- Breadth or low correlation alone never guarantees performance. Treat them
  as hypotheses to test, not as results.
- Keep the compute-graph depth cap of 6 and the era's protocol thresholds.
  Do not add filler nodes, transform duplicates, or weaken acceptance to
  improve a coverage number.
- Quality-diversity archives are a hypothesis source for coverage and
  diversity, not a performance promise: see MAP-Elites
  ([Mouret & Clune, arXiv:1504.04909](https://arxiv.org/abs/1504.04909)) and
  the quality-diversity framework
  ([Cully & Demiris, arXiv:1708.09251](https://arxiv.org/abs/1708.09251)).

## Research pipeline

Move through five stages and record one artifact per stage:

**Paper → Mechanism → Hypothesis → Custom algorithm → Measured comparison**

1. **Paper** — the source idea and its exact claim.
2. **Mechanism** — why the proposed mechanism could hold in the current market, universe, and era, and what would falsify it.
3. **Hypothesis** — a falsifiable statement tied to measurable local evaluator output, with a stated comparison and threshold.
4. **Custom algorithm** — the implementation (forked template or new sampler) constrained to valid, loaded features and rotation functions, rejecting any candidate with compute-graph depth greater than 6.
5. **Measured comparison** — the baseline-vs-variant result under matched budgets and seeds, including effect size, uncertainty, and negative findings.

A campaign that skips a stage is classified **exploratory**; it may still
contain real measurements, and it must be labeled as exploratory rather than
described as unmeasured.

## Matched budgets and seeds

- Baseline and variant MUST run under the same evaluation budget.
- Record the requested `budget_evaluations` separately from actual
  `completed_evaluator_simulations` for each arm. Started, queued,
  checkpointed, cache-hit, and failed work is not a completed evaluator result;
  a target or allocation never substitutes for an observed count.
- Use a recorded, matched seed set. `random_weighted` has no public seed CLI knob; control the random seed in the Python process or harness and save the generated expressions/configs you intend to compare. Setting a seed does not by itself prove every random-number generator in the process is controlled: record the seed-control mechanism and the known randomness limits, and treat uncontrolled randomness as a limitation.
- If budgets or seeds differ, label the comparison **exploratory**; do not present it as a measured registered comparison.
- Record the seed set, budget, config hash, and generated artifacts in the campaign manifest.

## Coverage

- **Broad valid feature × rotation coverage is the aspiration, not a global
  dense Cartesian allocation.** There is no requirement to enumerate or
  evaluate every cell: the eligible denominator (the number of valid, loaded
  feature × rotation pairs under depth-6 and node-budget rules) may be
  unknown, and it stays explicitly unknown rather than being guessed.
- **Actual dependency coverage is sparse.** Only some features and rotations
  appear as dependencies of passing or high-scoring graphs. Record what was
  exercised and do not generalize from the sparse observed set to the full
  catalogue, and do not present an unknown eligible denominator as known.
- Maintain a coverage ledger: valid pairs, evaluated pairs, dependency pairs,
  and unevaluated gaps.
- Coverage numbers never justify filler graphs, transformed duplicates, or
  coverage-cell gaming. Acceptance thresholds are not weakened to move a
  cell.

## Catalogue vs loaded features

- The **catalogue** is the advertised feature set. `catalogue_features` is measured per era/config from the installed library; there is no fixed approximate count to carry across eras.
- The **loaded** set is what the local evaluator actually has for the current era, universe, and config.
- Only loaded features can be evaluated. Never sample, count, or claim coverage for catalogue entries that are not loaded.
- Record `catalogue_features` and `loaded_features` counts per era/config, and state which set a coverage number refers to.

## Era and config segments

- Segment results by era and by configuration. Never pool results across era boundaries — thresholds, scoring, neutralization, caps, and windows can all change at a transition. See [Eras](/docs/agents/eras.md).
- Submitted signals stay bound to the era they were submitted under.
- Present per-era, per-config tables with the era/config identifier on every row.
- A resume that finds a configuration, era, or vocabulary mismatch opens a
  new segment and marks any affected baseline incomplete. It never splices
  mismatched evidence into one segment.

## Submissions and delayed server results

- A local pass is not a server acceptance.
- Submissions enter the protocol queue; server results are delayed. Track `submitted`, `pending`, `accepted`, `rejected`, `ambiguous`, and the cutoff through which server results have been received.
- Do not count pending submissions as accepted, do not resolve an ambiguous acknowledgment by resubmitting blindly, and do not conclude a campaign before the delayed results land — or state the pending/ambiguous counts explicitly in the report.
- Status fields such as `estimated_earnings`, `actual_earnings`, and submission `payment` values are server/API-reported off-chain metadata and local ledger observations, not client-signed settlement or guarantees. See [Submission and Status](/docs/agents/submission-status.md).

## Execution resources

Resource kinds in scope for a campaign are CPU, RAM, storage, wall time,
worker/concurrency count, and the completed-simulation budget. The record
carries, for each limit:

- `requested` and `enforced` values, which may differ;
- the actual enforcement mechanism;
- how the limit is observed periodically;
- the stop/kill/drain behavior on breach.

Rules:

- Run a preflight calibration before the campaign starts and attach the
  calibration artifact, so limits come from observation rather than
  assumption.
- Missing enforcement is visible in the record. A limit without an
  enforcement mechanism is not treated as enforced; record the mechanism and
  the observed evidence for any limit claimed as enforced.
- A hard resource breach or the preregistered exhaustion criterion stops
  immediately, from every active phase including bootstrapping. Plateau
  handling — research or pivot first — applies only to a statistical plateau
  while resources remain. Stop is never withheld because a pivot has not
  been attempted when a hard limit was breached.
- If required infrastructure is unavailable, enter `needs-input` with
  bounded retries; do not loop unattended.
- `completed`, `stopped`, and `exhausted` are distinct outcomes. The terminal
  report preserves pending server outcomes and prevents recurring relaunch:
  disable triggers, remove supervisor entries, and record
  `relaunch_prevented`. The outcome, terminal report, and relaunch flags must
  agree, and paused/needs-input/terminal states must not show a running
  worker or enabled recurring triggers in `lifecycle_observations`.

## Provenance and reproducibility

Every campaign and experiment record carries a provenance block:

- repository commit plus a hash of the dirty diff, if any;
- package name, version, distribution, and build identifier;
- environment: Python version, platform, and a dependencies-manifest hash;
- harness product and version, and the model identifier when one exists;
- seeds and the seed-control mechanism, plus known randomness limits:
  setting a seed in the harness or the Python process does not prove every
  random-number generator in the process is controlled;
- data/config scope: era, config hash, loaded-vocabulary hash, and history
  window;
- artifact references with SHA-256 hashes and recording times for sources,
  candidates, and campaign records;
- algorithm source identity and lineage, including whether an identified
  reference implementation exists or the implementation is unavailable, with
  the reason stated when unavailable.

Rules:

- Evidence references are immutable and amendments are append-only.
- Hashes attest integrity, not correctness or authenticity. A matching hash
  means the artifact is the one recorded, not that its claims are true.
- Sanitize exports and shared records. Hashes and provenance records never
  include secrets — see [Research Security](/docs/agents/research-security.md).
- A resume mismatch (commit, package, environment, harness, seed, config,
  data, era, or vocabulary) opens a new segment and marks the affected
  baseline incomplete.

## Shared results

- **Prepare** a sanitized immutable manifest plus the complete trial
  inventory; publish it only when the user instructs it. This packet
  introduces no publication channel.
- Use duplicate canonical identities for graphs, sources, and runs so the
  same artifact is never counted twice.
- Include an independent reproduction rubric: environment, commands,
  seeds, artifacts, and the expected evidence for each step.
- Held-out data is used only where actually exposed and authorized; the
  server-side out-of-sample evaluation is not user-tunable.
- No cherry-picking, transformed duplicates, filler graphs, or
  coverage-cell gaming.
- Negative and inconclusive findings are contributions and belong in the
  record with the same rigor as positive ones.
- This packet introduces no leaderboard or hosted service. Sharing is a
  sanitized record, not a submission to a new service.

## Negative findings and honest OOS language

- Record negative findings with the same rigor as positive ones: what was tried, the measured comparison, and why it did not clear the hypothesis threshold.
- Local evaluator outcomes are **in-sample simulation results**. Never describe them as out-of-sample performance, live performance, or expected earnings.
- Out-of-sample evidence exists only after the protocol's server-side evaluation returns. State that boundary in every report.
- Quantify uncertainty and cite the coverage ledger. Do not claim generalization beyond what was evaluated.
- Never generate, evaluate, or submit candidates with compute-graph depth greater than 6, regardless of the era's `max_depth` — see [Simulation Settings — Graph complexity limits](/docs/agents/simulation-settings.md#graph-complexity-limits).

## Campaign manifest fields

`campaign.json` should carry at least:

- `campaign_id`, `owner_session_identity`, and the objective;
- `state_machine` (definition reference, current state, revision, resume
  target, ownership claim and fencing fields) and the append-only
  `transitions` history;
- `frozen_baseline` (`availability`, `unavailable_reason`, id/hash,
  `target_completed_simulations: 100000`, `observed_completed_simulations`,
  `frozen`) and `compatible_baseline` (status, status reason, id, lineage,
  freeze flags, distinct-id rule, no silent overwrite);
- `preregistration` (protocol id/hash/timestamp, hypothesis, metrics,
  direction and minimum meaningful effect, comparator, seeds/repetitions,
  allocations, data scope, exclusions, look schedule, decision/stopping
  criteria, holdout handling, amendments). Early bootstrapping/research
  records and exploratory comparisons may leave these as not-yet-recorded,
  but must state `unavailable_reasons`; a registered or completed claim
  requires real values instead;
- `statistics` (unit of analysis, classification, trial inventory reference,
  and `complete_trial_inventory`); a completed registered claim requires the
  complete trial inventory with real resolved rows, while startup and
  exploratory work may state it as incomplete;
- `pipeline_stages` with one artifact per stage;
- `budgets`, `seed_sets`, and `config_hashes`;
- `coverage_ledger` (`catalogue_features`, `loaded_features`, `valid_pairs`, `evaluated_pairs`, `dependency_pairs`);
- `usefulness_metrics` with availability, provenance, unit, and window (or
  `unavailable` with a reason);
- `era_segments` and `config_segments`;
- `submissions` counters, `ambiguous`, and `server_results_through`;
- `checkpoints` and the completion reconciliation;
- `lifecycle_observations` (worker running, triggers enabled, observed at,
  source) so a paused/needs-input/terminal record cannot contradict live
  work;
- `resources` (preflight calibration, requested vs enforced limits with
  mechanisms and observed evidence, outcome, terminal report);
- `provenance`;
- `shared_results`;
- `negative_findings`.

## Related pages

- [Background Agent](/docs/agents/background-agent.md) — the agent that owns a campaign.
- [Research Security](/docs/agents/research-security.md) — trust boundaries, credentials, and publication rules.
- [Harness Verification](/docs/agents/harness-verification.md) — verified adapter registry for spawning and controlling that agent.
- [Diagnostics](/docs/agents/diagnostics.md) — classifying stalled or ambiguous campaigns.
- [Campaign state machine JSON](references/campaign-state-machine.json) — authoritative states and edges.
