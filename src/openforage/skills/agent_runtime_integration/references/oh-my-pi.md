# Harness Routing: Oh My Pi

Status: **documented-unverified** — the upstream `can1357/oh-my-pi` repository README describes the surfaces below, reviewed 2026-09-22, but no capability has been observed on an installed product and no adapter is registered. Registry entry: `references/harnesses.json`. Route: `references/oh-my-pi.md`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Documented surfaces

The upstream README documents four entry points over the same engine: interactive TUI (`omp`), one-shot (`omp -p` / `--print`), RPC over stdio (`omp --mode rpc`), and ACP (`omp acp`). It also documents an ephemeral `--no-session` mode that does not save a session. Source: <https://github.com/can1357/oh-my-pi>.

## Distinct session / conversation identity

Upstream documents saved on-disk sessions and an ephemeral `--no-session` mode, and the RPC/ACP entries hand the session to another program over stdio. That is session plumbing, not a documented spawn contract for a long-running child with recorded identity and ownership. The in-session `task` and `hub` tools supervise subagents; subagents are not separate CLI processes and do not satisfy the contract.

## Scheduler evidence

Not documented in the reviewed upstream README. There is no documented native scheduler surface for ticks, so a tick must come from a verified external scheduler until an adapter exists.

## Lifecycle evidence

Not documented in the reviewed upstream README. `hub` can message live agents, wait on or cancel background jobs, and supervise long-running processes, but the reviewed source does not document a separate-process pause/resume/stop contract with identity evidence.

## Documented examples

These examples come only from the upstream README:

```bash
omp
omp -p "Summarize this codebase"
omp --mode rpc
omp acp
```

The ephemeral `--no-session` flag is part of the documented upstream session surface.

## What is not documented (report, do not guess)

- A scheduler or tick primitive.
- A spawn/pause/resume/stop contract for a supervised separate Oh My Pi child.
- That `hub`, `task`, or `--no-session` produce a separate agent that owns a campaign. They do not establish ownership evidence.

## Safe next step

Treat this page as routing information, not as an adapter. Record `documented-unverified` in the capability report and complete the verification procedure in `docs/agents/harness-verification.md` before delegating. See also [unsupported.md](unsupported.md) for the discovery procedure.
