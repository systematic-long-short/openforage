# Harness Routing: Hermes

Status: **documented-unverified** — the official Hermes cron documentation describes the controls below, reviewed 2026-09-22, but no capability has been observed on an installed product and no adapter is registered. Registry entry: `references/harnesses.json`. Route: `references/hermes.md`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Documented surfaces

- Cron jobs are managed through the `cronjob_manage` tool in chat, or through the CLI: `hermes cron create`, `edit`, `list`, `status`, `tick`, `runs` (`history`), and `doctor`. Docs: <https://hermes-agent.nousresearch.com/docs/user-guide/features/cron>.
- Jobs support one-shot or recurring schedules, attached skills, delivery targets, project `--workdir`, `--paused` creation, no-agent script mode, and event-triggered fires.
- The gateway daemon runs the scheduler: `hermes gateway install` installs it as a user or system service.

## Distinct session / conversation identity

Documented: the gateway "starts a fresh `AIAgent` session for each due job" and runs due jobs in isolated agent sessions. Each cron run has a unique task identity bound to its workdir. Cron-run sessions cannot recursively create more cron jobs, which bounds runaway scheduling.

## Scheduler evidence

Documented: the gateway ticks the scheduler every 60 seconds, checks `next_run_at`, starts a fresh session per due job, and records run metadata. `hermes cron status` reports ticker heartbeat and overdue runs, and `hermes cron doctor` reports per-job health. A plan record is not a scheduler.

## Lifecycle evidence

Documented: `hermes cron pause`, `resume`, `run`, and `remove`; a recurring job that came due while paused fires one catch-up run on resume; `--paused` creates a job disabled with a reason; `hermes pause` is a global emergency stop and `hermes resume` lifts it. Runs in flight are never killed by the global pause.

## Documented examples

These examples come only from the official cron page:

```text
/cron add "every 2h" "Check server status"
/cron pause <job_id>
/cron resume <job_id>
/cron remove <job_id>
```

```bash
hermes cron create "every 2h" "Check server status"
hermes cron pause <job_id_or_name>
hermes cron resume <job_id_or_name>
hermes cron status
hermes gateway install
```

## What is not documented (report, do not guess)

- A spawn/pause/resume/stop contract for a separate long-running Hermes CLI child that owns an OpenForage `--data-dir`. The cron scheduler is not by itself that adapter.
- That a cron delivery target or `origin` session is the same identity as the child's session.
- That `--workdir` grants access to the campaign directory; scoping must be verified.

## Safe next step

Treat this page as routing information, not as an adapter. Record `documented-unverified` in the capability report and complete the verification procedure in `docs/agents/harness-verification.md` before delegating. See also [unsupported.md](unsupported.md) for the discovery procedure.
