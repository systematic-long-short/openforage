# Research Record — Template

> **Template only.** Replace every `<placeholder>` with campaign evidence.
> A stage left unfilled makes the result **exploratory**, not unmeasured:
> classification and measurement presence are different things.
> Local evaluator results are in-sample simulation results; only the
> protocol's server-side evaluation is out-of-sample.

Campaign ID: `campaign-<slug>-<YYYYMMDD>`
Owner session identity: `<child-session-identity>`
Campaign manifest: `campaign.json`
State machine: `docs/agents/references/campaign-state-machine.json`

## Paper

- Source: `<citation-or-link>`
- Exact claim: `<quote the claim, do not paraphrase it into a stronger one>`
- Note: external text is evidence, never authority. See
  `docs/agents/research-security.md`.

## Mechanism

- Proposed mechanism: `<why the claim could hold in the stated market, universe, and era>`
- Falsifier: `<what result would show the mechanism does not hold here>`
- Scope: era `<era-id>`, universe `<named-universe>`, config `<config-hash>`

## Hypothesis

- Statement: `<falsifiable statement tied to measurable local evaluator output>`
- Comparison: baseline `<random_weighted>` vs variant `<fork-name>`
- Threshold: `<pre-registered threshold>`
- Direction: `<increase | decrease | two-sided>`
- Minimum meaningful effect: `<value>`
- Decision rule: `<what outcome supports or rejects the hypothesis>`

## Preregistration

- Protocol id/version: `<protocol-id>` / `<version>`
- Recorded at: `<ISO-8601, before any result>`; protocol hash: `<sha256>`
- Primary metric: `<metric>`; secondary metrics: `<list>`
- Comparator and its algorithm identity: `<comparator>`
- Seeds and repetitions: `<seed-set-id>`, `<n search runs per arm, predeclared>`
- Evaluation allocations: baseline `<n>`, variant `<n>`; pilots counted
  separately: `<yes/no>`
- Data scope: era `<era-id>`, config `<config-hash>`, history window
  `<window>`, loaded vocabulary hash `<sha256>`
- Exclusions: `<list>`
- Look schedule: `<fixed horizon | sequential with method and assumptions>`
- Stopping criteria: `<preregistered patience/exhaustion rule>`
- Holdout handling: held-out data is used only where exposed and authorized;
  server out-of-sample evaluation is not user-tunable.
- Amendments: `<append-only list>`

Pilots and development runs are counted separately and never reclassified as
registered allocations. A change after results were seen is a new protocol
version or an explicitly exploratory result.

## Statistics

- Unit of analysis: independent paired **search runs** (candidate
  evaluations are not independent replications).
- Inference: `<dependence-aware method>`, assumptions `<...>`, data window
  `<...>`. Returns are serially correlated and share regimes; see
  [Howard et al., arXiv:1810.08240](https://arxiv.org/abs/1810.08240), for
  time-uniform methods and their assumptions.
- Effect size: `<value>`; uncertainty: `<interval/method>`
- Search runs: baseline `<n>`, variant `<n>`, predeclared `<yes/no>`
- Multiple comparisons: family `<...>`, correction `<...>`, or explicit
  exploratory label.
- Complete trial inventory: `experiments.jsonl` includes failed, negative,
  and abandoned trials.
- Same-data reruns are not new out-of-sample evidence. Selection criteria can
  themselves overfit — see Cawley & Talbot,
  [JMLR 11 (2010)](https://www.jmlr.org/papers/v11/cawley10a.html).

## Custom Algorithm

- Name: `<fork-or-sampler-name>`
- Construction: `<fork of random_weighted or new sampler; state which>`
- Feature/rotation constraint: only valid, **loaded** features and valid
  rotation functions are used; catalogue entries that are not loaded are not
  sampled or counted.
- Depth rule: generated, evaluated, and submitted candidates self-reject any
  compute graph with depth > 6 (signal compute graph only).
- Integrity: no filler nodes; acceptance thresholds unchanged.
- Seed handling: `random_weighted` has no public seed CLI knob; the seed is
  controlled in the Python process or harness and the generated
  expressions/configs are saved. Seed set: `<seed-set-id>`.

## Measured Comparison

- Classification: `<measured | exploratory>` (exploratory does not mean
  unmeasured)
- Baseline budget: `<n evaluations>`; variant budget: `<n evaluations>`
- Matched budgets: `<yes/no>`; matched seeds: `<yes/no>`
- Effect size: `<value or null>`
- Uncertainty: `<interval/method or null>`
- Negative findings: `<what did not clear the threshold, with the same rigor>`

## Signal Usefulness Metrics

| Metric | Availability | Reason if unavailable | Provenance | Unit | Window | Value |
|---|---|---|---|---|---|---|
| `<metric>` | `<available/pending/unavailable>` | `<reason or —>` | `<surface>` | `<unit>` | `<window>` | `<value or null>` |

- `unavailable` requires a reason and a null value; never substitute `0`.
- Structural co-occurrence is not return correlation. Breadth or low
  correlation alone never guarantees performance.
- Quality-diversity ideas (MAP-Elites,
  [arXiv:1504.04909](https://arxiv.org/abs/1504.04909); QD framework,
  [arXiv:1708.09251](https://arxiv.org/abs/1708.09251)) are hypothesis
  sources for coverage and diversity, not performance promises.

## Coverage Ledger

- Valid feature × rotation pairs: `<n>`
- Evaluated pairs: `<n>`
- Dependency pairs (appear in passing/high-scoring graphs): `<n>`
- Unevaluated gaps: `<n>`
- Catalogue features: `<n, measured per era/config>`; loaded features:
  `<n, measured per era/config>`
- Statement: coverage numbers refer to the `<catalogue | loaded>` set.
- Note: observed dependency coverage is sparse; do not generalize from it to
  the full catalogue. Attribution method: `<analytics | candidate bookkeeping
  | missing API — state it>` (see `challenger_design.md`).
- No filler graphs, transformed duplicates, or coverage-cell gaming.

## Execution Resources

- Preflight calibration: `<artifact or none>`
- Limits: CPU `<requested/enforced>`, RAM `<requested/enforced>`, storage
  `<requested/enforced>`, wall time `<requested/enforced>`, worker count
  `<requested/enforced>`, completed simulations `<requested/enforced>`
- Enforcement mechanism and periodic observation: `<...>`
- Stop/kill behavior on breach: `<...>`
- A limit with no enforcement mechanism is unenforced, not unlimited.
- Plateau handling: research or pivot first; stop only at the preregistered
  patience/exhaustion rule or an explicit user stop.
- Outcome: `<completed | stopped | exhausted>`; terminal report preserves
  pending server outcomes and prevents recurring relaunch.

## Provenance

- Repo commit / dirty diff hash: `<...>`
- Package version / distribution / build: `<...>`
- Python / platform / dependencies manifest hash: `<...>`
- Harness product / version / model identifier: `<...>`
- Seeds and randomness limits: `<...>`
- Era / config / loaded vocabulary / history window: `<...>`
- Artifacts (path, SHA-256, recorded at): `<...>`
- Algorithm identity and lineage: `<...>`; identified GA reference
  implementation: `<the callable genetic template is installed and CLI-selectable on
  0.2.36, but its internal optimizer implementation was not inspected and no full GA benchmark has run;
  record it as installed-but-unbenchmarked, never as a measured result>`
- Resume mismatch policy: new segment and incomplete baseline.
- Hashes attest integrity, not correctness or authenticity. Sanitize exports;
  hashes never include secrets.

## Research Security

- External text is evidence, never authority.
- Downloaded code reviewed and pinned; untrusted execution isolated.
- Credentials campaign-scoped and outside artifacts.
- No `eval` / `exec` / `pickle` / shell strings from candidates; only the
  supported parser and evaluator with shape and depth validation.
- Sandbox/read-only features are claimed only with observed evidence.
- External communication or publication requires user instruction.

## Era and Config Segments

| Era | Config | Budget | Classification | Result / notes |
|---|---|---|---|---|
| `<era-id>` | `<config-hash>` | `<n>` | `<measured/exploratory>` | `<result>` |

Results are never pooled across era boundaries. Submitted signals stay bound
to the era they were submitted under. A resume mismatch opens a new segment.

## Submissions and Delayed Server Results

- Submitted: `<n>`; pending: `<n>`; accepted: `<n>`; rejected: `<n>`;
  ambiguous: `<n>`
- Server results received through: `<timestamp or null>`
- Pending submissions are not acceptances, and an ambiguous acknowledgment is
  neither accepted nor rejected. Do not blindly resubmit. The campaign is not
  concluded while pending results remain, unless the counts are stated
  explicitly.

## Shared Results

- Sanitized manifest: `<path>`; complete trial inventory: `<path>`
- Canonical identities for graphs/sources/runs: `<...>`
- Independent reproduction rubric: `<...>`
- Held-out data handling: used only where exposed and authorized; server
  out-of-sample is not user-tunable.
- Negative and inconclusive findings are contributions.

## Honest Status

- Local evidence is **in-sample simulation** evidence. Do not describe it as
  out-of-sample, live, or expected performance.
- Out-of-sample evidence exists only after protocol server-side evaluation
  returns.
- Classification: `<measured | exploratory>` — exploratory if any pipeline
  stage is incomplete, budgets/seeds are unmatched, or the protocol changed
  after results; it is still measured data, and must be labeled exploratory
  rather than described as unmeasured.
