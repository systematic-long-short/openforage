---
name: scheduled-improvement-loops
description: Use this whenever an agent wants OpenForage to periodically emit algorithm review or improvement prompts through callbacks. Trigger for scheduled improvement, algorithm improvement loop, periodic prompt, run_due_callbacks, improvement_prompt, search refinement cadence, or automated review loop.
---

# Scheduled Improvement Loops

> **STOP.** If you have not read `openforage_library/README.md` §0–§C, read it first.
> This skill is depth reference for the `T-OF-schedule` bootstrap task. The README
> §E.4e shows the recommended cadence for `run_due_callbacks()` per runtime
> (Claude CLI `ScheduleWakeup` / `CronCreate`, Codex CLI `update_plan` tick, Hermes
> runtime tick or `/goal` loop if configured, OpenClaw Lane Queue), and §F documents the `improvement_prompt`
> event schema sinks will receive.

Scheduled improvement callbacks create periodic `improvement_prompt` events. They do not run an optimizer by themselves; they emit prompts to the registered non-scheduled callbacks for an agent runtime to consume.

## Register Outputs First

Use a file callback as the default output sink.

```python
from openforage.callbacks import register_callback

register_callback(
    data_dir=".openforage-state",
    callback_type="file",
    path=".openforage-state/improvement-events.jsonl",
)
```

## Add A Scheduled Prompt

```python
from openforage.callbacks import register_callback

register_callback(
    data_dir=".openforage-state",
    callback_type="scheduled_improvement",
    interval_seconds=3600,
    prompt="Review current OpenForage status, recent_errors, algorithm_state, and events.jsonl. Suggest one conservative next algorithm adjustment.",
)
```

## Run Due Callbacks

Call this from the owning agent runtime's scheduler.

```python
from openforage.callbacks import run_due_callbacks

result = run_due_callbacks(data_dir=".openforage-state")
```

`result["events_emitted"]` is the number of due improvement prompts.
`result["callbacks_dispatched"]` is the number of non-scheduled callbacks that
received them. There is no hard registry max/rate limit: each due enabled
scheduled callback emits one prompt per `run_due_callbacks()` call, and dispatch
is synchronous/serial to all enabled non-scheduled sinks. Effective rate is set
by `interval_seconds` and the owning runtime's call cadence.

## Operating Rules

- Keep prompts specific and conservative.
- Include status, `recent_errors`, `algorithm_state`, and event-log review in the prompt.
- Avoid promises about earnings or signal discovery.
- Use one state directory for the worker, callback registry, and output events.
- Let the external runtime decide whether and how to act on the prompt.

If no file, webhook, or shell callback is registered, due scheduled prompts have nowhere useful to go.

## Review A `random_weighted` Fork

For a scheduled review of a forked template, make the prompt diagnose and compare instead of promising automatic tuning. Scheduled callbacks do not automatically optimize and do not run an optimizer; the owning runtime decides whether to edit or keep the fork.

```python
register_callback(
    data_dir=".openforage-state",
    callback_type="scheduled_improvement",
    interval_seconds=3600,
    prompt=(
        "Review the random_weighted fork. Inspect analytics such as "
        "openforage.templates.analyze_random_weighted, get_search_stats(), "
        "get_search_patterns(), and get_yield_analysis() to diagnose what "
        "worked, failed, changed yield, or produced passing candidates. "
        "Before recommending action, require a bounded validation loop and "
        "compare baseline versus fork cold/warm distribution and behavior. "
        "This scheduled_improvement improvement_prompt does not run an optimizer."
    ),
)
```

Checklist for the receiving agent:

- Open the bundle `SKILL.md` or skill guidance before changing the custom template.
- Use analytics diagnosis to explain what worked, failed, changed yield, or passed.
- Require bounded local validation before acting on the fork.
- Compare baseline vs fork behavior, including cold and warm distribution.
- State that scheduled callbacks do not automatically optimize.

## Adjacent Skills

- `callback_hooks` — read before `T-OF-callbacks-sink` (register at least one non-scheduled sink first).
- `agent_runtime_integration` — read before `T-OF-improvement-loop` (per-runtime durable outcome + tick patterns for `run_due_callbacks()`).
- `background_search_loops` — read before `T-OF-start-search-bg` (the worker must be running for analytics-driven prompts to be meaningful).
