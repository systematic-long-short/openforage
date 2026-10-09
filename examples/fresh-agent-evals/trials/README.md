# Retained trials

Sanitized trial records and verdicts from real fresh separate harness runs.
These are **single-trial observations, not a model benchmark, not a blind
test, and not an OpenForage library evaluation**: no trial registered,
searched, downloaded data, evaluated, simulated, or submitted anything. All
earlier observations (the rows through `challenger-research`) used OpenCode
`1.18.31` on Linux with model `openrouter/deepseek/deepseek-v4.1-flash` and
variant `max`. The three portable-onboarding observations below separately
record OpenCode `1.18.31`, model `openrouter/openai/gpt-6-luna`, and variant
`max`. Raw event streams are **not** published; they stay in git-ignored
scratch and the records keep only normalized observations plus raw
sha256/byte counts.

Record format and required fields: [`trial-record.template.json`](../trial-record.template.json).
The run protocol is in [`../README.md`](../README.md).

## Compact result table

| Case | Session (short) | Wall time | First recorded tool action | Result | Evidence |
|---|---|---|---|---|---|
| `docs-fix` (initial) | `ses_f35cbc52fffejERhlvEF47gADs` | 41s (17:40:14–17:40:55Z) | 3.372s | **false negative**: all 10 artifact checks passed; isolation failed on cached validator bytecode + parent Git context; kept as a separate observation | [trial](docs-fix-initial.trial.json) · [verdict](docs-fix-initial.trial.verdict.json) · [notes](docs-fix-initial-notes.md) |
| `docs-fix` (rerun) | `ses_f35c4d44fffe2EyS64ZobFsMNM` | 30s (17:47:49–17:48:19Z) | 2.936s | **pass** after the bytecode/Git isolation repair | [trial](docs-fix-rerun.trial.json) · [verdict](docs-fix-rerun.trial.verdict.json) · [artifacts](docs-fix-rerun/) |
| `campaign-planning-separation` | `ses_f35c45f01ffeikOaBBxN8flGZ9` | 23s (17:48:19–17:48:42Z) | 3.345s | **fail (genuine)**: required deliverables missing; the agent looked for hidden graders via source-checkout paths exposed in prepare/owner metadata and OpenCode auto-denied the external read | [trial](campaign-planning-separation.trial.json) · [verdict](campaign-planning-separation.trial.verdict.json) · [claim state](campaign-planning-separation/.openforage-research/ownership.json) |
| `campaign-planning-separation` (rerun) | `ses_f35b38d60ffebwNvafdwF1FP0M` | 58s (18:06:41–18:07:39Z) | 3.615s | **pass** under `workspace-protocol-v1`; original failure retained above | [trial](campaign-planning-separation-rerun.trial.json) · [verdict](campaign-planning-separation-rerun.trial.verdict.json) · [artifacts](campaign-planning-separation-rerun/) |
| `recovery` | `ses_f35c35ff5fferHMDmWoFA8KrUu` | 16s (17:49:25–17:49:41Z) | 4.123s | artifacts/checks passed; original verdict **false failure** on a quoted `grep` inspection; corrected regrade of the **same session** passes | [trial](recovery.trial.json) · [original verdict](recovery.trial.verdict.original.json) · [corrected regrade](recovery.trial.verdict.regrade.json) · [artifact](recovery/trial-output/recovery-report.json) |
| `recovery` (rerun) | `ses_f35b2ac43ffeSKA2z9I2TIVo0P` | 10s (18:07:39–18:07:49Z) | 6.252s | **fail (genuine, non-portable probe)**: OpenCode auto-denied `external_directory(/proc/999999/*)` before the command executed, so no report was produced; this is a denied-probe failure, **not** a grader false failure and **not** proof the pid is dead | [trial](recovery-rerun.trial.json) · [verdict](recovery-rerun.trial.verdict.json) |
| `recovery` (portable, final) | `ses_f35add404ffe4kza6cXUbNwp4P` | 19s (18:12:57–18:13:16Z) | 3.415s | **pass** under `workspace-protocol-v1`: `kill -0 999999` completed with exit 1 / "No such process" and the report was written; no `/proc` read and no denied action | [trial](recovery-portable.trial.json) · [verdict](recovery-portable.trial.verdict.json) · [artifact](recovery-portable/trial-output/recovery-report.json) |
| `challenger-research` | `ses_f35c31fa2ffeOOOPLso4BuWAGP` | 29s (17:49:41–17:50:10Z) | 2.837s | **pass**: source excerpt and opportunity map read, plan scoped without superiority claims | [trial](challenger-research.trial.json) · [verdict](challenger-research.trial.verdict.json) · [artifacts](challenger-research/trial-output/) |
| `discovery-research` (GPT-6 Luna) | `ses_f3343fdc5ffeGpTIYX68nEH0SF` | 4m24s (05:27:45–05:32:09Z) | 8.509s | Original verdict failed on the scheduler enum; same-observation corrected regrade **passes** with the disclosed `unverified` value. | [trial](discovery-research-luna.trial.json) · [original verdict](discovery-research-luna.trial.verdict.json) · [same-trial regrade](discovery-research-luna.trial.verdict.regrade.json) · [provenance](discovery-research-luna.trial.verdict.regrade.provenance.json) · [artifact](discovery-research-luna/trial-output/research-discovery.json) |
| `discovery-contribution-cold-start` (GPT-6 Luna, initial) | `ses_f333f435effeE5fjU8Iw1KLUJC` | 3m14s (05:32:53–05:36:07Z) | 15.862s | **fail retained (original field-contract ambiguity)**: `selected_task` is a prose contribution description, not a canonical identifier; no regrade was made. | [trial](discovery-contribution-cold-start-luna.trial.json) · [verdict](discovery-contribution-cold-start-luna.trial.verdict.json) · [artifact](discovery-contribution-cold-start-luna/trial-output/contribution-discovery.json) |
| `discovery-contribution-cold-start` (GPT-6 Luna, rerun) | `ses_f33302f7cffeQllBfaNBfPWbq6` | 3m14s (05:49:18–05:52:32Z) | 17.388s | Original verdict failed only because the helper invocation was required. The same observation regraded **pass** after recognizing its actual canonical-source reads; this is not a new run. | [trial](discovery-contribution-cold-start-luna-rerun.trial.json) · [original verdict](discovery-contribution-cold-start-luna-rerun.trial.verdict.json) · [same-trial regrade](discovery-contribution-cold-start-luna-rerun.trial.verdict.regrade.json) · [provenance](discovery-contribution-cold-start-luna-rerun.trial.verdict.regrade.provenance.json) · [artifact](discovery-contribution-cold-start-luna-rerun/trial-output/contribution-discovery.json) |

The discovery prompts disclose the objective, README entrypoint, deliverable
path, and output schema without giving the selected route ID. The observations
above are native OpenCode runs only; they do not establish results for another
harness. The cold-start fixture's missing `AGENTS.md` and `CLAUDE.md` describe
visible workspace contents only and make no blanket claim about native
instruction loading. No library search, simulation, algorithm comparison, or
algorithm result is implied by these discovery observations.

## How to read the table

- **First recorded tool action** is the first timestamped tool event the
  wrapper observed minus the session start. It is a latency observation, not
  an independently graded usefulness measure, and a missing value would stay
  `unknown` rather than be fabricated.
- **A clean trace cannot prove absent unobserved actions.** The grader checks
  observable artifacts, recorded tool actions, and validator behavior; it
  does not and cannot prove the agent did nothing else outside the trace.
- **Original and corrected verdicts are grading versions of the same
  session.** The recovery regrade reuses the original workspace and trial
  record; it is not a new agent trial and does not upgrade the original
  observation into a measured result. It also re-ran against a
  **reconstructed** trial-time baseline, not an untouched original baseline
  (see the provenance file).
- **The earlier campaign failure stands and the rerun does not erase it.**
  The original missing-deliverable failure is retained; the rerun passed under
  `workspace-protocol-v1` after the runner stopped exposing the source
  checkout path and started sending the common protocol to every child.
- **The recovery rerun failed because of a non-portable probe, not grading.**
  Its command included `kill -0` plus an optional `/proc/999999` read, and
  OpenCode auto-denied the external directory before execution; no report was
  written, and the denial is not evidence that the pid is dead. The fixture
  prompt and the grader's required liveness-probe action were then made
  portable, and the later `recovery` portable trial passed with `kill -0`
  alone (no `/proc`).
- **All four cases now have passing fresh native observations.** After the
  `recovery` portable rerun passed, each case (`docs-fix`,
  `campaign-planning-separation`, `recovery`, `challenger-research`) has at
  least one passing fresh separate native trial in this table. This is **not
  one universal benchmark** and the observations are **not necessarily on
  identical snapshots**: each record carries its own
  `source_snapshot_sha256`, and they differ across runs. Single-trial
  observations say nothing general about a model, harness, or algorithm.
- **Protocol version.** The initial `docs-fix`, campaign, and `recovery`
  trials predate `workspace-protocol-v1` and have no `child_prompt` block.
  The reruns (`docs-fix`, campaign, `recovery` portable) used
  `workspace-protocol-v1` and record the protocol version plus the rendered
  child-prompt sha256.

See [`docs-fix-initial-notes.md`](docs-fix-initial-notes.md) for the retained
false-negative detail.

## Record quirks

- The initial and rerun `docs-fix` records both name the same
  `raw_events_scratch` path (`.tmp/fresh-agent-trials/docs-fix.raw-events.jsonl`)
  because the runner derived scratch names from the case id before the fix.
  The initial raw stream was preserved locally as
  `docs-fix-initial.raw-events.jsonl` (matching the initial record's recorded
  sha256); the rerun then overwrote the original scratch path. New runner
  versions derive the scratch filename from the output/trial identity, so this
  cannot recur.
- The archived `prepare-record.json` / owner-marker files were excluded
  because the pre-fix records embedded the source checkout path; the trial
  records already carry the snapshot hash and workspace identity.
- Trial records from before `workspace-protocol-v1` have no `child_prompt`
  block. That absence is itself evidence that the records predate the common
  protocol, not a missing observation.
