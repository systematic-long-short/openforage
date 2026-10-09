---
name: background-search-loops
description: Use this whenever an agent needs to run, supervise, resume, stop, or diagnose OpenForage background search loops. Trigger for background worker, daemon-style search, openforage start/status/stop, heartbeat, events.jsonl, worker lifecycle, stale pid, or long-running search supervision.
---

# Background Search Loops

> **Campaign path.** If your task is a campaign bootstrap and you have not
> read the OpenForage Library `README.md` §0, §A, and §A.1, read them first;
> documentation, template, and validation contributions do not need those
> sections.
> This skill is depth reference for the `T-OF-start-search-bg` bootstrap task. The
> README documents the durable outcome/tasklist setup, the §A.1 background research
> agent delegation contract, and the spawn-and-keep-working
> pattern (`nohup openforage start ... &`) that lets data download in the background
> while the agent wires up callbacks and schedules in parallel.

Prefer the explicit CLI worker for unattended runs. Keep the state directory stable across start, status, callbacks, and stop commands.

## Native Controls And Ownership

The worker is owned by a **background research agent** created per the
README §A.1 delegation contract: a separate CLI invocation of the same
harness with a distinct session/conversation identity. A Python worker,
a same-session subagent, a main-session heartbeat, and a detached resume
of the main session do not qualify.

Use the owning runtime's native goals, task stores, schedules, memory,
delegation, and background controls first: spawn, observe, pause, resume,
and stop. Exactly one live child may own a `--data-dir` or campaign ID; the
campaign takes an atomic ownership claim or equivalent verified lock with a
fencing token advanced on every transfer. Before starting anything, check
the child registry (`.openforage-research/agents.json` by convention) and
`openforage status --json`; if either shows a live owner, do not start a
second child. The parent must not restart or mutate the worker while the
child is live.

The child writes a capability report
(`.openforage-research/capabilities.json`) covering scheduling ticks,
shell execution, workspace access, parent reporting, and pause/resume/
stop. The parent must not assume a capability the report does not claim.
Harness spawn/pause/resume/stop commands come only from an evidence-gated
per-capability entry in the verified adapter registry — see
`docs/agents/harness-verification.md`. Discovery via local `--help` or
official docs is allowed for research but must be labeled unverified and
verified before unattended use.

Campaign metadata (campaign ID, checkpoint ledger, coverage ledger,
negative findings) lives with the campaign, not in the OpenForage data
directory, and never in `.openforage/settings.yaml`.

Plan records such as Codex `update_plan` are never schedulers: register
the status poll and `run_due_callbacks()` tick with a native scheduler or
an external scheduler, not with a plan step.

## Start And Monitor

```bash
openforage start --data-dir .openforage-state --algorithm random_weighted --json
openforage status --data-dir .openforage-state --json
```

Use the JSON status payload to decide what to do next. Key fields include:

- `running`
- `pid`
- `process_health.state`
- `process_health.heartbeat_age_seconds`
- `data_download_progress.percent`
- `data_download_progress.phase`
- `signals_found`
- `signals_submitted`
- `recent_errors`
- `paths.event_jsonl_file`

## Event Log Workflow

Read the append-only event log when status is unclear.

```bash
tail -n 20 .openforage-state/events.jsonl
```

Each line is a JSON object. Look for `start`, `heartbeat`, `register`, `search_start`, `worker_error`, `stop`, and `worker_exit` events.

## Stop And Recover

Stop before changing algorithms or deleting state.

```bash
openforage stop --data-dir .openforage-state --json
```

`openforage stop --json` sends `SIGTERM` and updates `background.pid` /
`status.json`; it does not guarantee that in-flight submissions drained. If
`status --json` reports `process_health.state` as `stale`, inspect
`background.pid`, `status.json`, `worker.log`, and recent `events.jsonl`
entries. Start again only after confirming no live worker still owns the same
state directory.

Pause is a stop plus disabled or drained triggers plus a checkpoint and
transition record in the campaign manifest plus a `paused` report from the
owning child, with the previous state preserved as `resume_target`. Resume
re-invokes a child — the original session identity or a fresh one — that is
a separate CLI invocation of the same harness with its own session identity
and is not the parent; ownership must be transferred under the verified
claim/lock with an advanced fencing token, and the registry, status, events,
and checkpoints must be reconciled before the worker restarts. Resume
restores only the recorded `resume_target`. A dead child process alone is
not a resume. Only the owning child performs pause, resume, or stop; the
parent must not do so while another child owns the data directory.

Campaign states and legal edges are authoritative in
[`docs/agents/references/campaign-state-machine.json`](/docs/agents/references/campaign-state-machine.json).
`stopped`, `completed`, and `exhausted` are distinct terminal outcomes and
never silently reactivate. When a submission acknowledgment is ambiguous,
record it as ambiguous and do not blindly resubmit; reconcile through the
submission/status surface first. The library provides no exactly-once
guarantee for submissions, transitions, or worker launches — never claim
one.

Execution limits are campaign records, not settings: record CPU, RAM,
storage, wall time, worker/concurrency count, and completed-simulation
limits with requested vs enforced values, the enforcement mechanism,
periodic observation, and stop/kill behavior. Run a preflight calibration;
a limit with no enforcement mechanism is unenforced, not unlimited. A
plateau triggers research or pivot before any terminal stop, and unavailable
infrastructure enters `needs-input` with bounded retries. See
[Research Campaign — Execution resources](/docs/agents/research-campaign.md#execution-resources).

## Runtime Loop Pattern

1. Start once.
2. Poll `status --json` at a reasonable interval.
3. Append observations to the agent runtime's own log.
4. Emit or handle callbacks for failures.
5. Stop cleanly when the runtime is done.

Do not run multiple workers against the same `--data-dir`. Use separate data
directories for separate runtimes, venvs, platforms, or OpenForage versions
unless the owning runtime explicitly coordinates process ownership.

## Analytics For Diagnosis

The Python API exposes six analytics helpers for interpreting an ongoing or completed search. Use them when status JSON and `events.jsonl` alone do not explain why discovery has slowed.

- `get_search_stats()` — per-function and per-feature yield rates.
- `get_search_patterns()` — expression-tree depth distribution and family combinations.
- `get_search_velocity()` — signal discovery rate over time.
- `get_yield_analysis()` — diminishing-yield detection; declining yield is a signal to switch strategies.
- `get_suggestions()` — recommended expression directions to try next.
- `get_correlation_patterns()` — function pairs that co-occur in passing evaluations.

## Templates

`openforage.templates.random_weighted` is the default starter template. It begins with random expression generation and adapts sampling weights based on observed yield rates.

Custom templates are callables that receive a `SearchContext` exposing `vocabulary`, `analytics`, `evaluate(graph)`, and `refresh_analytics()`. Expression graphs are dicts of the form:

```python
{"type": "rotation", "node": "func_id",
 "children": [{"type": "feature", "node": "feat_id", "children": []}]}
```

Switch to a custom template when discovery rate is declining and you want to bias sampling toward specific function families based on analytics.

Whichever template you use, agents MUST NOT generate, evaluate, or submit candidates with compute-graph depth greater than 6, regardless of the era's `max_depth`. The library will not enforce this cap; the agent must — see `README.md` §G Hard Rules.

## Forking `random_weighted`

Use a small, bounded workflow when an agent needs to fork the default template.

1. Discover bundled skills with `openforage.list_skills()`, then read or open the relevant bundle `SKILL.md` skill guidance before changing template behavior.
2. Run `openforage.templates.random_weighted` as the baseline in both cold and warm local starts. Record what worked, what failed, yield, passing candidates, and the generated distribution or behavior.
3. Inspect analytics before editing: `openforage.templates.analyze_random_weighted`, `get_search_stats()`, `get_search_patterns()`, `get_search_velocity()`, `get_yield_analysis()`, `get_suggestions()`, or `get_correlation_patterns()`.
4. Copy or wrap `openforage.templates.random_weighted` into a forked custom template. Keep the original baseline path available for comparison. Your fork MUST reject any candidate with `expr.depth() > 6`, regardless of the era's `max_depth` — see `README.md` §G Hard Rules.
5. Run a bounded local validation loop over fixed iterations or candidate counts. Compare baseline cold/warm distribution and behavior against the fork before using the custom template in a longer background search.

## Adjacent Skills

- `openforage_quickstart` — read before `T-OF-register` (install, wallet, durable state directory).
- `callback_hooks` — read before `T-OF-callbacks-sink` (sink contracts so the worker has somewhere to deliver events).
- `scheduled_improvement_loops` — read before `T-OF-schedule` (periodic prompt cadence on top of a running worker; native schedules, never plan records).
- `agent_runtime_integration` — read before `T-OF-improvement-loop` (how the runtime polls and consumes prompts; background research agent delegation contract).
- `search_settings` — read before changing worker or search settings (campaign metadata never goes in `.openforage/settings.yaml`).
- `search_research` — read before a research campaign (frozen reference, checkpoint accounting, matched budgets and seeds).
