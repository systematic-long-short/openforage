# OpenCode Lifecycle Example (external bridge)

A minimal, Linux-scoped external supervisor that demonstrates the required
background-agent lifecycle with the installed OpenCode CLI. It is an example
bridge — **never** a native scheduler, native OpenCode feature, or OpenForage
library feature. The bridge owns its state files and resumes the same child
OpenCode session with `opencode run --format json --session <id>` on later
turns.

## Scope and honesty

- The demonstrated sequence is **sequential**: the fixture parent CLI run
  finishes, the bridge records the parent session, and only then does the
  child CLI start. It does **not** prove surviving forced simultaneous parent
  death, terminal hangup, host reboot, or process-group kill. Every
  `observe` output declares this narrower scope.
- The child must be a **separate CLI invocation of the same harness** with its
  own session identity. The bridge refuses a child that reuses the parent
  session id, refuses `--continue`, and records the parent-exit timestamp
  before registering a child.
- The queued action is a **benign marker fixture** (`fixture/marker_task.md`):
  append one line and write an acknowledgement. No registration, download,
  evaluation, submission, wallet access, or network call is performed.
- OpenCode has no documented scheduler and no documented per-child
  pause/resume/stop control. This bridge is an **external controller**; it is
  not evidence of native OpenCode lifecycle support, and a future harness
  verification entry requires the evidence standard in
  [`docs/agents/harness-verification.md`](../../docs/agents/harness-verification.md).
- Unverified items are recorded as unverified, never faked: session identity
  persistence beyond the recorded run, native task/memory semantics beyond
  the recorded re-read evidence, and every survival case listed above.

## Modules

| File | Purpose |
|---|---|
| `bridge_core.py` | The compact engine: state, short-lock claim/finalize, process identity (pid + Linux starttime + boot id + pgid), bounded timeout handling, effect validation, recovery. |
| `opencode_bridge.py` | CLI over the engine (`init`, `register-*`, `queue`, `schedule`, `pause`, `resume`, `stop`, `diagnose`, `recover`, `status`, `observe`, `selfcheck`). |
| `bridge_selfcheck.py` | Deterministic inert-subprocess regressions, including prompt pause/stop, timeout, crash, stale-active recovery, duplicate dispatch, and marker-effect validation. |
| `run_fixture.py` | The executable orchestrator fixture: fresh parent, distinct fresh child, detached controller, native session-memory proof, sanitized observations. |

## Locking and cancellation model

- The run lock is held only for claim/finalize mutations. A due event is
  claimed and a fencing token advanced under a short lock; the lock is
  released while the child runs, so `pause` and `stop` are answered promptly.
- The active dispatch records the controller identity and, once spawned, the
  child's process identity. `os.kill(pid, 0)` alone is never treated as
  identity: the Linux `starttime` and boot id must also match.
- `pause`/`stop` block further dispatch, fence the active claim, and terminate
  only the recorded child process group after an identity check. A child that
  finishes later cannot resurrect paused/stopped state.
- A dispatch is `acknowledged` only when the exit code is zero, the expected
  CLI effect line is present, the acknowledgement matches
  run+event+session+fence+task-hash, and any expected marker file exists.
  Unknown acknowledgements, failed exits, timeouts, and marker mismatches stay
  failed/unverified.
- `recover` proves the recorded owner is dead (identity comparison) and
  auto-clears the stale claim before re-queuing under a new fence. Users are
  never told to delete a lease by hand.

Statuses: `initialized → parent_registered → parent_exited →
child_registered ⇄ paused → stopped`. Terminal `stopped` never reactivates;
`stop` cancels queued events and refuses later `queue`.

## Bridge selfcheck (run now, no agent sessions)

```bash
.venv-docs/bin/python examples/opencode-lifecycle/opencode_bridge.py selfcheck
```

This runs the full protocol with **inert fixture processes only** and prints
how many checks behaved as expected. It covers duplicate init refusal,
child-before-parent-exit refusal, parent-session reuse refusal, duplicate
event refusal, real due-time dispatch with marker-effect acknowledgement,
prompt pause and stop while a child is running, timeout termination and
failure persistence, crash classification, proof-based recovery, stale-active
classification, single-tick claiming under two competing schedulers, and the
observation scope declaration. It uses temporary directories and never
touches a wallet, data directory, or the network.

## Orchestrator fixture (run after this session exits)

The runner launches each OpenCode process sequentially and keeps at most one
OpenCode agent alive at a time. The parent and child bootstrap runs are fresh
(no resume flag); only the established child is resumed by the detached
controllers. The caller-selected model and variant are passed to every
`opencode run` argv and recorded.

```bash
.venv-docs/bin/python examples/opencode-lifecycle/run_fixture.py \
  --model <provider/model> --variant <label> \
  --output .tmp/lifecycle-observations.json
```

The runner:

1. launches a fresh parent OpenCode process, captures the actual session id
   from the native JSON stream and its exit code, and records the parent exit;
2. launches a distinct fresh child process whose first turn carries a one-time
   memory marker that is never written to the workspace and asks for a native
   task/todo item when the harness exposes one; task-tool events are recorded
   separately from session-memory recall and stay `unknown` when absent;
3. queues event 1 and runs the detached controller, which resumes only the
   established child session and requires the durable task re-read, the
   acknowledgement, the marker effect, and the effect line;
4. real pause: queues event 2, runs a bounded scheduler while paused, and
   requires zero dispatches; resumes the exact child and consumes event 2 in
   the same child session with a second detached controller;
5. real stop: queues event 3, stops, executes a bounded scheduler past the
   event's due time, and requires no later child dispatch, no marker, and a
   cancelled event;
6. runs the inert lifecycle regressions as inert subprocess controls, labeled
   separately from the actual OpenCode evidence (inert controls never prove
   native crash or pause behavior);
7. writes **sanitized** observations. Raw event streams stay only in
   git-ignored private scratch (`--state-root/<run-id>/raw/`); the public
   record keeps normalized tool observations and plain-text tails with
   credential-shaped strings and private paths removed. Every owned process
   is terminated with identity checks and the run fails if any survives.

Exit is zero only when all evidence gates pass: both real events acknowledged,
the memory marker recalled from session history and absent from the workspace,
the paused and stopped schedulers reporting zero dispatches, the post-stop
marker absent, the inert regression passing, and cleanup complete with no
owned process remaining. Failures list precise reasons.

`--inert` substitutes a generated inert OpenCode stand-in so the whole runner
can be regression-tested without an agent or credentials. Inert observations
are labeled `evidence_class=inert-regression-only` and
`actual_native_proof=false`; never cite them as native OpenCode proof.

Notes for a truthful run:

- Keep the parent and child prompts tiny. The parent run must exit before the
  child bootstrap starts; the runner enforces the sequence.
- The bridge's `schedule --executor` receives the full command prefix and the
  prompt is appended by the engine, so pass the executor last:
  `--executor opencode run --format json --dir <ws> --session <child> --model <m> --variant <v>`.
- If a capability cannot be observed (for example survival across reboot),
  keep it in the `unverified` list. Do not convert it into a pass.
- The bridge never installs a service, changes OS settings, or uses broad
  process matching. Remove `.tmp/opencode-lifecycle*/` to clean up.
