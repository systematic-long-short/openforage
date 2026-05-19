---
name: callback-hooks
description: Use this whenever an agent needs to register, list, emit, remove, or design OpenForage callbacks and failure hooks. Trigger for callback hooks, failure notification, JSONL event sink, webhook integration, shell adapter, degraded submission, stalled download, broken algorithm, or callback audit.
---

# Callback Hooks

> **STOP.** If you have not read the OpenForage Library `README.md` at the repo root §0–§C, read it first.
> This skill is depth reference for the `T-OF-callbacks-sink` bootstrap task. The
> README §E.4 covers the four callback types (`file`, `webhook`, `shell`,
> `scheduled_improvement`), and §F documents the event payload schemas a sink will
> actually receive — read both before registering any callback.

Callbacks are opt-in and stored under the same durable state directory used by the worker. Start with file callbacks because they are local, inspectable, and require no credentials.

## CLI Workflow

Register a file callback that appends callback events to JSON Lines:

```bash
openforage callbacks register file --data-dir .openforage-state --path .openforage-state/callback-events.jsonl --json
```

List registered callbacks:

```bash
openforage callbacks list --data-dir .openforage-state --json
```

Emit a local test event:

```bash
openforage callbacks emit --data-dir .openforage-state --event-type failure --severity warning --payload-json '{"reason":"local test"}' --json
```

Remove a callback by id:

```bash
openforage callbacks remove --data-dir .openforage-state --callback-id CALLBACK_ID --json
```

## Python API

```python
from openforage.callbacks import emit_event, list_callbacks, register_callback, remove_callback

record = register_callback(
    data_dir=".openforage-state",
    callback_type="file",
    path=".openforage-state/callback-events.jsonl",
)
emit_event(
    data_dir=".openforage-state",
    event_type="failure",
    severity="warning",
    payload={"reason": "local test"},
)
callbacks = list_callbacks(data_dir=".openforage-state")
remove_callback(data_dir=".openforage-state", callback_id=record["callback_id"])
```

## Event Types

Use supported event types only:

- `failure`
- `degraded_submission`
- `stalled_download`
- `broken_algorithm`
- `improvement_prompt`

## Integration Choices

Prefer generic adapters in this order:

1. File callback to JSONL.
2. Webhook callback if the runtime provides a documented local or remote webhook receiver.
3. Shell callback only when command execution is intentionally enabled and the command is a fixed argv list.

Do not put secrets in callback examples. Do not invent runtime-specific APIs; if a runtime has no documented shell, webhook, or file bridge, keep the callback as a local JSONL handoff.

## Adjacent Skills

- `scheduled_improvement_loops` — read before `T-OF-schedule` (scheduled prompts require a sink to be registered first).
- `agent_runtime_integration` — read before `T-OF-improvement-loop` (how runtimes consume callback events and persist the durable outcome).
- `background_search_loops` — read before `T-OF-start-search-bg` (callback events originate from the worker's lifecycle).
