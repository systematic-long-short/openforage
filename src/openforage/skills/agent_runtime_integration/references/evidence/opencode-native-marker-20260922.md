# OpenCode native marker observation (2026-09-22)

Sanitized summary of one actual OpenCode marker lifecycle run. Machine-readable
record: [`opencode-native-marker-20260922.json`](opencode-native-marker-20260922.json).
This is an observation, not a measured result and not an OpenCode adapter.

## What was observed

- **Product/version/model/variant:** OpenCode `1.18.31` on Linux, model
  `openrouter/deepseek/deepseek-v4.1-flash`, variant `max` passed in the
  actual run argv.
- **Run id / date:** `fixture-20260922T173829Z-242424`, UTC `2026-09-22`.
- **Distinct sessions and separate CLI invocations:** a fresh parent
  `opencode run --format json` process exited `0` before a distinct fresh
  child process started; parent session
  `ses_f35cd6001ffejoRHS8G1NrOQ1S` differs from child session
  `ses_f35cd4f73ffecQ16IvU2M0tcW8`. No `--continue`, no session reuse.
- **Memory recall:** the child's two acknowledgements reported the one-time
  marker that existed only in its first prompt/session history; nothing was
  written to the workspace. This is session-history evidence only. The
  recorded observations report native task/todo events as unknown; raw-stream
  inspection found one actual `todowrite` creation (a task item titled
  `lifecycle-fixture`, status pending, in the child bootstrap turn), which is
  recorded as a raw-stream note and **not** treated as reconciled native
  task evidence.
- **Two acknowledged callbacks to the same child:** `evt-001` (fence 2) and
  `evt-002` (fence 4) were queued by the external bridge, dispatched by
  `opencode run --session <child>`, and acknowledged with matching run id,
  event id, session id, fence, task-file hash, and action result.
- **Paused zero dispatch:** while paused, `evt-001` stayed queued and the
  bounded scheduler tick dispatched `0`.
- **Resume with fencing:** resume continued the exact child session id with
  fence 4 and acknowledged `evt-002`; no new identity was created.
- **Terminal stop:** the stopped tick dispatched `0`; the third queued event
  `evt-003` was cancelled, no marker appeared, and the dispatch count did not
  change in the bounded post-stop window.
- **Cleanup:** no owned processes remained after the run (`exit_code 0` for
  every invoked process).

## Scope limits

This run is **sequential parent-exit-before-child-continuation only**. It does
not claim survival of a forced simultaneous parent death, terminal hangup,
host reboot, or process-group kill. The bridge scheduler is **external**
(`examples/opencode-lifecycle/`), not a native OpenCode scheduler. Pause,
resume, and stop are scoped to the bridge's queued marker dispatch, not to
arbitrary active native OpenCode work. Crash recovery, duplicate-start
control, shell execution, workspace read/write, and native goal/task/memory
primitives remain unproved for OpenCode — in particular, the single raw
`todowrite` event is not reconciled with the recorded unknown field, so no
native task/memory contract is claimed. Inert crash/pause/duplicate
regressions live in the separate `inert-subprocess-regression` block; they
never prove native behavior.

## Probe instruction (orchestrator-run, manual)

Raw event streams and host paths are not published. To reproduce an
equivalent bounded marker run, an orchestrator (not CI, and never with
credentials, a wallet, or a campaign) runs from the checkout root:

```bash
.venv-docs/bin/python examples/opencode-lifecycle/run_fixture.py \
  --model <provider/model> --variant <label> \
  --output .tmp/lifecycle-observations.json
```

The runner launches a fresh parent, a distinct fresh child, a bounded
external-controller schedule, pause, resume, and stop, then writes sanitized
observations. Raw OpenCode event streams stay in git-ignored scratch, so a
fresh probe produces its own raw material; the digests recorded here identify
the run that was observed, not a re-runnable artifact.
