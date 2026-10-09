# Harness Routing: OpenCode

Status: **documented-unverified (partial scoped evidence)** — OpenCode `1.18.31` has an earlier Linux marker run and a separate completed Linux proof using the isolated profile of the external background controller. The exact per-capability records are shown in the generated [Harness Verification projection](/docs/agents/harness-verification.md#canonical-scoped-capability-projection). Overall adapter status remains `documented-unverified`: native scheduling and native goal/memory operations remain unobserved; the new `todowrite` observation is limited to that tool event, and controller lifecycle does not establish OpenForage worker checkpoint/stop behavior. Registry entry: `references/harnesses.json`. Earlier evidence: [`evidence/opencode-native-marker-20260922.json`](evidence/opencode-native-marker-20260922.json) and its [summary](evidence/opencode-native-marker-20260922.md).

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Documented surfaces

- `opencode run` runs non-interactively and accepts `--continue` (continue the last session), `--session <id>` (continue a specific session ID), and `--fork` (fork the session when continuing). Docs: <https://opencode.ai/docs/cli/>.
- `opencode serve` starts a headless HTTP server with API access to OpenCode functionality.
- `opencode session list` lists sessions (table or JSON) and `opencode session delete <sessionID>` deletes one.
- `opencode export [sessionID]` and `opencode import <file>` move session data.
- `opencode acp` starts an Agent Client Protocol server over stdin/stdout.

## Distinct session / conversation identity

Documented: sessions have IDs. `--session <id>` continues a specific session and `--fork` forks it; `opencode session list --format json` enumerates them. `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS` exists but is labeled experimental and is a background subagent task, not a separate CLI process with its own session identity; it must not be treated as satisfying the background research agent contract.

Observed (scoped): the earlier marker fixture and the separate isolated-profile controller proof recorded fresh parent/child OpenCode sessions, parent exit before child start, and later continuation to the exact recorded child session. These are version- and profile-scoped records, not a general contract.

## Scheduler evidence

Not documented. `opencode serve` is a server surface, not a scheduler. The earlier external example bridge consumed one queued due marker; the later isolated-profile background-controller proof recorded two acknowledged external due-time turns to the same exact child. Both delivery mechanisms were external, never a native OpenCode scheduler. A live callback still needs an authorized, verified delivery route wired to `openforage.run_due_callbacks()`.

## Lifecycle evidence

Documented: `opencode session delete <sessionID>` removes a session. No per-child pause/resume/stop control for a separate long-running CLI process is documented in the reviewed CLI reference.

Observed (scoped): the earlier bridge paused a queued marker dispatch, resumed the exact recorded child session, and stopped terminal. The later controller proof separately observed bounded controller process-group pause/stop, reconciliation, stale-controller recovery, exact-target refusal, terminal stop, and cleanup. These are external-controller lifecycle edges for the recorded isolated fixture; they do not establish native OpenCode lifecycle controls or OpenForage worker checkpoint/stop behavior.

## Example bridge (partial evidence, still not a native adapter)

The public packet ships an external bridge example at
[`examples/opencode-lifecycle/`](../../../../../examples/opencode-lifecycle/)
that sequences a fresh parent CLI run, a fresh separate child CLI run, a
queued event with a real due time, and `opencode run --session <id>`
continuation. It is an **example external controller**, not a native OpenCode
scheduler. Its `selfcheck` runs deterministically with inert fixture
processes; the actual marker run recorded on 2026-09-22 is retained only as
scoped observation evidence in
[`evidence/opencode-native-marker-20260922.json`](evidence/opencode-native-marker-20260922.json).
Native spawn, crash recovery, duplicate-start refusal, native
goal/task/memory primitives, and shell execution remain unverified.

## Completed scoped controller proof

The broader Linux-scoped process controller in
[`examples/background-agent/`](../../../../../examples/background-agent/README.md)
adds an explicit-command CLI for a durable task, a fresh OpenCode child,
acknowledged run/session/task-hash ownership, exact `--session` continuation,
status, process-group pause/stop, fenced reconciliation after interruption, and
an opt-in `--interval-seconds` external due-time mode with an explicit bounded
continuation command. Normal launches retain caller configuration and tools;
only the proof's isolated profile selects `--pure`. It preserves applicable
project instructions and does not use `--continue`. The completed proof is
retained at
[`examples/background-agent/native-contribution-wave-20260924/opencode-proof.json`](../../../../../examples/background-agent/native-contribution-wave-20260924/opencode-proof.json).
It observed parent/child identity and independence, exact-child continuation,
two external due-time turns, controller pause/stop/reconciliation/recovery,
and cleanup. The service remains external request/timer plumbing, not an
OpenCode scheduler; its process controls do not establish application-worker
checkpoint/stop semantics. The run used Linux and the proof's `isolated`
profile only; it does not establish host reboot or terminal-hangup survival,
every user plugin/configuration, or live OpenForage work. The canonical record
projection above is the capability summary; do not combine these records with
the earlier bridge's different mechanism/profile.

## Documented examples

These examples come only from the official CLI reference:

```bash
opencode run "Explain how closures work in JavaScript"
opencode serve
opencode run --attach http://localhost:4096 "Explain async/await in JavaScript"
opencode session list --format json
opencode session delete <sessionID>
```

The `--session`, `--fork`, and `--continue` flags are documented in the same reference.

## What is not documented (report, do not guess)

- A scheduler inside the CLI or server.
- A native OpenCode scheduler or native spawn/pause/resume/stop contract for a supervised child. The new process-group lifecycle observations belong to the external controller, not native OpenCode controls.
- That the experimental background-subagent mode counts as a separate agent. It does not.

## Safe next step

Treat this page as routing information plus scoped observation evidence, not as a verified adapter or operational launch instruction. Native scheduler and OpenForage worker-state gaps remain open. See the generated scope summary in `docs/agents/harness-verification.md` and [unsupported.md](unsupported.md) for the discovery procedure.
