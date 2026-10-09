# Diagnostics

Use this page when an OpenForage worker or research campaign is stalled, stale, or ambiguous. Diagnose from durable artifacts before changing any state. The background research agent owns the worker; the parent reads but does not mutate while the child is live. See [Background Agent](/docs/agents/background-agent.md).

## Read status before acting

```bash
openforage status --data-dir .openforage-state --json
```

Read these keys explicitly:

- `pid`, `running`;
- `process_health.state`, `process_health.heartbeat_age_seconds`;
- `data_download_progress.phase`, `data_download_progress.percent`;
- `signals_found`, `signals_submitted`;
- `estimated_earnings`, `actual_earnings` (server-reported metadata, not settlement);
- `recent_errors`;
- `paths.event_jsonl_file`, `paths.worker_log_path`.

Do not expect in-process metrics (`total_evaluated`, `total_found`, `signals_per_hour`) in the CLI status envelope; those come from a Python `SearchHandle`. See README §E.3.

## Worker classification

| Symptom | Likely meaning | Next step |
|---|---|---|
| `running: false`, `process_health.state: "not_running"` right after start | Worker never stayed up | Inspect `worker.log` and `events.jsonl`; classify the bootstrap error before restarting. |
| `process_health.state: "stale"` or heartbeat age growing far beyond the polling cadence | Stale pid or stalled supervision | Inspect `background.pid`, `worker.log`, and recent `events.jsonl` entries. |
| Nonempty `recent_errors` or a `worker_error` event | Bootstrap or search failure | Classify the error (network, auth, backtester, data) before restarting. |
| Data download not progressing | `stalled_download` condition | Check phase, bytes, and network reachability to `api.openforage.ai`. |
| Scheduled prompt fires but nothing happens | No non-scheduled sink registered | Register a `file`, `webhook`, or `shell` callback. |

Event types to look for in `events.jsonl`: `start`, `heartbeat`, `register`, `search_start`, `worker_error`, `failure`, `degraded_submission`, `stalled_download`, `broken_algorithm`, `improvement_prompt`, `stop`, `worker_exit`.

## Campaign classification

Read the campaign manifest (`.openforage-research/campaign.json` by convention), the child registry, and the capability report:

- **State** — read the current state and the append-only transition history
  against the authoritative
  [campaign state machine](/docs/agents/references/campaign-state-machine.json).
  A recorded transition whose guard did not pass, a resume that targets
  anything other than the recorded `resume_target`, or any activity in a
  terminal state (`stopped`, `completed`, `exhausted`) is a contradiction:
  stop and reconcile before proceeding.
- **Completion** — only completed evaluator simulations count toward the frozen `100000` baseline budget. Checkpoints, cache hits, and runtime errors are not completed work; a final failing-quality evaluation is completed. Reconcile `attempted` / `completed` / `in_flight` / `failed` before trusting a resume, and keep an incomplete budget incomplete.
- **Coverage** — compare valid pairs, evaluated pairs, and dependency pairs. Sparse dependency coverage is expected; do not treat it as a campaign failure by itself.
- **Era/config** — confirm results are segmented rather than pooled across era boundaries, and that a resume mismatch opened a new segment instead of splicing.
- **Submissions** — pending is not accepted, and an ambiguous acknowledgment is not a rejected or accepted one. Check the `server_results_through` cutoff and the ambiguous count before concluding anything from server results.
- **Ownership** — if the registry shows a live child, do not start another. If the child process is dead but the registry says active, reconcile the registry from status and events before resuming, transfer ownership under the verified claim/lock, and advance the fencing token.
- **Resources** — compare the declared limits against the observed values. A limit with no enforcement mechanism is unenforced, not unlimited; a plateau is handled by research or pivot before any terminal stop.
- **Blocked infrastructure** — `needs-input` with bounded retries is the correct state when a required dependency is unavailable. Do not relaunch unattended from a terminal state.

## Analytics

When status and events do not explain a slowdown, use the analytics helpers (README §L):

- `get_search_stats()` — per-function and per-feature yield rates;
- `get_search_patterns()` — depth distribution and family combinations;
- `get_search_velocity()` — discovery rate over time;
- `get_yield_analysis()` — diminishing returns;
- `get_suggestions()` — recommended directions;
- `get_correlation_patterns()` — function pairs co-occurring in passes.

Attach analytics output to the campaign manifest rather than editing `.openforage/settings.yaml`.

## Safe actions

1. Stop before mutating templates, settings, or local state; confirm quiescence.
2. Do not edit `events.jsonl` or `status.json` in place while a worker is running.
3. Do not delete a data directory while a live child owns it.
4. Record the diagnosis, the classification, and the action in the campaign manifest, including negative findings.
5. If the deviation is a known good baseline (README §J), change nothing.

## Fresh-agent evaluation verdicts

Current v2 contribution and paper-backed research verdicts include structured
`diagnostics` with a stable code/category, observed and missing evidence, a
native action sequence/hashed identity or workspace-relative artifact path when
available, and one bounded next step. The categories distinguish native
permission denial, unsupported evidence format, artifact/schema errors,
protocol violations, research-comparison outcomes, and runtime unavailability.
Use the verdict's next step and remain within the trial's permitted workspace;
do not retry by changing permissions or inspect held-out or grader source.

Negative, mixed, and incomplete honest research results are informational
comparison outcomes, not protocol failures. Read the matching evaluation guide
for native evidence and artifact scope:
[finished contributions](../../examples/fresh-agent-evals/v2/README.md) ·
[paper-backed research](../../examples/fresh-agent-evals/research/README.md).
Older verdicts remain unchanged and may not contain structured diagnostics.

## Related pages

- [Background Agent](/docs/agents/background-agent.md)
- [Research Campaign](/docs/agents/research-campaign.md)
- [Harness Verification](/docs/agents/harness-verification.md)
- `src/openforage/skills/background_search_loops/SKILL.md`
