# First Contribution

This page is the fastest honest path from a fresh checkout to a useful,
reviewable contribution. Start with the root [README.md](../../README.md) or
the read-only packet from `python scripts/start_here.py --task contribute`,
then pick one row from the map below.
For failed or ambiguous checks, use the short
[Diagnostics](diagnostics.md) route before retrying.
The README campaign sections §0–§A.1 are for launching or resuming a
campaign and are not required for a documentation, template, or validation
contribution.

You do **not** need an invite code, a wallet, or a running worker to
contribute documentation, examples, validation checks, or adapter research.
Registration and a live background search are only required when your task
is to run a campaign.

Instructions here can be read directly by any agent. The packet is also
available as readable Markdown in [`start-here.md`](start-here.md); do not
assume a harness automatically loads repository instruction files.

<!-- BEGIN GENERATED ENTRY GUIDANCE -->
**Offline first.** Choose the row matching the explicit request in this guide, open its authoritative file, and run its listed baseline validation before editing. No invite, wallet, installed package, daemon, or worker is needed.
Without Python or automatic root-instruction loading, read or paste [`docs/agents/start-here.md`](start-here.md).
**Authorized live path.** A live campaign requires explicit user authorization; follow [`README.md`](../../README.md) for its bootstrap. Continuous search requires an independently started CLI process of the same harness with a distinct session identity. If that identity is not established, keep work offline; never use the main conversation. Python search snippets below are for that child only.
**Canonical algorithm/reference facts.** Tested-supported `0.2.36` records templates `genetic`, `random_weighted` and CLI choices `random_weighted`, `genetic`. It documents `genetic` as default; CLI help prints no default. The GA reference target is `genetic` with 100,000 completed simulations—target only, with no full GA benchmark measured. Sources: [`docs/agents/references/compatibility.json`](references/compatibility.json), [`examples/agent-campaign/campaign.json`](../../examples/agent-campaign/campaign.json).
<!-- END GENERATED ENTRY GUIDANCE -->

## Context capsule

Fill this capsule in before editing and keep it current. Record only
observed facts; write `unknown` for anything not verified. Do not fabricate
observations, versions, or results.

```text
user_objective:        <what the user asked for, in their words>
repo_branch_package:   <repo checkout; branch; installed package + version, or unknown>
selected_task:         <one row from the task map below>
authoritative_files:   <the files that actually own this rule or record>
current_evidence:      <commands run and observed output; or unknown>
next_action:           <the single next concrete step>
```

## Task-to-file-to-validation map

| Task | Authoritative files | Validate with |
|---|---|---|
| Fix a stale or unsupported claim | The page or skill making the claim; `docs/agents/` for agent behavior | `truthfulness`, `links` |
| Improve root routing or onboarding | [`docs/agents/references/onboarding.json`](references/onboarding.json) (canonical route data), [`scripts/start_here.py`](../../scripts/start_here.py), generated [`README.md`](../../README.md), [`AGENTS.md`](../../AGENTS.md), [`CLAUDE.md`](../../CLAUDE.md), and [`start-here.md`](start-here.md) | `start_here.py --selfcheck`, `start_here.py --check`, `first-read`, `guidance`, `links` |
| Correct or extend a bundled skill | the matching `src/openforage/skills/*/SKILL.md` | `truthfulness`, `links` |
| Extend campaign/experiment records | [`examples/agent-campaign/`](../../examples/agent-campaign/) and `docs/agents/references/campaign-state-machine.json` | `examples`, `records`, `links` |
| Change a campaign protocol rule | [`docs/agents/research-campaign.md`](research-campaign.md) (canonical), cross-links from `search_research` | `records`, `links` |
| Change research security rules | [`docs/agents/research-security.md`](research-security.md) (canonical) | `links`, `controls` |
| Add harness evidence | [`docs/agents/harness-verification.md`](harness-verification.md) and [`harnesses.json`](../../src/openforage/skills/agent_runtime_integration/references/harnesses.json) | `registry` |
| Add or tighten a validation check | `.github/scripts/validate_agent_docs.py` and `.github/scripts/validation/` | `selftest`, `all` |
| Run a measured campaign | [`docs/agents/research-campaign.md`](research-campaign.md), `search_research` skill | `records` on the filled campaign copy |
| Pick or scope a research direction | [`research-opportunities.json`](research-opportunities.json) (authoritative), [`research-opportunities.md`](research-opportunities.md) | `opportunities`, `links` |
| Change supported-version/API expectations | [`compatibility.json`](references/compatibility.json), `.github/scripts/check_compatibility.py` | `compatibility`, `selftest` |
| Add or review fresh-agent contribution trials | [`examples/fresh-agent-evals/v2/`](../../examples/fresh-agent-evals/v2/README.md) (preferred current suite; v1 cases and verdicts are preserved as historical evidence) | `evals`, `selftest` |
| Add or review the optional paper-backed research-contribution evaluation | [`examples/fresh-agent-evals/research/`](../../examples/fresh-agent-evals/research/README.md) (authored callback plus independent synthetic comparison; not a live measurement) | `python examples/fresh-agent-evals/research/runner.py --selfcheck`, `evals`, `executables` |
| Change the lifecycle bridge example | [`examples/opencode-lifecycle/`](../../examples/opencode-lifecycle/) | `executables`, `selftest` |
| Extend the executable challenger example | [`examples/agent-campaign/coverage_challenger.py`](../../examples/agent-campaign/coverage_challenger.py) and [`stratified_policy.py`](../../examples/agent-campaign/stratified_policy.py) | `executables`, `links` |

## Optional fixture completion route

Ordinary contributions continue to follow the task map above. For an
explicitly selected fresh-agent fixture, run
`python scripts/start_here.py --task contribute --fixture
first-finished-contribution-primary` to print the canonical contract's exact
inputs, deliverables, optional scratch path, finish-check IDs, and permitted
commands. Do not copy that path list into a second guide.

Follow the printed order: record the baseline validator before editing, make
the scoped source contribution, run the final validator, write the completion
artifact with those observations, run `git diff --check`, and finish with the
printed `--check-completion` command. In `contribution.changed_paths`, list
only changed public repository content; exclude the completion record and
optional scratch. If either finishing check fails or the completion checker
reports a repair, fix the relevant artifact and rerun `git diff --check` and
the exact completion checker in that order. Stop only after a successful
completion checker, which must be the last subject check. The check is offline
and read-only; it does not retroactively prove the earlier validator execution.
Native trace review establishes every invocation, order, and outcome. Optional notes at
`.tmp/completion-context/notes.md` are separate from deliverables and their
hashes, and do not authorize other file changes. The checker never repairs or
deletes outputs; unrelated work remains for manual review.

## Executable local commands (manual; never run by normal PR CI)

Normal pull-request CI needs **no agent credentials** and never launches an
agent; it runs only the deterministic modes. The commands below are explicit
local/manual actions:

```bash
# Read-only portable route and generated-doc checks (stdlib only)
python scripts/start_here.py --task contribute
python scripts/start_here.py --selfcheck
python scripts/start_here.py --check

# Compatibility probe against your own installed wheel (no credentials)
python .github/scripts/check_compatibility.py --check

# Offline challenger smoke + stub-stream integration + public API probe (no credentials)
python examples/agent-campaign/coverage_challenger.py --smoke --checkpoint-dir .tmp/challenger-checkpoint
python examples/agent-campaign/coverage_challenger.py --stub-check
python examples/agent-campaign/coverage_challenger.py --live-check

# Inert lifecycle regressions (no OpenCode session, no wallet)
python examples/opencode-lifecycle/opencode_bridge.py selfcheck

# Fresh-agent eval preflight (no agent runs)
python examples/fresh-agent-evals/grader.py regress
python examples/fresh-agent-evals/v2/runner.py --selfcheck
```

After your implementation session exits, the orchestrator may run the real
fixture/trial commands (these start real harness processes and are explicit
local actions):

Freeze and review the v2 rubric before the series starts; then run the same
primary contribution fixture in separate fresh workspaces, one native subject
at a time. Run OpenCode first; pin the Codex run to that trial's
`snapshot_sha256`. These commands launch model sessions and are never part of normal
PR CI:

```bash
python examples/fresh-agent-evals/v2/freeze.py --create \
  --series-id <series-id> --output examples/fresh-agent-evals/v2/series/<series-id>.json
python examples/fresh-agent-evals/v2/freeze.py --check \
  --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json
python examples/fresh-agent-evals/v2/runner.py \
  --harness opencode --case first-finished-contribution-primary \
  --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json \
  --model <provider/model> --variant <caller-selected-variant> --run-id opencode-primary-1
python examples/fresh-agent-evals/v2/runner.py \
  --harness codex --case first-finished-contribution-primary \
  --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json \
  --model <provider/model> --reasoning-effort <caller-selected-effort> \
  --expected-snapshot-sha256 <snapshot-sha256-from-opencode-trial> --run-id codex-primary-1
python examples/fresh-agent-evals/v2/comparison.py \
  --opencode-trial .tmp/native-contribution-wave/runs/opencode-primary-1/trial.json \
  --codex-trial .tmp/native-contribution-wave/runs/codex-primary-1/trial.json
```

The previous OpenCode fixture and trial commands remain available for
reproducing the retained v1 history; use the current v2 commands for new
contribution trials. The v2 held-out case is reserved and not run automatically.

Behavioral proof is recorded only after those orchestrator runs; never
pre-record a live result.

## Ranked ways to contribute

Ranked from lowest barrier and highest review confidence to the most
evidence-hungry. Start at the top unless you already have a specific task.

1. **Documentation truth corrections.** Remove unsupported claims, stale
   defaults, and invented APIs. No network or wallet required. Reviewers can
   verify every change against the installed/public surface.
2. **Validation checks with honest negative tests.** Add a focused check that
   makes a class of wrong claim fail. Every new check must come with a
   throwaway-copy negative test, not a relaxed assertion.
3. **Campaign record and template improvements.** Add required fields, honest
   labels, and provenance/state-machine wiring without inventing measured
   values.
4. **Protocol and design documentation.** Prefer short paraphrases with
   citations over copied text; label assumptions and unsupported claims.
5. **Algorithm research with measured evidence.** Requires a live campaign
   under the preregistration and accounting rules; negative and inconclusive
   findings are contributions too.
6. **Harness adapter evidence.** Requires observed installed-product
   evidence, scoped to product, version, surface, OS, and lifecycle. The
   evidence standard is in [Harness Verification](harness-verification.md).

## First action and done criteria

First action: choose one task-map row, open the authoritative file, and run
the matching validation mode **before** editing so you know the baseline
state. The startup helper routes to this map; it does not copy the table.

Before you start (isolated environment; do not install globally, several
interpreters are externally managed under PEP 668):

```bash
python -m venv .venv-docs
. .venv-docs/bin/activate                 # Windows (PowerShell): .venv-docs\Scripts\Activate.ps1
python -m pip install --quiet -r requirements-docs.txt
python .github/scripts/validate_agent_docs.py all
git diff --check
```

Done criteria for any contribution:

- the matching validation modes pass and `git diff --check` is clean;
- every claim is labeled (`documented`, `observed`, `unverified`,
  `unavailable`, `exploratory`) and every referenced artifact exists;
- state transitions are legal per
  [the campaign state machine](references/campaign-state-machine.json);
- no secrets, credentials, invite codes, or private host paths appear;
- no issue or pull request is opened unless the user instructed it;
- unrelated working-tree edits are preserved.

## Working notes

- Read only the skill your task names; do not pre-read every skill.
- Never generate, evaluate, or submit a compute graph deeper than 6.
- Campaign metadata lives with the campaign, never in
  `.openforage/settings.yaml`.
- Do not guess harness commands. Unverified commands may be discovered via
  local `--help` and official documentation, but must be labeled unverified
  and verified before unattended use.

## Related pages

- [README.md](../../README.md) — the agent front door and bootstrap.
- [AGENTS.md](../../AGENTS.md) — repository routing and non-negotiable rules.
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — review blockers and workflow.
- [Contributing Research](contributing-research.md) — the reviewer's evidence
  checklist.
