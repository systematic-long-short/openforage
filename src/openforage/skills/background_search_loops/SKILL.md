---
name: background-search-loops
description: Use this whenever an agent needs to run, supervise, resume, stop, or diagnose OpenForage background search loops. Trigger for background worker, daemon-style search, openforage start/status/stop, heartbeat, events.jsonl, worker lifecycle, stale pid, or long-running search supervision.
---

# Background Search Loops

> **STOP.** If you have not read the OpenForage Library `README.md` at the repo root §0–§C, read it first.
> This skill is depth reference for the `T-OF-start-search-bg` bootstrap task. The
> README documents the durable outcome/tasklist setup and the spawn-and-keep-working
> pattern (`nohup openforage start ... &`) that lets data download in the background
> while the agent wires up callbacks and schedules in parallel.

Prefer the explicit CLI worker for unattended runs. Keep the state directory stable across start, status, callbacks, and stop commands.

## Start And Monitor

```bash
openforage start --data-dir .openforage-state --json
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

`genetic` is the default starter template when `--algorithm` is omitted. `openforage.templates.random_weighted` remains an explicit baseline template for comparison runs and forks. It begins with random expression generation and adapts sampling weights based on observed yield rates.

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
- `scheduled_improvement_loops` — read before `T-OF-schedule` (periodic prompt cadence on top of a running worker).
- `agent_runtime_integration` — read before `T-OF-improvement-loop` (how the runtime polls and consumes prompts).
