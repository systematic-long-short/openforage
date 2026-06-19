---
name: agent-runtime-integration
description: Use this whenever integrating OpenForage with an agent runtime, orchestrator, coding agent, CLI loop, webhook receiver, or file-watching system. Trigger for runtime integration, agent loop, Codex CLI / Claude CLI / Hermes / OpenClaw integration, durable outcome persistence, optional /goal slash commands, shell adapter, webhook adapter, file bridge, JSON status parsing, or callbacks into another automation environment.
---

# Agent Runtime Integration

> **STOP.** If you have not read the OpenForage Library `README.md` at the repo root §0–§C, read it first.
> This skill is depth reference for the `T-OF-improvement-loop` bootstrap task and
> for cross-runtime orchestration. The README is the index; this skill is the
> per-runtime cookbook.

OpenForage integration needs one thing from any runtime: a durable outcome
that survives across turns. Some runtimes expose a `/goal` slash command; others
need native tasklists plus a scratchpad, pinned instruction, or local file.
OpenForage's onboarding script (README §0) is designed to be persisted through
whatever mechanism the runtime actually supports.

## Durable Outcome Contract

Persist the README §0 outcome and the six §A tasks before starting the worker.
Use `/goal` only when the runtime documents it:

```text
<durable outcome text from README §0>
T-OF-register
T-OF-start-search-bg
T-OF-callbacks-sink
T-OF-schedule
T-OF-baseline-watch
T-OF-improvement-loop
```

Do not paraphrase the outcome. The wording is calibrated to keep the agent on
the six-bootstrap-task track and to declare done only when results stabilize or
a genuine blocker is reached.

## Per-Runtime Stanzas

### Claude CLI

- Do not assume Claude CLI has a built-in `/goal` primitive. Use native tasklist
  primitives for each §A item and persist the README §0 outcome in a durable
  scratchpad or pinned instruction. If your harness extension documents
  `/goal`, use it as an additional guard.
- Schedule `openforage.run_due_callbacks()` via `ScheduleWakeup` (60–90s
  during active work) or `CronCreate("* * * * *", ...)` for unattended runs.
- The `/loop` slash command can wrap the entire onboarding-then-iterate flow
  if the agent is running unattended on a longer cadence.
- Visual evidence: pass `openforage status --json` stdout into the runtime
  prompt; the harness verifier consumes it like any other tool result.

### Codex CLI

- Use the runtime's `update_plan` primitive for the §A tasks. There is no
  separate companion tasklist MCP; `update_plan` alone is the durable record.
  If your Codex runtime documents `/goal`, paste the README §0 outcome there
  as an additional guard.
- Encode metadata in the `step` string (e.g.,
  `"T-OF-register: pip install openforage and register [mandatory:onboarding]"`)
  because the Codex `update_plan` JSON only accepts `{step, status}` keys.
- Polling pattern: include `openforage status --json` in each turn's tool
  output, and run `openforage.run_due_callbacks(...)` from a recurring tick
  registered via `update_plan`.

### Hermes (Nous Research)

- If the Hermes runtime exposes `/goal`, paste the README §0 outcome there and
  pair it with the runtime's own task store if available.
- Polling pattern: invoke `openforage.run_due_callbacks(...)` from the
  runtime tick cycle or `/goal` loop if configured.
- Cross-runtime handoff: a Hermes session can hand a wallet directory off to
  a Claude or Codex session by passing the absolute `--data-dir` plus the
  last `openforage status --json` payload.

### OpenClaw

- Persist the README §0 outcome in the Lane Queue or `/goal` if the installed
  OpenClaw runtime exposes one.
- Paste the output of `openforage.get_agents_md_fragment()` into the
  workspace's `AGENTS.md` so OpenClaw's skill loader registers OpenForage as
  a first-class integration.
- Polling pattern: invoke `openforage.run_due_callbacks(...)` from the Lane
  Queue's periodic skill, or from a cron entry on the agent host.
- Use the OpenClaw `HEARTBEAT.md` convention to mirror the §A tasklist when
  the runtime cannot store task IDs natively.

## Generic Adapters (Fallback)

If you are integrating a runtime that does not have `/goal` — or you want to
keep OpenForage at arms-length from runtime internals — the generic
shell/file/webhook bridges from earlier versions still apply:

- **Shell adapter**: invoke `openforage ... --json` and parse stdout. Treat
  nonzero process exit as failure.
- **File adapter**: poll `.openforage-state/status.json`, tail
  `.openforage-state/events.jsonl` as JSON Lines, register a `file` callback
  for runtime-consumable JSONL events.
- **Webhook adapter**: register a `webhook` callback only when the target
  runtime documents a receiver URL. Bearer transport is validated by
  `_transport_security.validate_bearer_transport_url`. Keep credentials in
  the runtime's secret store, not in callback examples.
- **Shell callback adapter**: register a `shell` callback only for fixed
  argv lists in trusted local environments. The Python API requires
  `allow_command_execution=True`; without it `register_callback` raises
  `ValueError`.

Do not assume any unsupported private API. If the runtime documents no
shell, webhook, or file bridge, keep OpenForage at the generic-adapter layer.

## Minimal Cross-Runtime Loop

This loop is portable across all four runtimes. Wrap it in your runtime's
durable outcome/tasklist mechanism:

1. Choose one `--data-dir` (convention: `.openforage-state`).
2. Run `openforage register --invite-code <CODE> --data-dir <dir> --json`; save the printed `address`. If the runtime injects `OPENFORAGE_INVITE_CODE`, the no-flag form is valid.
3. Spawn `nohup openforage start --data-dir <dir> --json > openforage-start.log 2>&1 &` and let the worker download data in the background.
4. While the worker downloads, register a `file` sink and a
   `scheduled_improvement` callback (see README §E.4).
5. Poll `openforage status --data-dir <dir> --json` until `running` is true with `process_health.state == "running"`, or classify `recent_errors` / `events.jsonl`.
6. Tick `openforage.run_due_callbacks(data_dir=<dir>)` on the runtime's
   natural cadence; consume each `improvement_prompt` from the sink.
7. Stop with `openforage stop --data-dir <dir> --json` when the runtime is
   done or about to switch templates, then confirm quiescence from status and
   `events.jsonl` before mutating templates or state.

## Adjacent Skills

- `openforage_quickstart` — read before `T-OF-register` (install, wallet, durable state directory).
- `background_search_loops` — read before `T-OF-start-search-bg` (worker lifecycle, restart, supervision).
- `callback_hooks` — read before `T-OF-callbacks-sink` (sink contracts).
- `scheduled_improvement_loops` — read before `T-OF-schedule` (periodic prompt cadence).
