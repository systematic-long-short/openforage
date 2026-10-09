# Fresh-Agent Evaluations (example)

Machine-readable task definitions, fixtures, and a deterministic grader for
fresh separate harness trials. This is an example tool for the public packet;
it is not an OpenForage library feature and it contains no measured results.

## Preferred current trials: v2 finished contributions

Use [`v2/README.md`](v2/README.md) for the current cross-harness first-
contribution suite. It requires a completed documentation correction, an
explicit typed `completion.json`, native result-correlated baseline and final
validator commands, final workspace diff/digests, and independent final
validation. The primary fixture is shared across OpenCode and Codex; a distinct
held-out fixture is defined but never run automatically. Freeze and verify the
series manifest before native runs. There are no public model defaults.

New v2 subjects are measured from a root-first route: recorded content reads of
`README.md` and `AGENTS.md`, then the bounded `start_here.py --task contribute`
selector or readable `docs/agents/start-here.md`, then the task-specific
contribution guide. The grader requires that event order; injected task text
does not substitute for reads. Current verdicts include structured actionable
diagnostics and an `entry_route` evidence summary; historical records remain
unchanged.

For the optional paper-backed research-contribution evaluation, see
[`research/README.md`](research/README.md). It requires an authored executable
callback, source/mechanism/hypothesis artifact, and independently recomputed
synthetic comparison/coverage report; its no-model `--selfcheck` is part of the
offline validation route.
New research subjects follow the same root-first entry route, use the `research`
start-here selector/readable packet, and then read the research README and task.
The public route files linked by that packet are explicitly allowed; protected
evaluator implementation and held-out contents remain outside the subject read
allowlist. Negative, mixed, and incomplete research outcomes remain distinct
from protocol diagnostics.

The v2 suite deliberately retains its public evaluation files in the isolated
source snapshot so production `all` validation has its complete closure. The
task forbids direct case/oracle/answer/grader inspection and the native trace
audits observed access. This is instruction-isolated, not a pristine blind
evaluation or an OS sandbox. The raw native streams stay in ignored scratch;
only sanitized summaries and verdicts are retained.

The v1 suite below is preserved as historical evidence. Its task cases,
grader, trial records, artifacts, and original verdicts are unchanged; v2 is
the preferred route for new contribution trials.

## Preserved v1 suite (historical)

## What is and is not an agent trial

- A **dry/static suite** — reading `tasks.json`, running `grader.py regress`,
  or reviewing the fixtures — is not an agent trial.
- An **actual trial** means a fresh, separate CLI invocation of a harness
  (its own session identity) works the task in a throwaway workspace, and the
  harness records a sanitized trial record. Only then does `grade` produce a
  verdict about that trial.
- **No blind claim.** Fixtures and graders are visible in this public
  checkout on purpose, so no clean blind-eval claim is made. If you need a
  blind run, keep a private copy of the grader and fixtures outside the
  trial workspace and disclose the difference.
- Trials are graded on observable artifacts, actual recorded tool actions,
  and the fixture's real validator behavior — not on self-reported booleans.
  A missing trace, an unmet expected action, or an empty action list stays
  fail/unknown and is never converted to success. `grade` also re-prepares a
  fresh baseline and reports every write outside the case's exact allowed
  paths as an isolation violation.

## Cases

| Case | Route under test | Observable deliverable |
|---|---|---|
| `docs-fix` | Fix a validator-rejected onboarding page and leave command evidence. | restored page + `trial-output/evidence.json` |
| `campaign-planning-separation` | Resolve a two-owner campaign conflict into one live owner and plan the handoff. | separation plan + repaired claim; settings untouched |
| `recovery` | Classify a stale worker from fixture files and propose a bounded recovery without signalling. | `trial-output/recovery-report.json` |
| `challenger-research` | Select one opportunity from the research map, read its archived attributed source, and scope it honestly against the public API. | `trial-output/research-plan.json` + source evidence |
| `discovery-research` (`discovery`) | Starting from README, route a signal-seeking objective to a useful offline research step despite unavailable campaign access, package, and verified scheduler. | `trial-output/research-discovery.json` |
| `discovery-contribution-cold-start` (`discovery`) | Starting from README, route an explicit documentation contribution when the prepared workspace omits root instruction files. | `trial-output/contribution-discovery.json` |
| `discovery-quickstart-entry` (`discovery`) | Start directly from quickstart/first-search and produce evidence-backed offline research under the enforced file-tools-only child profile. | `trial-output/quickstart-research.json` |
| `discovery-pasted-entry` (`discovery`) | Use the canonical root route pasted into the task to plan an offline contribution when Python/autoload are unavailable. | `trial-output/pasted-entry-contribution.json` |

Every case prompt names its deliverable paths and exact JSON field schema
(including `case_id`); the grader does not depend on hidden artifact paths or
field names. Discovery prompts disclose the schema and enum alternatives, not
solution commands or selected answer values. They cover README entry, direct
quickstart/first-search entry, and a canonical route pasted into the task.
`selected_task` is the canonical task identifier from the startup helper or
onboarding task definitions, not a human-readable title or description. A
route can be established by a real CLI selector where available or by actual
readable-source use; quoted filename mentions do not count. When a field copies
a value from a discovered record, the prompt requires the exact canonical URL
or identifier from that record without naming the selected value or route.

The two direct/pasted cases select the reviewable runtime JSON profile
[`trial-inputs/opencode-file-tools-only.json`](trial-inputs/opencode-file-tools-only.json)
as `entry-files-only`. The runner injects it through documented
`OPENCODE_CONFIG_CONTENT`, then runs the non-model
`opencode debug agent entry-files-only`
command and retains only the selected agent's effective permission matrix.
The profile puts wildcard `* = deny` first, permits only the `read`, `list`,
`glob`, `grep`, and `edit` permission keys, and keeps `external_directory`
denied. OpenCode's `edit` permission covers edit/write/patch operations; the
grader retains the emitted `edit`, `write`, or `apply_patch` event identity and
confines it to the declared artifact path. This does not admit shell, network,
task, or other tool permissions. Each case seeds its disclosed artifact so
the file-only agent can update it. The prepared workspace contains no
`.opencode` directory or `node_modules`; the config probe
checks both before and after. Actual runs add `--pure`, which disables external
plugins; it is not described as a filesystem sandbox. The prompt is not the
enforcement mechanism, and this profile does not claim the host lacks a Python
executable. The fixtures remove root `AGENTS.md` and `CLAUDE.md`, recording
visible files only without a native-autoload claim. Prepared snapshots include
`legal/terms.md` and public documents linked from the case entrypoints.
The `challenger-research` fixture supplies an
attributed archived excerpt at `trial-inputs/primary-source-excerpt.md` with
its provenance, because the fixture forbids network access; reading that
archived excerpt is the recorded reopening, and no reopening is invented.

The runner prepends a **common trial protocol block** (`workspace-protocol-v2`)
to the case prompt before delivering it to the child: workspace root,
workspace-only reads/writes/tools, no source checkout or hidden
fixtures/graders, no nested agents or model sessions, no network, disclosed
artifact schemas, and the one case-mandated non-signalling process-liveness
probe exception. Discovery route answers remain in workspace sources rather
than being stated in the prompt.
The case prompt in `tasks.json` stays exact case content; for the pasted-entry
case the runner also appends the canonical generated README route by value.
The record carries the protocol version, rendered-prompt hash, and pasted
route hash (rendered text is not retained because it embeds the workspace
path).

## Retained trial evidence

Sanitized records, verdicts, and the produced deliverable artifacts from real
trials live in [`trials/`](trials/), including the retained `docs-fix` false
negative and the corrected `recovery` regrade. Those are single-trial
observations only — no model benchmark, no blind test, and no library
evaluation. All six discovery entry and cold-start observations are summarized
in the [`entrypoint-outcomes-20260923` archive](trials/entrypoint-outcomes-20260923/README.md).
Original entry failures remain preserved alongside two clearly labeled
same-native-run rubric-only regrades. The earlier
[`closeout-20260923.md`](trials/entrypoint-outcomes-20260923/closeout-20260923.md)
retains prior explanations and distinguishes the cold-start trial pass from
its nonzero pre-edit validator result.

## Run protocol (orchestrator)

All commands run from the checkout root. Never point a trial at the real
checkout, a wallet, a data directory, or the network. The orchestrator runner
prepares the workspace, launches one fresh `opencode run --format json`
session, captures the actual tool events, writes a sanitized trial record,
and invokes the grader:

```bash
# 1. Inspect the suite.
.venv-docs/bin/python examples/fresh-agent-evals/grader.py list

# 2. Prepare-only (no agent) when you want to inspect a fixture.
.venv-docs/bin/python examples/fresh-agent-evals/grader.py prepare \
  --case docs-fix --dest .tmp/fresh-agent-trials/docs-fix

# 3. Run one fresh separate trial and grade it. Run it after your
#    implementation session exits; it starts a real agent invocation. The
#    caller-selected model and variant are passed in the actual run argv.
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
  --case docs-fix --model <provider/model> --variant <label> \
  --output .tmp/fresh-agent-trials/docs-fix.trial.json

# Discovery trials — run only after the implementation session exits.
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
  --case discovery-research --model <provider/model> --variant <label> \
  --output .tmp/fresh-agent-trials/discovery-research.trial.json
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
  --case discovery-contribution-cold-start --model <provider/model> --variant <label> \
  --output .tmp/fresh-agent-trials/discovery-contribution-cold-start.trial.json
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
  --case discovery-quickstart-entry --model <provider/model> --variant <label> \
  --output .tmp/fresh-agent-trials/discovery-quickstart-entry.trial.json
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py \
  --case discovery-pasted-entry --model <provider/model> --variant <label> \
  --output .tmp/fresh-agent-trials/discovery-pasted-entry.trial.json

# 4. The verdict is written beside the trial record:
#    .tmp/fresh-agent-trials/docs-fix.trial.verdict.json
```

`run_trial.py --events-file <captured.jsonl>` replays a previously captured
native JSON stream instead of launching an agent; it exists so the runner and
action extractor can be regression-tested offline. Replay trials are labeled
`execution_mode=events-file-replay` / `fresh_separate_invocation=false` and
are never presented as a fresh actual run.

Trial records are sanitized: they carry the prepared-snapshot content hash
(the source HEAD alone ignores uncommitted candidates), normalized tool
observations with versioned native outcome fields, and non-JSON plain text
only. Raw event streams stay only in
git-ignored private scratch, whose filename is derived from the output identity
(`.tmp/fresh-agent-trials/<trial-identity>.raw-events.jsonl`), so a rerun can
never overwrite an earlier trial's preserved raw trace. Fixtures never launch
nested agent tasks: `nested_agent_launches` is forbidden, and the grader
flags `opencode run` or another agent launch in the observed actions.

## Prepared-snapshot isolation

Each prepared snapshot is a throwaway local Git repository:

- `grader.py prepare` runs a local `git init` in the destination only — no
  staging, no commits, no global/system config, no network — so OpenCode
  resolves its project/worktree root and `git status` to the fixture instead
  of the parent checkout. Runner-owned `.git` internals are excluded from
  snapshot hashing and isolation comparisons; every source/config file is
  still compared, so agent edits are never overlooked.
- The shared `discovery_checks.py` helper is retained as a grader dependency
  under `examples/fresh-agent-evals/`; it recognizes command invocations,
  exact canonical route-source reads, and validator modes, not expected
  artifact values. The task
  definitions, fixtures, and `grader.py` remain out of the child workspace.
- The destination is claimed with a marker at
  `trial-output/.prepare-owner.json`. Preparing again over that marked
  snapshot is allowed deliberately; an existing directory that is not a
  recognized grader-owned snapshot is refused before deletion, and the
  source checkout root, its ancestors, filesystem roots, and the home
  directory are refused outright.
  A snapshot prepared by an earlier grader version (a valid
  `trial-output/prepare-record.json`) is recognized as owned so upgrades do
  not require manual cleanup. To retain a failed trial's workspace and
  verdict, point the next run at a new `--workspace`/`--output` path; runner
  code never deletes `.tmp` trial evidence, and reusing the default workspace
  path replaces only that prepared snapshot.
- The owner marker and `prepare-record.json` carry an **opaque hashed source
  identity**, never the source checkout absolute path, so a child reading its
  own prepare metadata cannot navigate to external fixtures or graders.
  Legacy prepare records remain recognized for ownership only.
- The child session runs with `PYTHONDONTWRITEBYTECODE=1`, and the
  validator interpreter's directory is prepended to the child `PATH`, so
  `python .github/scripts/validate_agent_docs.py <mode>` resolves to the
  intended interpreter and leaves no `__pycache__/*.pyc` under frozen dirs.
  `grader.py` applies the same bytecode suppression to every validator
  subprocess it starts.

Repeat for `campaign-planning-separation`, `recovery`, and
`challenger-research`, then the two `discovery` cases when running the new
onboarding evaluation. Keep each workspace until its verdict is archived.
`.tmp/` is git-ignored; sanitize trial records before sharing beyond the
host, and never include credentials, invite codes, wallet material, provider
usage/cost/token metadata, or private host paths.

## Shared task/protocol and record contract

`tasks.json` is the harness-neutral case source: it carries each user
objective, task mode, disclosed artifact contract, fixture conditions,
isolation limits, and observable rubric. The common prompt constraints are
the `workspace-protocol-v1` block in `run_trial.py`; other harnesses should
apply the same workspace-only, no-network, no-credentials, no-install,
no-nested-agent/model, and sanitized-trace conditions without copying an
unverified command recipe. Use `trial-record.template.json` to record a fresh
separate CLI invocation, distinct session identity, actual relevant actions,
fixture conditions, source snapshot hash, sanitized artifact references, and
unknown values honestly. The provided orchestrator launches OpenCode only;
it does not claim other harnesses are supported by that runner.

## Deterministic grader regressions

`grader.py regress` prepares every case, writes a correct artifact set and a
plausible wrong artifact set, and asserts the expected verdicts. It also
covers the independent-review regressions directly:

- correct outputs with a matching action trace pass;
- wrong artifacts, fabricated command claims, empty action lists, missing
  trials, metadata-only artifact sets, wrong-case artifacts, tampered
  `.openforage/settings.yaml`, and a sibling `*.md.bak` file outside an
  exact-file allowance all fail;
- the prepared `docs-fix` fixture fails the real `truthfulness` validator
  before repair (the validator mode itself runs the pre-failure check);
- quoted read-only inspection (for example `grep "openforage stop" README.md`)
  is not misclassified as an invocation, while a chained actual invocation
  (`true && openforage start ...`) and a real lifecycle command still fail;
- discovery rejects a main-session fallback posture even when its boolean
  says `false`; the scheduler rubric accepts exactly the disclosed
  `unknown`/`unverified` no-evidence alternatives while rejecting `verified`,
  arbitrary prose, and boolean contradictions;
- both discovery prompts statically require `selected_task` to be the
  canonical route identifier, rather than a task title or description;
- the singular `baseline_command` must be a recognized validator command and
  match the command recorded in the actual trace; invented or trace-matched
  invalid modes fail;
- the contribution route accepts either an observed helper invocation or an
  actual recorded read of canonical task definitions/readable onboarding
  content. Structured `read` paths are authoritative; simple `cat` file
  operands are also recognized, while unrelated reads and quoted filename
  mentions in grep/search inspections do not satisfy the action;
- direct/pasted entry cases require completed file-tool reads, a completed
  artifact-write action (`edit`, `write`, or `apply_patch`) on the seeded
  artifact, the admitted child permission profile, and task-relevant evidence.
  The `edit` permission key is not confused with the native event label. Empty
  traces, a wrong entry route, shell/Python tool use, and main-session fallback
  are rejected;
- a fresh native entry verdict checks normalized file-action outcomes,
  command digests, call IDs, and completion states against the retained raw
  event stream and its recorded digest. This is a consistency check, not
  cryptographic authentication. `events-file-replay` is replay-only and never
  establishes fresh-native execution;
- grader regressions use `execution_mode=inert-grader-regression` and are
  explicitly synthetic. A passing synthetic rubric selfcheck validates the
  checker only; it is not a model trial or native-execution proof. Full verdicts
  report this distinction in `evidence_scope`;
- direct-entry research accepts either an observed completed read of canonical
  `docs/agents/references/onboarding.json` or readable `docs/agents/start-here.md`
  without requiring a README reread; the actual first-search entry, public
  research map, compatibility facts, campaign facts, and cited evidence remain
  required;
- pasted contribution targets may be a suitable existing `docs/` Markdown
  page or the canonical `docs/agents/references/onboarding.json`, provided the
  target is confined, exists, is actually read, and is cited. The live-boundary
  check tests explicit authorization, a separate same-harness session identity,
  and no main-session fallback; its prose need not contain a literal README token;
- contribution-helper invocations accept either option order and
  `--task=contribute`, while a quoted read-only inspection of that command
  does not satisfy the route action;
- outside-workspace reads and commands are rejected from the recorded trace
  (source-checkout references, `../` traversal, filesystem-root access,
  absolute paths outside the workspace), while the case-mandated
  non-signalling liveness probe (`kill -0 <pid>` or Python `os.kill(pid, 0)`)
  stays allowed, while `/proc` inspection is rejected as outside the
  workspace-bound fixture.
  This is an instruction + trace check, **not** sandbox-level enforcement, so
  a clean trace still cannot prove the absence of unobserved actions.
- baseline validation requires a full, untruncated standalone validator
  command, paired with its native call ID and that action's own native exit
  metadata. A validator embedded in a pipeline, conditional, or compound shell
  command cannot inherit the enclosing shell result; normal environment
  prefixes remain valid. `completed` alone does not mean exit 0. A failed
  baseline is useful when the artifact honestly reports the observed nonzero
  exit. Older invocation-only records remain `unknown`; later metadata does not
  retroactively upgrade them.

```bash
.venv-docs/bin/python examples/fresh-agent-evals/grader.py regress
.venv-docs/bin/python examples/fresh-agent-evals/run_trial.py --selfcheck
```

`run_trial.py --selfcheck` is offline: it checks prompt/profile fixture
boundaries, wildcard-default permission fallback for an unknown tool, safe
debug-agent matrix extraction, distinct scratch paths, native call-ID
correlation, bounded command hashes, output redaction, and the distinction
between tool status and explicit shell exit metadata.

## Honest limits

- A single trial says nothing general about a model or harness; retain the
  harness, version, model, session id, case id, and timestamps with each
  verdict.
- `first_useful_action_seconds` and `clarification_count` stay `unknown`
  unless the harness wrapper observes them; the grader never invents them.
- The grader checks artifacts, recorded actions, and validator behavior. It
  cannot prove the agent reasoned correctly, only that the required observable
  outcomes exist and that forbidden actions were not recorded. Discovery
  capability availability and search posture are checked as structured
  enum/boolean fields; free-form prose and reasoning quality are not proven.
- A recorded command establishes that the invocation appeared in the captured
  action trace. Native tool status and an explicitly captured shell exit code
  are separate fields; missing results stay unknown. Free-form reasoning is
  not graded. The full verdict's `evidence_scope` distinguishes synthetic
  rubric checks, replay data, and fresh native execution; retained-stream
  consistency does not authenticate who produced the stream.
- Trial grading must not include `openforage register`, `openforage start`,
  `openforage stop`, package installs, network fetches, or broad process
  kills. The grader flags those when they appear in the trial record.
- Multiple semantically equivalent solutions are accepted where the case
  defines equivalent outcomes; the prompt's schema is the contract.
