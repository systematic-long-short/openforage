# Harness Routing: OpenClaw

Status: **documented-unverified** — official OpenClaw documentation describes the controls below, reviewed 2026-09-22, but no capability has been observed on an installed product and no adapter is registered. Registry entry: `references/harnesses.json`. Route: `references/openclaw.md`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Documented surfaces

- `openclaw automations` (registered as `openclaw cron`) manages automation jobs for the Gateway scheduler. Payload kinds: `--system-event`, `--message`, `--command` / `--command-argv`, and `--script`.
- Schedules: `--at` (one-shot), `--every` (interval), `--cron` (five- or six-field), `--on-exit`, and `--stream-command`.
- Session styles: `main`, `isolated`, `current`, and `session:<id>`.
- Docs: <https://docs.openclaw.ai/automation/cron-jobs/payloads> and <https://github.com/openclaw/openclaw/blob/main/docs/cli/cron.md>.

## Distinct session / conversation identity

Documented and explicit: `--session isolated` "creates a fresh transcript and session id for each run" in a dedicated `cron:<jobId>`, and isolated runs reset ambient conversation context. `current` binds to the active session at creation time, and `session:<id>` pins an explicit persistent session key. This is the strongest isolated-session evidence among the documented harnesses, but it is still documentation, not installed-product verification.

## Scheduler evidence

Documented: the Gateway scheduler supports cron expressions, intervals, one-shot runs, exit/stream triggers, retry backoff (30s, 1m, 5m, 15m, 60m), and run history in the shared SQLite state database. `openclaw automations run <job-id> --wait` can block until a queued run reaches a terminal status. A plan record is not a scheduler.

## Lifecycle evidence

Documented: `openclaw automations enable` / `disable`, `remove` (which stops future runs and cleans up the isolated session, reporting `sessionCleanup: "pending"` while deferred), and manual `run`. Failure incidents alert once per signature, remind on a cooldown, and resolve on a successful run.

## Documented examples

These examples come only from the two official pages:

```bash
openclaw automations create "0 7 * * *" \
  "Summarize overnight updates." \
  --name "Morning brief" \
  --agent ops
```

```bash
openclaw automations create "*/15 * * * *" \
  --name "Queue depth probe" \
  --command "scripts/check-queue.sh" \
  --command-cwd "/srv/app" \
  --announce \
  --channel telegram \
  --to "-1001234567890"
```

```bash
openclaw automations create "0 7 * * *" \
  "Summarize overnight updates." \
  --name "Lightweight morning brief" \
  --session isolated \
  --light-context \
  --no-deliver
```

```bash
openclaw cron list
openclaw automations enable <job-id>
openclaw automations disable <job-id>
openclaw automations run <job-id> --wait --wait-timeout 10m
```

## What is not documented (report, do not guess)

- A named "child agent" object distinct from an isolated session. The isolated run is the documented unit of separation.
- That a scheduled isolated job can own and supervise an `openforage start` worker on the host. Command payloads run in the Gateway process, and agent-turn payloads run in an isolated session; the exec host and working directory must be verified.
- That the Gateway's `openclaw cron` alias and the docs path `docs/cli/cron.md` are the same surface as `openclaw automations`; the docs state they are aliases, and that is all that is recorded.

## Safe next step

Treat this page as routing information, not as an adapter. Record `documented-unverified` in the capability report and complete the verification procedure in `docs/agents/harness-verification.md` before delegating. See also [unsupported.md](unsupported.md) for the discovery procedure.
