# Harness Verification

Before an onboarding agent delegates to a background research agent, it must
verify that the installed harness can actually host one. This page defines the
required capabilities, the evidence standard, and the canonical scoped record
projection. See [Background Agent](/docs/agents/background-agent.md).

No harness is fully verified in this packet. Codex and OpenCode have bounded
Linux observations for their CLI identity/continuation and for an external
background controller using an evidence-specific isolated profile. The actual
scope is listed in the generated projection below. The controller is not a
native scheduler; its process lifecycle does not establish OpenForage worker
checkpoint/stop behavior, global memory, host reboot or terminal-hangup
survival, or compatibility with every plugin/configuration. Overall registry
status remains `documented-unverified`. Documentation and scoped observations
are routing evidence, not a certified adapter contract or launch authorization.
Commands must not be guessed, copied from another product, or inferred by
analogy. Discovering commands through local `--help` or official documentation
is allowed for research; label documented routes accurately.

## Required capabilities

| Capability | Why it is required | Evidence to record |
|---|---|---|
| Distinct session/conversation identity | The background research agent must not share the onboarding session identity. | A newly created identity observed and recorded, with how it was created. |
| Separate CLI invocation of the same harness | The child must be the same product started as a new process. | An observed second process with its own identity. |
| Parent independence | The child keeps its identity and ownership when the parent exits. | The child observed running after the parent stopped. |
| Native goal / task / memory primitives | Durable outcome, task records, and campaign notes across turns. | Separate typed observations for goal declaration, task creation, durable reread, memory write/reread; each needs an explicit native tool/event identity, outcome, and scope. |
| Shell execution | The child supervises `openforage start`/`stop` and reads events. | An observed command and its output. |
| Workspace read/write | The child writes the campaign manifest, registry, capability report, and checkpoints. | Observed file creation and content. |
| Native schedule or verified external scheduler | Tick execution for `run_due_callbacks()` and child consumption of the tick. A plan record is never a scheduler. | The scheduler primitive or bridge, and an observed tick consumed by the child. For background-agent continuations, also record the actual delivery owner and due-time child acknowledgement. |
| Pause / resume / stop controls | Lifecycle control without duplicate ownership. | Observed pause, resume to the exact target with transferred ownership, and stop. |
| Crash recovery | A killed child is detected and reconciled before restart. | Observed detection, reconciliation, and safe restart or stop. |
| Duplicate-start control | A second child for the same campaign or data directory is refused or coordinated. | The observed refusal or coordination result. |
| Exact target control | Lifecycle commands act on the intended campaign and data directory, never a stale or sibling target. | Observed command target and affected process. |
| Terminal triggers stopped | On stop, completion, or exhaustion, recurring triggers no longer fire. | An observed tick window with no further child activity. |

## Evidence standard

- **Verified** means observed on the installed product and version, not assumed from documentation.
- Record the product, version, date, observer, the instruction used, the scope, and the observed result.
- Scope every capability to the surfaces where it was observed: product,
  version, interface (CLI, desktop, cloud, API), operating system, and
  lifecycle edge (spawn, pause, resume, stop, crash recovery).
- A capability that is only described in marketing material, or only present in a different product, is **not verified**.
- If any required capability cannot be verified, the product is **unresolved** for that capability and must not host a background research agent yet.
- Preserve the exact product identity. Do not substitute one product for another: Grok Build is not Grok Bot, and an ambiguous product name stays ambiguous until a primary source identifies it.

## Standard conformance fixture

Verify against bounded, benign marker tasks only — for example echo a
marker, write a marker file, and exit. Never register a wallet, start
mining, submit signals, or run unbounded work during verification.

The fixture exercises, in order:

1. start a fresh separate CLI process and record the parent and child
   identities;
2. confirm the child identity differs from the parent;
3. validate a real structured ownership acknowledgement bound to request ID,
   task digest, turn ID, fence, and child session (stdout prose alone is not an ack);
4. stop the parent and confirm the child keeps its identity and ownership;
5. separately create and re-read each native goal/task/memory primitive;
6. fire a scheduler wakeup and confirm the child consumes exactly that tick; for
   the background-agent adapter's external interval mode, observe at least two
   bounded due-time continuations after the launcher exits;
7. pause the child, confirm triggers stop, resume to the exact preserved
   target, and confirm ownership transfer and fencing;
8. stop the child, confirm the stop is terminal, and confirm triggers no
   longer fire in a following tick window (also confirm a paused due window
   dispatches nothing);
9. kill the child process, confirm crash detection, registry reconciliation,
   and safe restart or stop;
10. attempt a duplicate start and record the refusal or coordination result;
11. record the target each lifecycle command acted on.

The reusable proof driver emits a versioned conformance report with these
external-controller edges reported separately: fresh CLI/distinct session,
exact-child continuation, real ownership acknowledgement, parent independence,
pause/drain/reconcile, controller crash recovery, terminal stop/no relaunch,
duplicate-owner exclusion, and owned-process cleanup. It also reports external
due-time delivery separately from the native scheduler-delivery operation. Each
edge includes its mechanism plus `external_controller_status` and
`native_product_status`; process-group control by the external shim never
becomes a native pause/stop observation. The report prepares typed review
pointers to the canonical `project_capability_records` projection and never
edits or promotes records automatically. Inert fixtures are labeled synthetic
and are not public observations.

Native-operation evidence requires an event with a real tool/event identity and
explicit outcome. A goal declaration is not task creation; task creation is not
a durable reread; scheduler creation is not a delivered wakeup. A planning or
todo record, feature flag, help option, final prose, process exit, or external
due-time event cannot substitute for the corresponding native operation.

If the harness has no native scheduler, a verified external bridge hosted
with the **same harness** may provide the tick. Record the real mechanism;
never describe a bridge as a native primitive.

The earlier Codex candidate fixture in
[`examples/codex-lifecycle/`](../../examples/codex-lifecycle/README.md) is
narrower than full conformance. It records fresh parent/child CLI processes,
distinct thread IDs, exact-ID history resumes, explicit native goal/tool events
when visible, and a bounded due dispatch by an **external** finite driver. It
requires the parent to exit before the child starts. Its paused/stopped windows
only verify that the external driver makes zero later invocations; they do not
pause or kill active Codex work. The fixture does not test parent-kill, hangup,
reboot, native scheduling, global memory, OpenForage callbacks, search, or
submission. The later isolated-profile background-controller proof is a
separate observed run; it is represented by its own per-capability records and
does not retroactively change this earlier fixture.

The background-agent controller offers an explicit `--interval-seconds` mode
with one bounded continuation command. This is an external timer inside the
detached controller, not a native scheduler or research engine. The completed
Codex and OpenCode proofs recorded two exact-child due acknowledgements,
controller pause/reconciliation/recovery/stop edges, and cleanup. The proof
sources alone are not observations; the retained proof artifacts and the
generated projection are. If a verified native scheduler already delivers
controller `turn` requests, use manual mode rather than adding a second
scheduling owner. See the [adapter instructions](../../examples/background-agent/README.md).

Claude Code's official CLI/headless sources document the separate print route
(`--print --output-format stream-json --verbose`, caller-chosen `--session-id`,
and exact `--resume`) as well as the native `claude --bg` / `claude agents
--json` route. `--bg` conflicts with print mode, so these are separate
interfaces. The native route remains documented-unverified here: its docs do
not supply the proof's per-request run/task/turn/fence acknowledgement binding,
and `respawn` is not equated with exact-child continuation when saved history is
absent. The reviewed installed CLI showed Claude Code `2.1.281`; help/version
probing is not lifecycle or native-operation verification. See the
[Claude Code route](../../src/openforage/skills/agent_runtime_integration/references/claude-code.md)
and [shared adapter interface](../../examples/background-agent/adapter-interface.md).
The retained 2026-09-24 isolated-profile proof attempt inherited native
permissions: a `Write` denial blocked the marker operation, and the strict
acknowledgement was missing despite CLI exit 0. The proof failed and added no
verified capability; no permission bypass or retry was attempted. Honor the
installed policy and keep a denied operation blocked. See the
[retained failed proof](../../examples/background-agent/contributor-reliability-wave-20260924/background-claude-code-1.json).

## Capability report

The child writes `.openforage-research/capabilities.json` (by convention)
with one entry per capability: status (`verified` / `unverified` /
`unresolved`), scope, evidence reference, and the adapter identifier when one
exists. The parent reads the report and must not assume a capability the
report does not claim.

## Harness routing registry (this packet)

The routing registry at [`src/openforage/skills/agent_runtime_integration/references/harnesses.json`](/src/openforage/skills/agent_runtime_integration/references/harnesses.json) records, for each harness, its documented surfaces and honest status:

- `documented-unverified` — official documentation describes the surface, but the overall adapter entry is not fully verified. Scoped per-capability observations may still be listed, as in the OpenCode entry;
- `discovery-only` — the primary source itself could not be verified, so no product facts are recorded;
- `unresolved` — the name or semantics are ambiguous, or a required capability is absent from the source.

The registry routes to per-harness pages under [`references/`](/src/openforage/skills/agent_runtime_integration/references/): `claude-code.md`, `codex.md`, `opencode.md`, `hermes.md`, `oh-my-pi.md`, `pi.md`, `openclaw.md`, `muse.md`, `grok-bot.md`, and the shared [`unsupported.md`](/src/openforage/skills/agent_runtime_integration/references/unsupported.md) discovery procedure.

Each registry entry stores observed and unknown details in one
`capability_records` array. A record is always scoped to its own mechanism,
version, OS, profile, probe, test id, evidence artifact, and lifecycle edge.
Legacy observations whose source did not record a profile keep that dimension
unrecorded; they are not silently matched to the new isolated-profile runs.

## Canonical scoped capability projection

The table is generated from `capability_records` by
`scripts/onboarding/capabilities.py`. Rows group records only when their
product, state, mechanism, version, surface, OS, profile, probe, test id, and
artifact match exactly; each capability's lifecycle remains listed separately.
The startup packet calls the same projection API. Registry validation checks
the proof assertions; `python scripts/start_here.py --check` verifies that this
rendered view is current.

<!-- BEGIN GENERATED SCOPED CAPABILITY PROJECTION -->

| Product | State | Mechanism | Version / surface / OS / profile | Capability records (individual lifecycle) | Probe / test id | Evidence |
|---|---|---|---|---|---|---|
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex CLI exec/resume with detached external background-agent controller / linux / isolated | `parent-independence` — parent exited before child start; detached controller remained after launcher return and acknowledged child ownership | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex CLI exec/resume with detached external background-agent controller / linux / isolated | `parent-independence` — parent exited before child start; detached controller remained after launcher return and acknowledged child ownership | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex detached external background-agent controller due-time delivery / linux / isolated | `terminal-triggers-stopped` — terminal stop remained stopped through the bounded following due window | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex detached external background-agent controller due-time delivery / linux / isolated | `terminal-triggers-stopped` — terminal stop remained stopped through the bounded following due window | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex detached external background-agent controller recovery / linux / isolated | `crash-recovery` — stale external controller was replaced with a new generation/fence without overlapping the exact child | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / Codex detached external background-agent controller recovery / linux / isolated | `crash-recovery` — stale external controller was replaced with a new generation/fence without overlapping the exact child | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller canonical-workspace owner guard for Codex / linux / isolated | `duplicate-start-control` — second owner of the canonical workspace was refused before child launch; original owner remained unchanged | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller delivering Codex CLI continuations / linux / isolated | `scheduler-wakeup-and-consumption` — two external due-time child continuations acknowledged; paused/stopped windows suppressed later dispatch | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller delivering Codex CLI continuations / linux / isolated | `scheduler-wakeup-and-consumption` — two external due-time child continuations acknowledged; paused/stopped windows suppressed later dispatch | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller process-group pause; Codex remains a separately managed CLI child / linux / isolated | `pause` — active controller pause terminated the bounded turn process group; paused due window dispatched nothing; application-worker checkpoint unknown | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller process-group pause; Codex remains a separately managed CLI child / linux / isolated | `pause` — active controller pause terminated the bounded turn process group; paused due window dispatched nothing; application-worker checkpoint unknown | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller process-group stop; Codex remains a separately managed CLI child / linux / isolated | `stop` — active controller stop reached terminal state; stopped due window dispatched nothing; application-worker stop unknown | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller process-group stop; Codex remains a separately managed CLI child / linux / isolated | `stop` — active controller stop reached terminal state; stopped due window dispatched nothing; application-worker stop unknown | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller targeting exact Codex child thread / linux / isolated | `exact-target-control` — mismatched target refused and continuation kept exact recorded child identity | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | external-background-controller | codex-cli 0.156.1 / detached external background-agent controller targeting exact Codex child thread / linux / isolated | `exact-target-control` — mismatched target refused and continuation kept exact recorded child identity | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | external-finite-driver | codex-cli 0.156.1 / Finite external driver plus Codex CLI resume / linux / not-recorded | `scheduler-wakeup-and-consumption` — one due event dispatched and consumed; no native schedule claim | codex-native-marker-20260923 / codex-native-marker-20260923 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec JSONL / linux / not-recorded | `distinct-session-identity` — fresh parent and child bootstrap processes in a bounded sequential fixture | codex-native-marker-20260923 / codex-native-marker-20260923 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec resume / linux / not-recorded | `resume` — three exact-thread history resumes; no active-process control | codex-native-marker-20260923 / codex-native-marker-20260923 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec resume to a recorded child thread under external controller delivery / linux / isolated | `resume` — two external due-time continuations and history continuation target the exact child; no native scheduler claim | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec resume to a recorded child thread under external controller delivery / linux / isolated | `resume` — two external due-time continuations and history continuation target the exact child; no native scheduler claim | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec/resume JSONL / linux / not-recorded | `separate-cli-invocation` — bounded sequential marker fixture; parent exited before child | codex-native-marker-20260923 / codex-native-marker-20260923 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec/resume with detached external background-agent controller / linux / isolated | `distinct-session-identity` — fresh child CLI identity distinct from the parent; later turns retain the child's exact identity<br>`separate-cli-invocation` — fresh parent exited before distinct child bootstrap; launcher returned after ownership acknowledgement | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | observed | harness-cli | codex-cli 0.156.1 / Codex CLI exec/resume with detached external background-agent controller / linux / isolated | `distinct-session-identity` — fresh child CLI identity distinct from the parent; later turns retain the child's exact identity<br>`separate-cli-invocation` — fresh parent exited before distinct child bootstrap; launcher returned after ownership acknowledgement | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | unknown | native-scheduler | codex-cli 0.156.1 / Codex CLI scheduling / linux / isolated | `native-scheduler` — native scheduler unknown; due-time delivery belonged to the external controller | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | unknown | native-scheduler | codex-cli 0.156.1 / Codex CLI scheduling / linux / isolated | `native-scheduler` — native scheduler unknown; due-time delivery belonged to the external controller | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | unknown | native-tool | codex-cli 0.156.1 / Codex native task operation events / linux / isolated | `native-task-store` — no correlated durable task reread operation recorded; task creation is a separate outcome | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | unknown | native-tool | codex-cli 0.156.1 / Codex native tool events / linux / isolated | `native-goal-operation` — no goal operation recorded<br>`native-memory-operation` — no memory operation recorded; thread history is not global memory<br>`native-task-tool-operation` — no successful typed task-list-write event recorded; task creation and durable reread remain unknown | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | unknown | native-tool | codex-cli 0.156.1 / Codex native tool events / linux / isolated | `native-goal-operation` — no goal operation recorded<br>`native-memory-operation` — no memory operation recorded; thread history is not global memory<br>`native-task-tool-operation` — no task tool operation recorded | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| Codex | unknown | not-recorded | not-recorded / not-recorded / not-recorded / not-recorded | `native-goal-task-memory` — not-recorded<br>`pause` — not-recorded<br>`stop` — not-recorded | not-recorded / not-recorded | not recorded |
| Codex | unknown | openforage-worker | codex-cli 0.156.1 / Codex proof scope: OpenForage worker state / linux / isolated | `openforage-worker-checkpoint` — worker not started by adapter; checkpoint state unknown<br>`openforage-worker-stop` — worker not started by adapter; stop state unknown | bounded-native-background-agent-proof / background-proof-107b6cda4ce14eccba25fabc9fbe7668 | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-codex-1.json) |
| Codex | unknown | openforage-worker | codex-cli 0.156.1 / Codex proof scope: OpenForage worker state / linux / isolated | `openforage-worker-checkpoint` — worker not started by adapter; checkpoint state unknown<br>`openforage-worker-stop` — worker not started by adapter; stop state unknown | bounded-native-background-agent-proof / background-proof-c81444655561472aa20ed857260f92ce | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode CLI run/session with detached external background-agent controller / linux / isolated | `parent-independence` — parent exited before child start; detached controller remained after launcher return and acknowledged child ownership | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode CLI run/session with detached external background-agent controller / linux / isolated | `parent-independence` — parent exited before child start; detached controller remained after launcher return and acknowledged child ownership | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode detached external background-agent controller due-time delivery / linux / isolated | `terminal-triggers-stopped` — terminal stop remained stopped through the bounded following due window | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode detached external background-agent controller due-time delivery / linux / isolated | `terminal-triggers-stopped` — terminal stop remained stopped through the bounded following due window | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode detached external background-agent controller recovery / linux / isolated | `crash-recovery` — stale external controller was replaced with a new generation/fence without overlapping the exact child | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / OpenCode detached external background-agent controller recovery / linux / isolated | `crash-recovery` — stale external controller was replaced with a new generation/fence without overlapping the exact child | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller canonical-workspace owner guard for OpenCode / linux / isolated | `duplicate-start-control` — second owner of the canonical workspace was refused before child launch; original owner remained unchanged | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller delivering OpenCode CLI continuations / linux / isolated | `scheduler-wakeup-and-consumption` — two external due-time child continuations acknowledged; paused/stopped windows suppressed later dispatch | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller delivering OpenCode CLI continuations / linux / isolated | `scheduler-wakeup-and-consumption` — two external due-time child continuations acknowledged; paused/stopped windows suppressed later dispatch | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller process-group pause; OpenCode remains a separately managed CLI child / linux / isolated | `pause` — active controller pause terminated the bounded turn process group; paused due window dispatched nothing; application-worker checkpoint unknown | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller process-group pause; OpenCode remains a separately managed CLI child / linux / isolated | `pause` — active controller pause terminated the bounded turn process group; paused due window dispatched nothing; application-worker checkpoint unknown | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller process-group stop; OpenCode remains a separately managed CLI child / linux / isolated | `stop` — active controller stop reached terminal state; stopped due window dispatched nothing; application-worker stop unknown | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller process-group stop; OpenCode remains a separately managed CLI child / linux / isolated | `stop` — active controller stop reached terminal state; stopped due window dispatched nothing; application-worker stop unknown | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller targeting exact OpenCode child session / linux / isolated | `exact-target-control` — mismatched target refused and continuation kept exact recorded child identity | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | external-background-controller | 1.18.31 / detached external background-agent controller targeting exact OpenCode child session / linux / isolated | `exact-target-control` — mismatched target refused and continuation kept exact recorded child identity | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | external-bridge | 1.18.31 / CLI (opencode run) + external bridge (examples/opencode-lifecycle) / linux / not-recorded | `exact-target-control` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`parent-independence` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`pause` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`resume` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`scheduler-wakeup-and-consumption` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`stop` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`terminal-triggers-stopped` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`workspace-read-write` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop | opencode-native-marker-20260922 / fixture-20260922T173829Z-242424 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/opencode-native-marker-20260922.json) |
| OpenCode | observed | harness-cli | 1.18.31 / CLI (opencode run) + external bridge (examples/opencode-lifecycle) / linux / not-recorded | `distinct-session-identity` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop<br>`separate-cli-invocation` — bounded sequential marker fixture: fresh child, queued dispatch, pause, resume, stop | opencode-native-marker-20260922 / fixture-20260922T173829Z-242424 | [`JSON`](../../src/openforage/skills/agent_runtime_integration/references/evidence/opencode-native-marker-20260922.json) |
| OpenCode | observed | harness-cli | 1.18.31 / OpenCode CLI run --session to a recorded child session under external controller delivery / linux / isolated | `resume` — two external due-time continuations and history continuation target the exact child; no native scheduler claim | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | harness-cli | 1.18.31 / OpenCode CLI run --session to a recorded child session under external controller delivery / linux / isolated | `resume` — two external due-time continuations and history continuation target the exact child; no native scheduler claim | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | harness-cli | 1.18.31 / OpenCode CLI run/session with detached external background-agent controller / linux / isolated | `distinct-session-identity` — fresh child CLI identity distinct from the parent; later turns retain the child's exact identity<br>`separate-cli-invocation` — fresh parent exited before distinct child bootstrap; launcher returned after ownership acknowledgement | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | observed | harness-cli | 1.18.31 / OpenCode CLI run/session with detached external background-agent controller / linux / isolated | `distinct-session-identity` — fresh child CLI identity distinct from the parent; later turns retain the child's exact identity<br>`separate-cli-invocation` — fresh parent exited before distinct child bootstrap; launcher returned after ownership acknowledgement | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | observed | native-tool | 1.18.31 / OpenCode native todowrite tool event / linux / isolated | `native-task-tool-operation` — successful todowrite events recorded in child turns; does not establish native goals or global memory | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | unknown | native-scheduler | 1.18.31 / OpenCode CLI scheduling / linux / isolated | `native-scheduler` — native scheduler unknown; due-time delivery belonged to the external controller | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | unknown | native-scheduler | 1.18.31 / OpenCode CLI scheduling / linux / isolated | `native-scheduler` — native scheduler unknown; due-time delivery belonged to the external controller | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | unknown | native-tool | 1.18.31 / OpenCode native task operation events / linux / isolated | `native-task-store` — no correlated durable task reread operation recorded; task creation is a separate outcome | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | unknown | native-tool | 1.18.31 / OpenCode native tool events / linux / isolated | `native-goal-operation` — no goal operation recorded<br>`native-memory-operation` — no memory operation recorded; thread history is not global memory<br>`native-task-tool-operation` — no successful typed task-list-write event recorded; task creation and durable reread remain unknown | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | unknown | native-tool | 1.18.31 / OpenCode native tool events / linux / isolated | `native-goal-operation` — no goal operation recorded<br>`native-memory-operation` — no global memory operation recorded; thread history is not global memory | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
| OpenCode | unknown | not-recorded | not-recorded / not-recorded / not-recorded / not-recorded | `crash-recovery` — not-recorded<br>`duplicate-start-control` — not-recorded<br>`native-goal-task-memory` — not-recorded<br>`shell-execution` — not-recorded | not-recorded / not-recorded | not recorded |
| OpenCode | unknown | openforage-worker | 1.18.31 / OpenCode proof scope: OpenForage worker state / linux / isolated | `openforage-worker-checkpoint` — worker not started by adapter; checkpoint state unknown<br>`openforage-worker-stop` — worker not started by adapter; stop state unknown | bounded-native-background-agent-proof / background-proof-7d11dbf185bb47e0b97e13265ba3612c | [`JSON`](../../examples/background-agent/contributor-reliability-wave-20260924/background-opencode-2.json) |
| OpenCode | unknown | openforage-worker | 1.18.31 / OpenCode proof scope: OpenForage worker state / linux / isolated | `openforage-worker-checkpoint` — worker not started by adapter; checkpoint state unknown<br>`openforage-worker-stop` — worker not started by adapter; stop state unknown | bounded-native-background-agent-proof / background-proof-ad1a6d07359d4573bf5ab25628d184fe | [`JSON`](../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json) |
<!-- END GENERATED SCOPED CAPABILITY PROJECTION -->

## Verified adapter registry (evidence-gated)

A future entry may claim `verified` for a specific capability only when it
carries all of:

- an evidence artifact **inside the repository** (a probe log, transcript, or
  fixture output that contains no secrets, credentials, or private paths);
  absolute paths and `..` traversal outside the repo are rejected;
- a test id and the date the evidence was recorded; the date must be a real
  calendar date, not merely date-shaped;
- the product version and a typed scope object with `surface`, `os`, and
  `lifecycle` strings — a bare string does not scope anything;
- the exact probe instruction used and an observed **passing** result. A
  result that records a failure does not count as verified.

A `verified` entry that aggregates several capabilities must not mix
unrelated product versions, surfaces, or operating systems; each capability
keeps its own lifecycle edge. Partial capability evidence may be recorded
honestly with a non-`verified` entry status, and the entry becomes fully
`verified` only when every required capability carries evidence.

The registry validator is static: it checks consistency, presence, types,
dates, and in-repo artifact existence. It can never confirm that a probe
actually ran or that an observation is true.

The canonical record shape in
[`references/harnesses.json`](/src/openforage/skills/agent_runtime_integration/references/harnesses.json):

```json
{
  "record_id": "<stable-record-id>",
  "capability": "pause",
  "status": "verified",
  "mechanism": "external-background-controller",
  "evidence_artifact": "src/openforage/skills/agent_runtime_integration/references/evidence/<product>-pause.json",
  "test_id": "<fixture-run-id>",
  "tested_at": "2026-01-01",
  "product_version": "1.2.3",
  "probe": "<bounded-probe-id>",
  "scope": {
    "surface": "CLI or controller surface",
    "os": "linux",
    "profile": "isolated",
    "lifecycle": "pause edge and its bounded result",
    "evidence_scope": "<exact retained proof scope>"
  },
  "assertions": [
    { "path": "proof.field", "equals": true }
  ],
  "observed_result": "<what the bounded marker fixture showed>"
}
```

Unknown records use `status: "unverified"` and a `note`, with source scope and
assertions when an artifact explicitly records the unknown. Projection API:
`onboarding.capabilities.project_capability_records(root, entry,
selectors=None) -> list[dict]`. Optional selectors match exactly and
case-sensitively on `capability`, `mechanism`, `product_version`, `probe`,
`test_id`, or `scope.surface|os|profile|lifecycle`. The returned machine records
include `record_id`, state/availability, mechanism, product version, full scope,
probe, test id/date, artifact, result or unknown reason, and assertions. The
function validates the complete harness record set before applying selectors
and returns one output record per input record; it never pools scopes.
New registry records carry an explicit `record_id`. For migrated historical
records without one, the projection emits a deterministic content-derived
`legacy-…` id; missing historical profile/probe/mechanism details remain
`not-recorded` rather than being inferred.

### Versioned evidence-contract extension point

`harnesses.json` may declare an `evidence_contracts` array on a product entry.
Each declaration names a registered `contract_id`, `evidence_class`, integer
`schema_version`, exact `product_identity`, and exact `adapter_identity` (the
proof's `harness` value). The validator selects strict proof checks from the
artifact's class/schema and the matching product-specific declaration, never
from its repository path. Moving an artifact into a future archive therefore
does not weaken validation. Unrecognized classes, schemas, products, or
contract ids are rejected rather than promoted by generic passing text.

The reusable background-agent proof uses schema 2 with explicit product
contracts for OpenCode, Codex, and Claude Code. A record identified as part of
the versioned background-proof family must retain both a recognized
`evidence_class` and integer `schema_version`; removing either or both never
falls back to generic legacy assertions. Only the two explicitly identified
retained legacy native-observation sources keep their legacy route. Schema 2
adds duplicate-owner exclusion and typed native-operation outcomes without
promoting any new registry capability until a parent review attaches an actual
passing proof artifact and required assertions.

For a future adapter beyond the current OpenCode, Codex, and Claude routes, add
a distinct product-specific contract to `PROOF_CONTRACTS` in
`scripts/onboarding/capabilities.py`, including only capabilities that its
proof schema can verify; add the exact matching declaration to that product's
`harnesses.json` entry; and register its mechanism, lifecycle, and required
positive assertions only where supported by that proof schema. Add
focused regressions for the artifact schema, a relocated copy, and malformed
nested shapes. Do not inherit another product's identity or capabilities by
default. The current native-task-tool proof is limited to a recorded successful
tool operation (`todowrite`); it does not establish native goals, durable task
storage, global memory, or a scheduler.

A verified entry for one capability does not verify any other capability, and
it does not authorize publishing spawn, pause, resume, stop, or probe
commands for unrelated capabilities. Re-verify after a harness version
change. The registry validator rejects a claimed verified capability that is
missing any required evidence field or whose evidence artifact does not
exist.

## Ambiguous products remain unresolved

Some products leave the relevant semantics ambiguous. Examples of ambiguity that block delegation:

- whether resuming a session creates a new identity or reuses the original one;
- whether a subagent or delegated task shares the parent session identity;
- whether a scheduled tick survives process exit or host reboot;
- whether a "background" mode is a new process or a detached thread of the same session.

If any of these cannot be resolved from observed behavior, record the product as unresolved and do not delegate. The OpenForage contract prefers a stalled handoff over a guessed command.

## Verification procedure

1. Enumerate the required capabilities from the table above.
2. Probe only with documented, unambiguous primitives. Do not construct commands by analogy.
3. Use the standard conformance fixture: bounded benign marker tasks only.
4. Record every probe and result; label unobserved capabilities `unverified`.
5. If a required capability is unverified or the product's semantics are ambiguous, mark that capability `unresolved`.
6. Register a capability as verified only after it is observed under the evidence standard; publish commands only from the registry.
7. Re-verify after a harness version change.

## What verification is not

- It is not a substitute for the OpenForage bootstrap; registration, authentication, and the depth-6 rule still apply to any child.
- It is not permission to bypass closed-beta invite codes or to share a `wallet.key`.
- It is not a scheduler. A verified scheduler capability still needs the tick wired to `openforage.run_due_callbacks()`; a plan record never fires it.
- It is not a security sandbox. A verified harness capability says nothing about the trust level of external text, generated code, or third-party dependencies — see [Research Security](research-security.md).

## Related pages

- [Background Agent](/docs/agents/background-agent.md)
- [Research Campaign](/docs/agents/research-campaign.md)
- [Research Security](/docs/agents/research-security.md)
- [Diagnostics](/docs/agents/diagnostics.md)
- `src/openforage/skills/agent_runtime_integration/SKILL.md`
- [`references/harnesses.json`](/src/openforage/skills/agent_runtime_integration/references/harnesses.json) and the routed pages in [`references/`](/src/openforage/skills/agent_runtime_integration/references/)
