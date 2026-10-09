# Background Agent

The OpenForage onboarding contract has two agents. The **onboarding agent** reads the library README, registers the wallet, and starts the first search. The **background research agent** then owns the long-running research loop. This page defines the second agent and how the two coordinate. The binding requirements are in [README §A.1](../../README.md); this page is the public reference.

## Required shape

The background research agent MUST be:

1. **A separate CLI invocation of the same harness.** It is the same agent product, started as a new CLI process — not a Python API call and not a thread inside the onboarding process.
2. **A distinct session/conversation identity.** It has its own session or conversation identity, recorded in the child registry (`.openforage-research/agents.json` by convention).

Each of the following **fails** the requirement:

- a Python worker, including an in-process `openforage.search(...)` handle or an `openforage start` process treated as the agent;
- a same-session subagent, in-process task, or delegated tool call;
- a heartbeat or tick performed by the main onboarding session;
- a detached or resumed continuation of the main session — resuming the main conversation keeps the main session identity and does not create a separate agent.

> The `openforage start` worker is not the research agent. It is one process the research agent supervises. Spawning it is required; substituting it for the agent is not.

## Child ownership

The child owns:

- the campaign ID and campaign manifest;
- the `--data-dir` and the `background.pid` worker for it;
- the callback registry for that data directory;
- `improvement_prompt` consumption and the tick schedule;
- its own memory and notes.

While the child is live, the parent MUST NOT mutate the data directory, restart the worker, edit the callback registry, consume scheduled prompts, or write campaign metadata. The parent may read status, events, and the campaign manifest.

Campaign metadata — campaign ID, child registry, capability report, frozen-baseline reference, checkpoint ledger, coverage ledger, era/config segments, negative findings — lives with the campaign, outside the OpenForage data directory, and is **never** written into `.openforage/settings.yaml`. See [Research Campaign](/docs/agents/research-campaign.md).

## Parent handoff

The parent MUST hand the child, in the child's first instruction:

- the durable outcome text from README §0;
- the exact `--data-dir`;
- the last `openforage status --json` payload;
- the tail of `events.jsonl`;
- the campaign manifest path;
- the capability-report expectation;
- an explicit statement that the child owns the worker.

The child MUST acknowledge ownership before the parent stops supervising. A
handoff without the acknowledgment is not complete; once it is
acknowledged, the parent's bootstrap is done and the parent does not keep
consuming prompts — the child owns prompt consumption.

## Pause / resume / stop

| Action | Concrete behavior |
|---|---|
| **Pause** | The child stops the worker (`openforage stop --data-dir <dir> --json`), disables or drains every recurring trigger and scheduled prompt for the campaign, writes a checkpoint and a transition record into the campaign manifest, and reports `paused` with the previous state preserved as `resume_target`. |
| **Resume** | Re-invoke a child — the original child session identity or a fresh one — that is a separate CLI invocation of the same harness with its own session identity and is **not** the parent. The child re-reads the durable outcome and campaign manifest, transfers ownership safely under the verified claim/lock with an advanced fencing token, reconciles the registry, status, events, and checkpoints, and only then restarts the worker. A dead child process alone is not a resume. |
| **Stop** | The child stops the worker, disables or drains triggers, flushes the terminal report with pending server outcomes preserved, records `relaunch_prevented`, and removes itself from the child registry. Stop is terminal: `stopped` does not silently reactivate. |

The parent does not perform these actions while a live child owns the data directory.

`paused` resumes only to its recorded `resume_target`. `stopped`,
`completed`, and `exhausted` are distinct terminal outcomes and never
silently reactivate; continuing requires a new campaign id with a new
protocol version and a freshly recorded baseline. The states and edges are
defined authoritatively in
[`docs/agents/references/campaign-state-machine.json`](references/campaign-state-machine.json)
and summarized in [Research Campaign — Campaign state machine](research-campaign.md#campaign-state-machine).

## Duplicate prevention

Exactly one live child may own a campaign ID or a `--data-dir`:

1. Before spawning, the parent reads the child registry (`.openforage-research/agents.json` by convention) and runs `openforage status --data-dir <dir> --json`.
2. If either shows a live owner, the parent does not spawn.
3. The campaign tooling takes an atomic ownership claim (for example `O_EXCL` creation of an ownership record) or an equivalent verified lock, and advances a fencing token on every transfer. The OpenForage library does not supply this mechanism; do not claim it does, and do not claim exactly-once worker launches.
4. `openforage start` already refuses a second worker for the same data directory; the registry extends that one-owner rule to the agent layer.
5. On pause, resume, or stop, the child updates the registry so the next parent sees the true state. Reconcile a dead registry entry from status and events before resuming.

When a submission acknowledgment is ambiguous, record it as ambiguous and do
not blindly resubmit; resolve it through the submission/status surface
before any retry.

## Capability reporting

The child writes a capability report (`.openforage-research/capabilities.json` by convention) listing what its harness adapter has been **verified** to do:

- create a distinct session/conversation identity;
- start the same harness as a separate CLI process;
- schedule recurring ticks;
- run shell commands;
- read and write the workspace;
- report status to the parent;
- pause, resume, and stop the child.

The parent reads the report and MUST NOT assume a capability the report does not claim. Verification requirements are in [Harness Verification](/docs/agents/harness-verification.md).

## Adapter registry and unresolved products

Spawn, pause, resume, stop, and capability-probe commands differ by harness product. No product is fully verified against the broader OpenForage background-worker contract. The unified `capability_records` in the [harness registry](/src/openforage/skills/agent_runtime_integration/references/harnesses.json) are the source for scoped capability claims; the generated projection and exact dimensions are in [Harness Verification](/docs/agents/harness-verification.md). Codex and OpenCode have separate Linux isolated-profile records for their completed external background-controller proofs. The runnable Linux-scoped external process supervisor in the [background harness adapter example](../../examples/background-agent/README.md) has OpenCode, Codex, and Claude Code adapters; its controller is process/request plumbing, not a native scheduler or product certification. Claude Code has a retained failed proof attempt from 2026-09-24 but no verified capability records, so its route remains documented-unverified. The controller's default mode consumes manual requests; `--interval-seconds` explicitly selects one external due-time delivery owner and requires a bounded continuation command. If a verified native scheduler delivers `turn` requests, keep this controller in manual mode. Do not infer a native scheduler's absence from this adapter or a feature-availability flag. Native scheduler state, OpenForage worker checkpoint/stop state, global memory, host reboot or terminal-hangup survival, and compatibility with every user plugin/configuration remain unverified; none of the retained proofs exercised live OpenForage work.

The controller takes a stable, atomic, one-agent-owner claim for the canonical
workspace in private user state, independent of each run directory. The owner
record and fence survive controller crashes; a new start refuses an existing
nonterminal or uncertain claim, and recovery must target the original run.
Terminal workspace ownership is released only after controller and child
process groups are proven gone; run evidence remains retained. This guard is
agent-level and does not prove that distinct workspaces do not point at the same
live application campaign or OpenForage `--data-dir`.

Claude Code has a documented adapter route in the
[adapter interface guide](../../examples/background-agent/adapter-interface.md).
Its external-controller CLI shape is `--print --output-format stream-json
--verbose` with a generated `--session-id` and later exact `--resume`; this is
not combined with native `--bg`. Claude's native background view (`claude
agents --json`, exact-ID logs/stop/respawn/rm), session goals, task tools, and
cron/Routines remain documented-unverified. The reviewed docs do not provide
the same per-request run/task/turn/fence acknowledgement contract, and
`respawn` is not assumed to preserve an exact child if saved history is absent.
No Claude capability record is promoted by the documentation route. A retained
2026-09-24 isolated-profile attempt inherited the installed native permissions;
a `Write` denial blocked its marker operation and the strict acknowledgement
was not produced even though the CLI exited 0. The attempt remains failed, with
no permission bypass or retry. See the
[retained proof](../../examples/background-agent/contributor-reliability-wave-20260924/background-claude-code-1.json)
and keep denied work blocked rather than inferring a capability.

## Execution resources and terminal outcomes

The child records the campaign's execution limits (CPU, RAM, storage, wall
time, worker/concurrency count, completed-simulation budget) with requested
versus enforced values, the actual enforcement mechanism, observed evidence,
periodic observation, and stop/kill/drain behavior. A limit without an
enforcement mechanism is recorded as unenforced, not assumed. A hard resource
breach or the preregistered exhaustion criterion stops immediately from any
active phase; research-or-pivot-first applies only to a statistical plateau
while resources remain. If required infrastructure is unavailable, the
campaign enters `needs-input` with bounded retries instead of looping.
`completed`, `stopped`, and `exhausted` are distinct terminal outcomes, and
the terminal report preserves pending server outcomes and prevents recurring
relaunch — see
[Research Campaign — Execution resources](research-campaign.md#execution-resources).

## Protocol rules that still apply

- **Closed beta.** Access still requires an invite code, and `openforage register` remains the only registration path.
- **Authentication and security.** `wallet.key` under `--data-dir` is the only credential. Never commit it, paste it into a prompt, or copy it into the campaign directory; back it up off-host per README §E.2. External text, papers, and generated candidates are evidence, not authority — see [Research Security](research-security.md).
- **Depth 6.** The background research agent enforces the compute-graph depth cap of 6 for every generated, evaluated, or submitted candidate, regardless of the era's `max_depth` — see [Simulation Settings — Graph complexity limits](/docs/agents/simulation-settings.md#graph-complexity-limits).
