# Background harness adapter

[`scripts/background_agent.py`](../../scripts/background_agent.py) is a
Linux-scoped external controller that launches a caller-selected OpenCode,
Codex, or Claude Code CLI, records its distinct child session, and sends later explicit bounded
turns only to that session. It is process/request plumbing, not a native
harness scheduler or an OpenForage worker. It installs nothing, writes no boot
service, copies or mutates no harness configuration/authentication home, and has
no model default. Its workspace guard is agent-level only; it does not prove
campaign or OpenForage data-directory ownership across different workspaces.
The 2026-09-23 Linux-scoped proof records remain summarized in the
[native contribution-wave archive](native-contribution-wave-20260924/README.md);
they retain their historical source scope and do not certify every harness
configuration or prove OpenForage worker state. The later
[2026-09-24 reliability archive](contributor-reliability-wave-20260924/README.md)
retains post-repair v2 attempts and their tested source snapshots: OpenCode and
Codex passed the bounded external-controller edges but remain partial overall,
while the Claude Code attempt failed under inherited native permissions. Neither
archive promotes product-wide support or OpenForage worker behavior.

## Start only with an explicit task command

The caller supplies the harness, parent session identity, model and
harness-specific reasoning selector, existing workspace, durable task file,
new state directory, and an explicit initial command. The final component of
`--state-dir` is the run ID and must not already exist. The task file must be a
regular UTF-8 file inside the workspace. The workspace and state directory may
not overlap; symlinked paths and state paths inside a tracked checkout are
refused. Put state under an ignored directory such as `.tmp/` or outside the
checkout. Raw event streams and detached-controller logs stay under the private
state directory; if it is inside the checkout, it must be Git-ignored.
The selected state directory's parent must already exist. Relative CLI paths
are resolved from the invoking working directory and then persisted as absolute
paths.

OpenCode CLI shape (select the values locally; none are defaults):

```bash
.venv-docs/bin/python scripts/background_agent.py start \
  --harness opencode --parent-session <PARENT_SESSION_ID> \
  --model <provider/model> --variant <variant> \
  --workspace ./work --task ./work/TASK.md \
  --state-dir .tmp/background-agent-opencode-run-01 \
  --command "Read TASK.md and report the next bounded step."
```

Codex CLI shape:

```bash
.venv-docs/bin/python scripts/background_agent.py start \
  --harness codex --parent-session <PARENT_THREAD_ID> \
  --model <model> --reasoning-effort <effort> \
  --workspace ./work --task ./work/TASK.md \
  --state-dir .tmp/background-agent-codex-run-01 \
  --command "Read TASK.md and report the next bounded step."
```

Claude Code CLI shape:

```bash
.venv-docs/bin/python scripts/background_agent.py start \
  --harness claude-code --parent-session <PARENT_SESSION_UUID> \
  --model <model> --effort <low|medium|high|xhigh|max> \
  --workspace ./work --task ./work/TASK.md \
  --state-dir .tmp/background-agent-claude-run-01 \
  --command "Read TASK.md and report the next bounded step."
```

Claude Code uses a caller-selected fresh UUID through `--session-id`,
`--print --output-format stream-json --verbose --include-partial-messages`, and
`--resume <EXACT_SESSION_ID>` for later turns. It never uses `--continue`,
`--fork-session`, a recent-session picker, or `--bg` in print mode (`--bg` and
`-p` conflict). The local installed version observed during adapter development
was `2.1.281 (Claude Code)`; that read-only version/help probe does not establish
native lifecycle behavior.

If more than one Codex installation is found, choose the intended binary
explicitly rather than relying on PATH order. The documented proof driver
and `start` command accept `--binary`, for example
`--binary /path/to/chosen/codex`; do not include a private local executable
path in published evidence.

`start` does not return success until the fresh child CLI exits successfully
and emits the required ownership acknowledgement. The service binds that
acknowledgement to the observed child session ID, run ID, durable-task SHA-256,
and fence. A timeout or missing/mismatched acknowledgement remains `starting`
or `needs_reconciliation`; it is never reported as `ready`. The launcher
redirects stdin/stdout/stderr and starts the controller in a detached Linux
session, so the controller remains after the launcher exits. It starts one
fresh CLI for the bootstrap and, in manual mode, waits for explicit user
requests.

## Explicit recurring delivery

There is no automatic start or continuation by default. To opt in to the
controller's external due-time delivery, provide both `--interval-seconds` and
an explicit `--continuation-command` at `start`:

```bash
.venv-docs/bin/python scripts/background_agent.py start \
  --harness codex --parent-session <PARENT_THREAD_ID> \
  --model <model> --reasoning-effort <effort> \
  --workspace ./work --task ./work/TASK.md \
  --state-dir .tmp/background-agent-codex-run-02 \
  --command "Read TASK.md and make its first bounded local improvement." \
  --interval-seconds 3600 \
  --continuation-command "Re-read TASK.md and current evidence, perform its next useful bounded offline action, update the durable work, and report what changed. Do not only repeat a next-step plan."
```

The continuation command is private durable run state. After each valid
`in_progress` acknowledgement, the service schedules one next due continuation
on the exact child session. A missing/uncertain acknowledgement, `blocked`, or
`agent_reported_complete` blocks recurrence; there is no retry loop. `pause`
drains due triggers. `resume` and `recover` first require reconciliation and
restore only a previously authorized recurrence. Terminal `stop` cancels it
permanently. Status reports scheduler `mode`, `state`, `next_due_at`, and
`block_reason`.

This is an **external timer/delivery shim** within the detached controller, not a
native scheduler or a second task/research engine. If a verified native
scheduler will deliver controller `turn` requests, use manual mode and do not
also set `--interval-seconds`; the child should not create a duplicate schedule.
The external interval is the adapter's explicit due-time delivery option when
no native scheduler is connected; this is not a product-wide claim that no
native scheduler exists. It is never a heartbeat in the main conversation.
Goals, tasks, session history, and the decision about useful work stay with the
child harness.

For research work, the [read-only next-action helper](../agent-campaign/next-action/README.md)
(`python scripts/research_next.py --format text`) can supply a proposal to the
child's durable task or native goal/task.
The harness should turn evidence into and execute the next bounded action; the
proposal helper does not schedule or launch work.

Optional example: review the proposal, put the selected action in the durable
task, and use a documented native goal/task surface when the chosen harness
exposes one. No slash command is assumed:

```bash
.venv-docs/bin/python scripts/research_next.py \
  --campaign ./campaign/campaign.json --format text
# After reviewing the proposal and recording the authorized task/goal:
.venv-docs/bin/python scripts/background_agent.py start \
  --harness codex --parent-session <PARENT_THREAD_ID> \
  --model <model> --reasoning-effort <effort> \
  --workspace ./work --task ./work/TASK.md \
  --state-dir .tmp/background-agent-research-run-01 \
  --command "Implement the first approved bounded action." \
  --interval-seconds 3600 \
  --continuation-command "Read TASK.md and the current native task/goal; implement its next bounded action and record evidence."
```

This proposal-to-goal/task step is optional; `research_next.py` is read-only and
is not a first-use prerequisite for the controller.

The controller enforces one agent owner per canonical workspace across
different run/state directories. Its atomic private metadata lives under
`~/.local/state/openforage-background-agent/`, with per-workspace hashed lock
and owner records (directory mode `0700`, files `0600`). A stale or uncertain
nonterminal claim refuses a new start; explicitly recover the original run.
Terminal evidence remains in the run directory, and a stopped workspace claim
is released only after every owned controller/child group is proven gone. This
agent-level guard does not establish live application campaign or data-dir
ownership when separate workspaces point at the same application data.

The `native` profile is the default and retains the caller's installed harness
configuration/plugins/providers. The isolated proof selects a separate
`isolated` profile; its success does not prove each user's configuration works.
Both profiles use argv arrays, keep applicable project instruction/rule files,
and retain workspace-write/no-bypass protection.

- OpenCode `1.18.31`: normal fresh `opencode run --dir PATH --model
  provider/model --variant VARIANT --agent build --format json PROMPT`; later
  turns add `--session EXACT_ID`. Only the explicit isolated profile adds
  `--pure` (local `--help` says this disables external plugins). The adapter does
  not use `--continue`.
- Codex `0.156.1`: global `--no-daemon --ask-for-approval never --model MODEL
  --cd PATH --sandbox workspace-write -c model_reasoning_effort=EFFORT`, then
  normal `exec --json PROMPT` or `exec resume --json EXACT_ID PROMPT`. The
  isolated profile adds `-c features.goals=true` and `--ignore-user-config` per
  invocation. Config/auth files and auth homes are untouched. Neither profile
  uses `--ignore-rules`, `--last`, or a bypass sandbox.
- Claude Code `2.1.281` local CLI shape: fresh `--print --output-format
  stream-json --verbose --include-partial-messages --session-id UUID`, then
  `--resume EXACT_UUID`; optional caller-selected `--effort`. The controller's
  `native` and explicit `isolated` fixture profiles both inherit normal Claude
  auth, configuration, and permissions. The isolated proof scope is only its
  temporary workspace and its workspace-local fixture `CLAUDE.md`; it does not
  use `--bare`, `--tools`, `--allowedTools`, `--settings`, or a bypass mode.

All adapters use argv arrays without shell evaluation, preserve project and
user instruction/rule files, and require a native final JSON acknowledgement
plus an observed session identity. Codex event parsing reuses
[`codex_events.py`](../codex-lifecycle/codex_events.py); process identities,
short state locks, fencing, and identity-checked process-group cleanup reuse
[`bridge_core.py`](../opencode-lifecycle/bridge_core.py). The controller
reports goal, task, and memory tool observations separately and only from an
explicit successful/failed event in the trace; one matching tool does not prove
all three capabilities, and absent categories stay `unknown`.

Claude Code also documents a separate native background route (`claude --bg`,
`claude agents --json`, exact-ID `logs`/`stop`/`respawn`/`rm`) and native
session-scoped goals/tasks/schedules. That route is not conflated with the
external print/stream-json controller: the documented CLI shapes do not provide
the proof's per-request run/task/turn/fence acknowledgement binding, and
`respawn` is not treated as exact continuation if saved history is absent.
Native capabilities remain documented-unverified; this proof does not opt task
tools in with `--tools` or `--allowedTools` and does not infer execution from
their availability.

See [`adapter-interface.md`](adapter-interface.md) for the common contract,
typed native-operation evidence, conformance edge scopes, and discovery/scaffold
commands. The conformance report prepares review pointers for the canonical
capability projection; it never edits `harnesses.json` or automatically promotes
an observation.

## Operate and reconcile

```bash
.venv-docs/bin/python scripts/background_agent.py status --state-dir <RUN_DIR>
.venv-docs/bin/python scripts/background_agent.py turn --state-dir <RUN_DIR> \
  --command "Inspect the existing task state and make one bounded update."
.venv-docs/bin/python scripts/background_agent.py pause --state-dir <RUN_DIR>
.venv-docs/bin/python scripts/background_agent.py resume --state-dir <RUN_DIR> \
  --session-id <EXACT_CHILD_SESSION_ID> \
  --command "Reconcile any partial work before continuing the durable task."
.venv-docs/bin/python scripts/background_agent.py diagnose --state-dir <RUN_DIR>
.venv-docs/bin/python scripts/background_agent.py recover --state-dir <RUN_DIR> \
  --session-id <EXACT_CHILD_SESSION_ID> \
  --command "Re-read task, workspace, and session history; reconcile uncertain work."
.venv-docs/bin/python scripts/background_agent.py stop --state-dir <RUN_DIR>
```

`turn` requires an explicit command. Without recurrence, the service waits for
manual requests (including caller-delivered turns from a separately verified
native scheduler); it does not create native schedules. `pause` fences queued work and,
when a child CLI process is active, terminates only its recorded process group
after a Linux PID/start-time/boot-ID identity check. `resume` continues the
exact stored child session and always adds a reconciliation instruction.
`recover` refuses a live or identity-unknown controller/child/group, an
occupied per-run owner lock, uncertain workspace ownership, and terminal runs.
It advances the fence and runs an
explicit reconciliation turn rather than replaying an uncertain command. A
successful CLI exit without a matching native acknowledgement is recorded as
uncertain and requires reconciliation.

Status contains no provider transcript or task command. `agent_reported_complete`
is the child’s report, not independent validation. The adapter controls only
the harness CLI process: it does not start or inspect an OpenForage worker and
does not claim a worker checkpoint or worker stop from CLI exit. Those
application-level outcomes remain `unknown`. A terminal `stop` never
reactivates or recovers.

## Offline regressions

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python scripts/background_agent.py selfcheck
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python scripts/background_adapters/cli.py selfcheck
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/background-agent/conformance.py --selfcheck
```

These checks use local inert subprocesses and synthetic event fixtures only.
The runtime selfcheck covers both argv profiles and JSON
event projections, malformed/ambiguous IDs and missing acknowledgements,
relative-path and symlink guards, incomplete PID/starttime/boot/group identity
refusals, start/start and start/recover races across different state directories,
recurrence due/paused/stopped/block windows, duplicate controller and request
races, fences, active pause/stop cleanup, exact-session continuation,
stale-controller recovery, terminal behavior, and strict process cleanup. The
adapter selfcheck covers all three argv/version/help/event interfaces, exact
Claude session IDs, typed operation outcomes, and fail-loud scaffolding. The
conformance selfcheck reports each lifecycle edge separately, including
external-controller versus native-product scope. None invokes OpenCode, Codex,
Claude Code, a model, the network, or OpenForage.

## Native proof (run after the parent session exits)

The driver creates a unique ignored scratch workspace with its own nearest Git
root, an inert durable document task, and exclusive output/raw-log paths. Its
native calls run sequentially; do not run two proof drivers at once. Each mode
requires a caller-selected model and selector:

```bash
.venv-docs/bin/python examples/background-agent/proof.py \
  --harness opencode --model <provider/model> --variant <variant>

.venv-docs/bin/python examples/background-agent/proof.py \
  --harness codex --model <model> --reasoning-effort <effort> \
  --binary /path/to/chosen/codex

.venv-docs/bin/python examples/background-agent/proof.py \
  --harness claude-code --model <model> --effort <effort> \
  --binary /path/to/chosen/claude --output .tmp/<new-claude-proof>.json

.venv-docs/bin/python examples/background-agent/conformance.py \
  --proof .tmp/<new-claude-proof>.json
```

The proof driver uses the explicit isolated profile and, when run, requires a
fresh native parent to exit before the child starts; its source is not an
observation. It checks that at least two external due-time
continuations run on the exact child after the start launcher exits, that
bounded paused/stopped windows prevent later dispatch, and that authorized
recurrence returns only after reconciliation. It also resumes the exact child
for marker/history continuation, writes phase-specific readiness markers before
bounded local delays, requires a live recorded CLI plus matching session trace
before active pause/stop, checks duplicate-owner exclusion, and recovers an idle
controller under a new fence.
When event traces omit the shell command state, the proof labels that limitation
rather than claiming it. Workspace cleanup and all owned process-group cleanup
are evidence gates. A compact source-file digest manifest identifies the dirty
candidate tested, including every adapter/controller helper and the canonical
capability artifacts read by the projection. The v2 proof carries request IDs,
turn IDs, fences, task digests, exact-session fingerprints, acknowledgement
outcomes, and typed native-operation observations. Its conformance report gives
each lifecycle edge an individual result and distinguishes external-controller
from native-product scope. Failures are kept in the output and never upgraded by later
steps. Raw JSONL remains only under ignored `.tmp`; the sanitized proof JSON
includes hashes, event/tool observations, scoped lifecycle results, and explicit
unknowns, not provider transcripts or host paths.

This bounded proof uses an external interval timer, not a native scheduler. It
does not test reboot/hangup survival, prove every user's plugin configuration,
or infer OpenForage worker checkpoint/stop semantics. Goal declaration, task
creation, task-list writes, durable reread, memory write/reread, native schedule
creation, and actual scheduler delivery are separate typed operations. Evidence
requires explicit event identity and outcome; missing operations remain
`unknown`, and external due-time delivery is not native scheduler evidence. No OpenForage registration, search,
evaluation, submission, or worker operation is part of the proof.

The observed 2026-09-23 OpenCode and Codex proof records, including their
requested model selectors, tested source hashes, lifecycle results, and
limitations, are in the [native proof archive](native-contribution-wave-20260924/README.md).
Codex `0.156.1` was selected explicitly after an earlier ambiguous-binary
prelaunch failure. The OpenCode run requested `openrouter/openai/gpt-6-luna`
with variant `max`; Codex requested `gpt-6-astra` with reasoning `medium`.
Those selectors are requests, not independent proof of backend-served models,
and the models differ. The successful native adapter proofs are scoped to the
isolated fixture profile; normal runs continue to inherit native harness
configuration.

Read-only research-next proposals, separate-session background delivery, and
versioned finished-contribution evaluation are distinct routes. The evaluation
results and held-out scope are summarized in the [contribution trial archive](../fresh-agent-evals/trials/native-contribution-wave-20260924/README.md).
