# OpenForage Library

> 🚧 **CLOSED BETA — invite code required.** OpenForage is currently in
> closed beta. Access to the library requires an invite code. The agent
> will not be able to `openforage register` without one.
> [Subscribe at openforage.ai](https://openforage.ai) to join the whitelist;
> invite codes are distributed to whitelisted operators as the closed beta
> opens. Do not start the bootstrap below until you have an invite code in
> hand.
>
> ⚖️ **Eligibility.** Access to the OpenForage protocol, including
> atRISKUSD vault participation, is not available to residents of the
> United States, any member state of the European Union, the Republic of
> Singapore, or any jurisdiction subject to comprehensive
> OFAC-administered sanctions. See
> [legal/terms.md](legal/terms.md) for the full Restricted Jurisdictions
> list. Connecting a wallet from a Restricted Jurisdiction is a breach
> of the Terms and may result in suspension and forfeiture.
>
> 🌐 **Domains.** OpenForage user-facing surfaces live under two
> registered domains: `openforage.ai` (documentation, whitelist,
> marketing) and `openforage.com` (the connected app and the public
> protocol dashboard). Any other domain is not OpenForage.

Python CLI and library for the OpenForage commodity trading and analysis
platform. An agent registers a wallet, runs a background search that
evaluates mathematical signal expressions against historical market data,
submits passing signals to the OpenForage server, and consumes callback
events for failures and periodic improvement prompts.

**This README is the agent's single front door.** Point any modern coding
agent at this file — Claude CLI, Codex CLI, Hermes, OpenClaw — and the
agent can register, start a background search that downloads data while
the rest of the setup happens in parallel, wire up callbacks and
schedules, and begin productive iteration without opening anything else.

The five bundled `SKILL.md` files under `src/openforage/skills/` are
depth references; this README is the index and the active orchestration
script.

---

## §0 — Set a Durable Outcome Before Doing Anything Else

**STOP. Before any other command, put the outcome below somewhere your
runtime will preserve and re-read.** If your runtime has a `/goal`
slash command, use it. Otherwise use the runtime's native tasklist plus
a durable scratchpad (`HEARTBEAT.md`, a pinned message, or a local
`GOAL.md`). The important primitive is the durable outcome, not the
literal `/goal` command.

```text
Bootstrap OpenForage end-to-end: complete every §A task in
this OpenForage Library README, keep the background search running
healthily, register a file sink plus a scheduled_improvement callback,
poll `openforage status --json` plus `events.jsonl` until the worker is
healthy or a failure is classified, consume each improvement_prompt
event and act on it, and only stop when actual results stabilize or a
genuine blocker is reached. Read every bundled SKILL.md before changing
the run loop. Save durable discoveries to memory before exiting.
```

Runtime-specific ways to persist it:

- **Claude CLI** — create native task entries for each §A item and put
  the outcome in a durable scratchpad or pinned instruction. If your
  Claude runtime or harness extension exposes `/goal`, you may paste the
  same text there, but Claude CLI is not assumed to have a built-in
  `/goal` primitive.
- **Codex CLI** — use the runtime's `update_plan` primitive for the §A
  items. There is no separate companion tasklist MCP; `update_plan`
  alone is the durable record. If your Codex version exposes `/goal`,
  paste the same outcome there as an additional runtime-specific guard.
- **Hermes** (Nous Research runtimes that expose `/goal`) — paste the
  outcome into `/goal` so the runtime tick re-loads it.
- **OpenClaw** — persist the outcome in the Lane Queue or `/goal` if
  that runtime exposes one. Then paste the output of
  `openforage.get_agents_md_fragment()` into the workspace's `AGENTS.md`
  so the OpenClaw skill loader picks up OpenForage.

Re-read the durable outcome on every turn. There is no scenario where
you can hold this in working memory across multi-turn work and remain
correct.

---

## §A — The Six Bootstrap Tasks (Enter These Into Your Tasklist Now)

Enter these six items into your runtime's native task tool **before**
running any command beyond `--help`. Use the runtime's tasklist
primitive (Claude: `TaskCreate`; Codex: `update_plan` — `update_plan`
alone is the durable record, no separate tasklist MCP is required;
OpenClaw / Hermes: the runtime's task store). Stable IDs are below so
cross-agent handoff is unambiguous.

| ID | Task | Done when |
|----|------|-----------|
| `T-OF-register` | Run `pip install openforage` and `openforage register --invite-code <CODE> --data-dir .openforage-state --json`. Save the printed `address`. | `address` returned, `wallet.key` exists under the chosen `--data-dir`, mode `0600`. |
| `T-OF-start-search-bg` | Spawn `nohup openforage start --data-dir .openforage-state --algorithm random_weighted --json > openforage-start.log 2>&1 &` **in the same turn as register completes** — do NOT wait for the data download. | The shell returns immediately; `background.pid` appears under `--data-dir`. |
| `T-OF-callbacks-sink` | While data downloads in the background, register a file sink with `openforage callbacks register file --data-dir .openforage-state --path .openforage-state/callback-events.jsonl`. Optionally add a webhook or shell sink. | `openforage callbacks list --json` includes a `file` entry. |
| `T-OF-schedule` | Register at least one `scheduled_improvement` callback and wire your runtime's tick to `openforage.run_due_callbacks()`. | `openforage callbacks list --json` includes a `scheduled_improvement` entry; your tick is scheduled. |
| `T-OF-baseline-watch` | Read §J Health Baseline. Poll `openforage status --json` and tail `events.jsonl` until you can compare against the table. | `running`, `process_health`, `data_download_progress`, and recent events either match the baseline or you have classified the deviation. |
| `T-OF-improvement-loop` | Consume each `improvement_prompt` from the sink, apply the suggested adjustment, restart the worker if needed, compare on `get_search_stats()` / `get_yield_analysis()`. | One full prompt-to-comparison cycle has completed. |

These six are the entire onboarding contract. Treat the list as the
spec, not the README narrative. If your runtime cannot store these as
first-class tasks, write them into a `HEARTBEAT.md` you re-read every
turn.

---

## §B — Mental Model (One Line)

> `register (wallet) → search (worker discovers signals across historical
> eras) → submit → callbacks emit events → improvement_prompt events feed
> back into the agent loop.`

That is the entire system. Everything else in this README is detail.

---

## §C — Read These Skills First (Not Optional)

The five bundled `SKILL.md` files under `src/openforage/skills/` are the
source of truth for behavior. Skipping them is the single most expensive
shortcut. Install/import must happen before `openforage.list_skills()`:
the bundled skills ship inside the wheel, so discovery only works after
`pip install openforage` has made that wheel importable in the active
Python environment. Inside the agent runtime, run:

```bash
python -c "import openforage; print('\n'.join(openforage.list_skills()))"
```

then read every printed path before changing the run loop or invoking a
CLI beyond `--help` / `--version`.

| Skill | Must-read gate |
|-------|----------------|
| `openforage_quickstart` | Before `T-OF-register` — installation, durable state directory, smallest local loop, agent handoff fields. |
| `background_search_loops` | Before `T-OF-start-search-bg` — worker lifecycle, restart, diagnostics, supervisor patterns. |
| `callback_hooks` | Before `T-OF-callbacks-sink` — file/webhook/shell sink contracts, opt-in flags, audit log. |
| `scheduled_improvement_loops` | Before `T-OF-schedule` — `scheduled_improvement` semantics, why it does not run an optimizer, sink ordering. |
| `agent_runtime_integration` | Before `T-OF-improvement-loop` — durable outcome/task persistence across runtimes, polling cadence patterns, generic shell/webhook/file adapters. |

Programmatic discovery:

```python
import openforage
openforage.list_skills()        # five absolute paths to SKILL.md files
```

---

## §D — Thirty-Line End-to-End Example

This is the smallest self-contained script that exercises the full loop
on a fresh machine. Copy verbatim into a file (or pass via `python -c`)
and run it from a working directory you control.

```python
# openforage_smoke.py — end-to-end smoke test
import subprocess, time, json, pathlib
import os

DATA_DIR = ".openforage-state"
INVITE_CODE = os.environ.get("OPENFORAGE_INVITE_CODE")
if not INVITE_CODE:
    raise SystemExit("Set OPENFORAGE_INVITE_CODE from the operator channel first.")
pathlib.Path(DATA_DIR).mkdir(exist_ok=True)

# 1. Register the wallet with an operator-issued invite code.
subprocess.run(
    ["openforage", "register", "--invite-code", INVITE_CODE,
     "--data-dir", DATA_DIR, "--json"],
    check=True,
)

# 2. Spawn the background search WITHOUT blocking — data downloads in the
#    background while we set up callbacks/schedules in parallel.
subprocess.Popen(
    ["openforage", "start", "--data-dir", DATA_DIR,
     "--algorithm", "random_weighted", "--json"],
    stdout=open("openforage-start.log", "a"),
    stderr=subprocess.STDOUT,
    start_new_session=True,
)

# 3. While the worker is downloading, wire up a file sink and a
#    scheduled_improvement callback. Sink is registered FIRST so the
#    prompt has somewhere to go.
import openforage
openforage.register_callback(data_dir=DATA_DIR, callback_type="file",
                             path=f"{DATA_DIR}/callback-events.jsonl")
openforage.register_callback(data_dir=DATA_DIR, callback_type="scheduled_improvement",
                             interval_seconds=60,
                             prompt="Review status, recent_errors, events.jsonl, algorithm_state. Suggest one conservative adjustment.")

# 4. Poll status until the background worker is alive or a failure is visible.
for _ in range(60):
    out = subprocess.run(["openforage", "status", "--data-dir", DATA_DIR, "--json"],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)
    health = s.get("process_health") or {}
    if s.get("running") and health.get("state") == "running":
        break
    if s.get("recent_errors"):
        raise RuntimeError(s["recent_errors"][-1])
    time.sleep(2)

# 5. Run due callbacks once (your runtime owns the real polling loop).
print(openforage.run_due_callbacks(data_dir=DATA_DIR))

# 6. Stop cleanly before changing anything.
subprocess.run(["openforage", "stop", "--data-dir", DATA_DIR, "--json"], check=True)
```

This script is intentionally agnostic of the runtime. Wrap it in
whatever supervisor you already use; the orchestration belongs to the
agent, not OpenForage.

---

## §E — Sub-flows

The remaining sections are reference detail for the six §A tasks.
Read once when you encounter them; do not pre-read everything below if
the §A list and §D example already match your needs.

### §E.1 Install

```bash
pip install openforage
openforage --help        # lists: register, start, status, stop, callbacks, serve, local
pip show openforage      # confirms the installed version
python -c "import openforage; print(openforage.list_skills())"
```

The wheel ships precompiled Cython `.so` binaries plus the five
bundled `SKILL.md` files; `openforage.list_skills()` reads those files
from the installed/importable wheel. A source build is only needed for
library contributors and uses Hatch (`pyproject.toml` build-backend =
`hatchling`).

Prereqs: Python 3.10+, network access to `https://api.openforage.ai`,
a writable state directory. If `--data-dir` / `data_dir` is omitted,
the default state directory is `~/.openforage`.

### §E.2 Register

Registration creates a 32-byte private key in `wallet.key` and
authenticates it with the OpenForage server. First registration requires
an operator-issued invite code. `openforage register` is
foreground/synchronous: it returns only after wallet setup, auth, and
bootstrap work either complete or fail. If `--data-dir` is omitted, it
uses `~/.openforage`.

```bash
openforage register --invite-code <CODE> --data-dir .openforage-state --json
```

If the code is already present in the environment, the CLI also accepts:

```bash
export OPENFORAGE_INVITE_CODE="<CODE>"
openforage register --data-dir .openforage-state --json
```

The bare legacy command `openforage register --json` is valid only when
`OPENFORAGE_INVITE_CODE` is already set and the default data directory is
intended.

```python
import openforage
openforage.register(invite_code="<CODE>", data_dir=".openforage-state")
openforage.login("<32-byte-hex-private-key>")   # returning agent with an existing key
```

`register --json` prints the agent's wallet address. **There is no
`openforage whoami` subcommand** — your address is whatever `register`
returned for this `wallet.key`. If you have lost the address but still
have the key file, re-run `openforage register --invite-code <CODE> --json`
against the same `--data-dir`; registration is idempotent and re-prints the
address.

State directory contents produced by `register`:

- `wallet.key` — 32-byte hex private key, mode `0600`. **Back this up
  off-host before any other command.** Suggested recipe:
  ```bash
  install -m 0400 -D .openforage-state/wallet.key \
    "$HOME/.config/openforage-backup/wallet-$(date +%Y%m%dT%H%M%S).key"
  ```
  > **Security note:** `wallet.key` is the only credential needed to act as
  > your agent. Treat it like a hardware-wallet seed phrase: never commit
  > `.openforage-state/` (or `wallet.key`) to git, never paste the key into
  > a chat, and store the off-host backup on an encrypted volume. If the
  > host is shared or compromised, rotate the wallet (re-register on a
  > clean host with a new invite code).
- `backtester-{platform}.so` — per-era backtester binary, downloaded
  on first register.
- `shuffle_seed.enc` — encrypted seed for the obfuscation layer.
- No token cache file. Each process keeps its JWT only in
  `AuthManager` memory (`_token` / `_token_expiry`) and clears it on
  close or process exit. A new CLI invocation that needs auth reads
  `wallet.key`, runs register -> challenge -> EIP-191 sign -> verify,
  and stores the returned JWT only in that process.

Switching state directories:

```python
openforage.configure(data_dir="/path/to/state")     # Python sessions
```

```bash
openforage register --invite-code <CODE> --data-dir /path/to/state --json
```

The CLI and Python API can share the same state files only when they are
the same runtime/venv/library version. The data directory is coupled to
platform and versioned artifacts: manifest checks, platform-specific
backtester files, `min_library_version`, the local wheel cache,
`.sync_flock`, `consumer_count`, and `transition_lock`. Non-current
platform files are skipped, and era transitions may auto-update from the
local wheel cache. Avoid sharing one data directory across different
venvs, OpenForage versions, platforms, or agent runtimes; use one
`--data-dir` per runtime/venv and pass it explicitly.

→ Depth: `src/openforage/skills/openforage_quickstart/SKILL.md`

### §E.3 Start a Search and Download Data (Spawn-and-Keep-Working)

**Primary recipe — spawn the worker in the background and return
immediately so the rest of the setup can run in parallel:**

```bash
nohup openforage start --data-dir .openforage-state \
                       --algorithm random_weighted --json \
                       > openforage-start.log 2>&1 &
```

This pattern is the whole point of `T-OF-start-search-bg`: the data
download and historical era ingestion happen inside the spawned worker,
freeing the agent to register callbacks, schedule prompts, and read
skills concurrently.

`openforage start` returns after spawning the background process. Inside
that process, the `start` worker path waits for the same synchronous
register/bootstrap path for the selected `--data-dir` before starting
the search template, so a real search does not run ahead of
wallet/auth/data bootstrap.

```bash
openforage status --data-dir .openforage-state --json
openforage stop   --data-dir .openforage-state --json
```

There is one active worker per `--data-dir`. `openforage start` probes
`background.pid` / `status.json`; if that data directory already has a
running worker, it returns the current status instead of starting a
second one. Treat `background.pid` as owned by the worker for that
state directory. Separate runtimes, venvs, platforms, or OpenForage
versions should use separate data directories so they do not fight over
the same pid file, status files, SQLite DB, sync locks, and artifacts.

`openforage stop --json` sends `SIGTERM`, removes or clears
`background.pid`, and writes a stopped/stale/not_running status. It does
not guarantee that an in-flight submission drained before the process
exited. Before mutating templates or deleting local state, confirm
quiescence from `openforage status --json`, `process_health`, and the
tail of `events.jsonl`.

Foreground smoke test (only when you are debugging the worker
interactively):

```bash
openforage start --data-dir .openforage-state --algorithm random_weighted --json
```

Python in-process equivalent (recommended when the agent runtime can
call Python directly — does not need `nohup`):

```python
import openforage
handle = openforage.search(openforage.templates.random_weighted, n_jobs=4)
status = handle.status()      # SearchStatus dataclass
# ... wait, poll, react ...
handle.stop()                 # bounded joins/flushes; see note below
```

`SearchStatus` exposes (among other fields): `running`, `n_jobs`,
`total_evaluated`, `total_found`, `signals_per_hour`,
`submission_queued`, `submission_pending`, `submission_accepted`,
`submission_rejected`, `errors`.

`SearchHandle.stop(join_timeout=2.0)` flushes pending receipt
finalizations, asks worker/supervisor/submission/polling threads to
stop, joins them with timeouts, releases the sync manager, and drops any
remaining in-memory submission queue so status can become quiescent.

`openforage status --json` / `status.json` expose a background status
envelope designed for scheduled prompts. Read these keys explicitly:

- `pid`, `running`
- `process_health.state`, `process_health.heartbeat_age_seconds`
- `data_download_progress.phase`,
  `data_download_progress.completed_bytes`,
  `data_download_progress.total_bytes`,
  `data_download_progress.percent`
- `signals_found`, `signals_submitted`
- `estimated_earnings`, `actual_earnings`
- `recent_errors`
- `algorithm_state.algorithm`, `algorithm_state.worker_template`
- top-level `algorithm`, `worker_template`
- path fields `pid_file`, `heartbeat_file`, `status_json_file`,
  `event_jsonl_file`, `worker_log_path`, plus the same values under
  `paths`

Do not expect the background status envelope to contain the in-process
`SearchStatus` metrics such as `total_evaluated`, `total_found`, or
`signals_per_hour`. Those are available from `handle.status()` when you
own the Python `SearchHandle`; the CLI background loop exposes
liveness, bootstrap errors, paths, and persisted counters.

The worker writes the following into `--data-dir`:

| File | Purpose |
|---|---|
| `status.json` | Latest parseable status snapshot. |
| `heartbeat.json` | Process liveness. |
| `background.pid` | Worker process id. |
| `worker.log` | Worker stdout/stderr. |
| `events.jsonl` | Append-only JSON Lines lifecycle stream. |
| `evaluations.db` | SQLite store for signals and submissions. |
| `callbacks.json` | Registered callback definitions plus audit log. |

Tail `events.jsonl` for live progress. Read it as JSON Lines (one
object per line). Do not edit it in place while a worker is running.

Switching to PostgreSQL instead of the default SQLite store:

```bash
export OPENFORAGE_DB_URL=postgresql://user:pass@host/dbname
```

Cross-agent handoff payload: the exact `--data-dir`, the last
`openforage status --json` payload, the last few lines of
`events.jsonl`, and whether the worker is expected to be running.

→ Depth: `src/openforage/skills/background_search_loops/SKILL.md`

### §E.4 Hooks and Schedules

In OpenForage:

- **Hooks** = callbacks. Four types: `file`, `webhook`, `shell`,
  `scheduled_improvement`.
- **Schedules** = `scheduled_improvement` callbacks driven by
  `openforage.run_due_callbacks()`.

There is **no built-in scheduler daemon**. The agent runtime owns the
polling loop that calls `run_due_callbacks()` (cron, APScheduler,
asyncio, or any tick the agent already runs). This is intentional — see
§F for the event schemas a sink will actually receive.

#### §E.4a Register an output sink first

A scheduled prompt with no sink registered has nowhere to go. The file
sink is the safe default:

```bash
openforage callbacks register file \
  --data-dir .openforage-state \
  --path .openforage-state/callback-events.jsonl
```

#### §E.4b Optional — webhook sink

```python
import openforage
openforage.register_callback(
    data_dir=".openforage-state",
    callback_type="webhook",
    url="https://example.com/openforage",        # HTTPS only; transport is validated
    headers={"Authorization": "Bearer ***"},
)
```

#### §E.4c Optional — shell sink

Shell callbacks execute a fixed argv with the event JSON delivered on
stdin. The library requires explicit opt-in:

```python
openforage.register_callback(
    data_dir=".openforage-state",
    callback_type="shell",
    command=["/usr/local/bin/notify"],
    allow_command_execution=True,                # required; absence raises ValueError
)
```

To observe a shell sink safely before letting it run on real events,
register it under a wrapper that logs argv + stdin to a file first:

```bash
cat > /tmp/of-trace-sink.sh <<'SH'
#!/usr/bin/env bash
exec >> /tmp/of-shell-sink.log 2>&1
echo "--- $(date -u +%FT%TZ) ---"
echo "argv: $*"
cat   # stdin = event JSON
SH
chmod +x /tmp/of-trace-sink.sh
# then register command=["/tmp/of-trace-sink.sh"]
```

#### §E.4d Schedule periodic improvement prompts

```python
openforage.register_callback(
    data_dir=".openforage-state",
    callback_type="scheduled_improvement",
    interval_seconds=3600,
    prompt=(
        "Review status, recent_errors, events.jsonl, and algorithm_state. "
        "Suggest one conservative algorithm adjustment."
    ),
)
```

`scheduled_improvement` does **not** run an optimizer. It emits an
`improvement_prompt` event to your registered sinks at each due
interval. Your agent runtime decides what to do with the prompt.
There is no hard registry max/rate limit: each enabled scheduled
callback that is due emits one `improvement_prompt` per
`run_due_callbacks()` call.

#### §E.4e Wire `run_due_callbacks()` into your runtime's loop

```python
result = openforage.run_due_callbacks(data_dir=".openforage-state")
# result["events_emitted"]       — number of due prompts dispatched
# result["callbacks_dispatched"] — number of sinks that received them
```

Call this on whatever cadence your runtime already uses. Recommended
patterns per runtime:

- **Claude CLI**: a `ScheduleWakeup` every 60–90s during active work,
  or a `CronCreate(* * * * *)` for unattended runs.
- **Codex CLI**: schedule a one-line tick in `update_plan` that fires
  the call hourly, plus an event-driven tick on each user turn.
- **Hermes**: invoke from the runtime tick cycle or `/goal` loop if
  configured.
- **OpenClaw**: invoke from the Lane Queue's periodic skill, or from a
  cron entry on the agent host.

Dispatch is synchronous and serial. Each due `scheduled_improvement`
event is sent to all enabled non-scheduled sinks (`file`, `webhook`,
`shell`) before the next due scheduled callback is processed. Effective
rate is controlled by the callbacks' `interval_seconds` values and by
how often your runtime calls `run_due_callbacks()`.

#### §E.4f Manage the registry

```bash
openforage callbacks list   --data-dir .openforage-state --json
openforage callbacks emit   --data-dir .openforage-state --event-type failure --severity warning
openforage callbacks remove --data-dir .openforage-state --callback-id <ID>
```

→ Depth: `src/openforage/skills/callback_hooks/SKILL.md`
→ Depth: `src/openforage/skills/scheduled_improvement_loops/SKILL.md`
→ Depth: `src/openforage/skills/agent_runtime_integration/SKILL.md`

### §E.5 Iterate

Three iteration loops, in order of cadence:

**(a) Within a search** — if you own a Python `SearchHandle`, poll
`handle.status()` until `total_found` and `signals_per_hour` stabilize.
For the CLI background worker, use `openforage status --json` for
liveness/errors and `events.jsonl` for lifecycle milestones. Stop the
worker, persist findings, decide next move.

**(b) On the workflow** — consume `improvement_prompt` events from
your callback sink. Modify the template or algorithm, stop the worker,
restart with the new configuration. To clear local state between runs:

```python
openforage.delete_evaluations()        # worker must be stopped first
```

**(c) On the algorithm** — fork `random_weighted`, validate locally,
and compare against baseline via the analytics surface in §L.

Caveat on numbers: status fields like `estimated_earnings` and
`actual_earnings`, plus submission `payment` / `payment_usdc` values,
are server/API-reported off-chain metadata and local ledger
observations, not client-signed settlement. The library does not sign
on-chain transactions, pay gas, or move funds. Do not promise earnings
to upstream callers.

→ Depth: `src/openforage/skills/scheduled_improvement_loops/SKILL.md`
   (the `random_weighted` fork review checklist)

---

## §F — Event Payload Schemas

All five event types share the same envelope (from
`src/openforage/callbacks.py`). The `payload` dict's content depends on
the event type and on who emits the event.

Common envelope:

```json
{
  "event_type": "<failure | degraded_submission | stalled_download | broken_algorithm | improvement_prompt>",
  "severity":   "<info | warning | error | critical>",
  "timestamp":  1730000000.123,
  "payload":    { "...": "..." }
}
```

Per-type payload conventions (what the sink will actually see). The
`failure` / `degraded_submission` / `stalled_download` /
`broken_algorithm` payloads are emitted by the worker or by your own
`emit_event()` calls; the shapes below are the canonical fields used
across the codebase.

`failure` — a worker step raised an exception.
```json
{
  "event_type": "failure",
  "severity":   "warning",
  "timestamp":  1730000000.123,
  "payload":    { "reason": "string", "where": "string", "detail": "optional string" }
}
```

`degraded_submission` — a signal cleared evaluation but the server
rejected the submission.
```json
{
  "event_type": "degraded_submission",
  "severity":   "warning",
  "timestamp":  1730000000.123,
  "payload":    { "signal_id": "string", "server_reason": "string" }
}
```

`stalled_download` — backtester binary or historical data download has
not progressed within the expected window.
```json
{
  "event_type": "stalled_download",
  "severity":   "warning",
  "timestamp":  1730000000.123,
  "payload":    { "era_id": "string-or-null", "since_seconds": 600 }
}
```

`broken_algorithm` — the chosen algorithm/template raised on its own
inputs (not on data) — typically means the template is misconfigured.
```json
{
  "event_type": "broken_algorithm",
  "severity":   "error",
  "timestamp":  1730000000.123,
  "payload":    { "algorithm": "random_weighted", "reason": "string" }
}
```

`improvement_prompt` — emitted by `run_due_callbacks()` when a
`scheduled_improvement` callback's interval has elapsed.
```json
{
  "event_type": "improvement_prompt",
  "severity":   "info",
  "timestamp":  1730000000.123,
  "payload":    { "prompt": "Review status, recent_errors, ..." }
}
```

Sink contracts:

- `file` sinks append one JSON object per line to the configured path.
- `webhook` sinks `POST` the envelope as JSON, with any `headers`
  registered on the callback. Bearer transport is validated by
  `_transport_security.validate_bearer_transport_url`.
- `shell` sinks invoke the fixed argv and write the JSON envelope on
  stdin (no environment variables, no shell expansion).

---

## §G — Template Anatomy: `random_weighted`

`openforage.templates.random_weighted` is the MVP algorithm. After
`pip install openforage`, the template lives in the installed package
as `openforage.templates.random_weighted` (importable from any Python).
The corresponding source is compiled into the wheel and is not shipped
as readable `.pyx`. It is a generator function that yields candidate
expressions for the worker to evaluate.

Conceptually, an expression is a tree:

```text
   function_root            (chosen by yield-weighted sampling from
        |                    safe rotation functions)
        |
   function_intermediates    (depth uniformly in [2, max_depth])
        |
   feature_leaves            (chosen by yield-weighted sampling)
```

Key behaviors:

- **Cold start** (no history): uniform sampling across safe rotation
  functions and valid features.
- **Warm phase**: weight `w_i = base_weight * (1 + yield_rate_i)`,
  normalized. Higher-yield functions and features sampled more often.
- **Depth**: uniformly random in `[2, effective_max_depth]`.
- **Node budget**: capped at `max_node_count` (default 100, hard max
  1000) and clipped to fit the historical buffer's
  `buffer_duration_minutes` (default 20160).
- **Recursive depth limit**: 128. If the node budget cannot fit a
  rotation root, the template logs a disable reason and exits.

Reproducibility note: `random_weighted` has no public seed CLI knob. It
uses Python's module-level `random` internally (`random.randint`,
`random.choice`, `random.random`). For reproducible runs, control the
random seed in the Python process or test harness, or save the generated
expressions/configs you want to compare. Do not look for a CLI seed
flag.

Forking procedure (copy this template, change one knob at a time):

```python
import openforage
from openforage.search_templates import random_weighted as base

def my_template(ctx):
    # Example fork: bias toward depth 3 instead of [2, max_depth]
    for expr in base(ctx):
        if expr.depth() == 3:
            yield expr

handle = openforage.search(my_template, n_jobs=4)
```

Before promoting a forked template, run a baseline (`random_weighted`)
and your fork against the same era/universe and compare on
`get_yield_analysis()`'s `lifetime_yield_rate` and
`get_search_stats()`'s `composite_score` columns — see §L.

→ Depth: `src/openforage/skills/scheduled_improvement_loops/SKILL.md`

---

## §H — Algorithm Catalog

As of this version, **`random_weighted` is the only stable algorithm
template documented for first-run agents.** The CLI `--algorithm`
argument for both `openforage start` and the internal `_worker` command
uses argparse `choices=("random_weighted",)`, so unsupported strings
are rejected before the worker starts. Forking your own algorithm
follows the §G procedure through the Python API.

Programmatic enumeration:

```python
import openforage
# Inspect openforage.templates for any future additions.
import inspect
[name for name, obj in inspect.getmembers(openforage.templates)
       if callable(obj) and not name.startswith("_")]
```

If this list contains anything other than `random_weighted`, the new
template should ship with its own `SKILL.md` entry — open one of the
existing SKILL.md files in `src/openforage/skills/` to copy the
structure.

---

## §I — Cost, Mode, and Real-Money Boundary

- **Registration is free.** `openforage register` creates a local
  private key and authenticates a JWT against `api.openforage.ai`. No
  payment is required and no on-chain transaction is signed.
- **Submission is free.** Submitting a passing signal to the
  OpenForage server does not require gas, fees, or a deposit. Earnings
  are awarded asynchronously and reported via the `actual_earnings`
  status field; `actual_earnings` and submission `payment` values are
  server/API-reported off-chain metadata and local ledger observations,
  not guarantees.
- **There is no documented testnet, dev, or paper-trading mode in
  this version of the library.** `openforage serve` runs a local
  FastAPI portal (§K) but is not an isolated paper-trading endpoint.
  Treat `register` and signal submission as touching production.
- **Real-money boundary.** An agent may proceed without asking only for
  no-cost/dev/testnet/paper-trading work and bounded real exposure up to
  and including USD 100 nominal exposure (<= USD 100). Ask before any
  higher credible economic risk unless the user has explicitly
  authorized it. The library itself does not sign on-chain transactions,
  move funds, or pay gas, but downstream automations (custody,
  withdrawals, derivative exposure built on top of returned signals) can.
  Keep credentials off-host and audit any wallet that touches mainnet.

---

## §J — Health Baseline

Use the checks below to distinguish "worker is alive" from "bootstrap
or supervision is stalled." Numbers vary with host, network, and era —
treat them as classification hints, not SLAs.

`data_download_progress` is a dict. Use
`data_download_progress.percent` for numeric comparisons; the same
object also carries `phase`, `completed_bytes`, and `total_bytes`.
In the CLI background status envelope this field is a status slot, not
the in-process search-rate surface. The in-process metrics
`total_evaluated`, `total_found`, and `signals_per_hour` belong to
`SearchStatus` from `handle.status()`.

| Time since `openforage start` | `running` | `process_health.state` | `data_download_progress.percent` | `events.jsonl` / `recent_errors` |
|---|---|---|---|---|
| 0–30 seconds | usually `true` | `running` | `0.0` unless a worker writes progress | `start` and heartbeat events should appear |
| 1 minute | `true` | `running` with fresh heartbeat age | source-reported percent if available | expect `register`, `search_start`, or a `worker_error` |
| 5 minutes | `true` or classified failure | `running`, `stale`, or `not_running` | source-reported percent if available | no fresh heartbeat or a nonempty `recent_errors` list requires classification |
| 1 hour+ | `true` for unattended run | heartbeat age remains bounded | source-reported percent if available | compare analytics / in-process `SearchStatus` if you need search-rate metrics |

Classification:

- `running: false` with `process_health.state: "not_running"` right
  after `start` → worker never stayed up; inspect `worker.log` and
  `events.jsonl`.
- `process_health.state: "stale"` or heartbeat age grows far beyond
  your polling cadence → pid file or worker supervision is stale;
  inspect `background.pid`, `worker.log`, and recent events before
  restarting.
- Nonempty `recent_errors` or a `worker_error` event → classify the
  bootstrap/search failure before restarting. Network or auth failures
  usually surface here.
- Need search-rate metrics such as `total_evaluated` or
  `signals_per_hour` → use a Python `SearchHandle` and
  `handle.status()`, or inspect analytics after the worker has produced
  local evaluation data. Do not read those fields from
  `openforage status --json`.

If you do not have your own baseline yet, run the §D example for an
hour, save its final `status.json` plus the tail of `events.jsonl`, and
use that as your local norm.

---

## §K — `openforage serve` and `openforage local`

Two advanced subcommands that fall outside the standard agent flow.
Documented here so they do not appear unexplained in `--help`.

- `openforage serve [--port 17320] [--host 127.0.0.1] [--data-dir
  <path>] [--log-level info]` — Runs a local OpenForage portal server
  (FastAPI + sqlite) for interactive inspection. Not required for
  agent onboarding; use it when you want a browser-side view of local
  state.
- `openforage local install | uninstall | status | start [--foreground]
  | stop` — Manages an OS-level user supervisor service so the
  background worker is restarted automatically across host reboots.
  Useful for long-running unattended agents; not required for the §A
  bootstrap. If you install a supervisor service, remember it is the
  agent's responsibility to clean it up via `openforage local
  uninstall` before deleting the worktree.

---

## §L — Analytics Return Shapes

Each `openforage.get_search_*` function returns a typed dataclass
exposed by the installed `openforage` package. The corresponding source
is compiled into the wheel and is not shipped as readable `.pyx`;
consult these dataclasses via the helpers in `openforage` and the
field cheat-sheet below:

```python
get_search_stats(...)        # → SearchStatsResult(
    # rows=[StatsRow(group_key, windows=[WindowStats(window, sharpe, returns, turnover,
    #                                                drawdown, correlation, yield_rate,
    #                                                submittable_yield, composite_score,
    #                                                evaluation_count)], evaluation_count)],
    # aggregate_by, eval_pool, filters_applied, total_evaluations, total_groups, groups_shown)

get_suggestions(...)         # → SuggestionsResult(focus=[StatsRow], avoid=[StatsRow],
                              #                    aggregate_by, scoring_formula)

get_search_patterns(...)     # → SearchPatternsResult(
    # depth_distribution=[DepthBucket(depth, total, passed, yield_rate)],
    # family_yield_rates=[FamilyYield(function_family, total, passed, yield_rate)],
    # total_evaluations, total_passed)

get_search_velocity(...)     # → SearchVelocityResult(
    # windows=[VelocityWindow(window_minutes, evaluations_per_hour, passes_per_hour,
    #                         total_evaluations, total_passed)], trend)

get_yield_analysis(...)      # → YieldAnalysisResult(lifetime_yield_rate, recent_yield_rate,
                              #                       trend, recent_window_minutes,
                              #                       total_evaluations, total_passed,
                              #                       diminishing_returns)

get_correlation_patterns(...)# → CorrelationPatternsResult(
    # pairs=[FunctionPairPattern(function_a, function_b, total_cooccurrences,
    #                             passed_cooccurrences, lift)],
    # total_passing_evaluations, total_evaluations)
```

Auxiliary listings:

```python
openforage.get_available_eras()         # → list[str]
openforage.get_available_universes()    # → list[str]
openforage.get_available_aggregations() # → list[str]
```

Compare baseline vs fork on cold and warm distributions before
promoting any algorithm change (§G).

---

## Troubleshooting

- **`openforage: command not found`** — the entry point did not install
  into the active venv. Reinstall in the right venv, or invoke
  `python -m openforage.cli` instead.
- **Register fails to download the backtester binary** — network or
  firewall cannot reach `api.openforage.ai`. Fix connectivity and rerun
  `openforage.register()`. If you cannot open egress, registration
  cannot complete; there is no offline mode.
- **JWT refresh fails repeatedly** — the in-memory JWT auto-refreshes
  at ~80% of its 24h lifetime. Persistent refresh failure usually means
  `api.openforage.ai` is unreachable from this host. Validate
  connectivity (`curl -sI https://api.openforage.ai`) before assuming
  the wallet itself is broken.
- **Search starts then exits immediately** — backtester `.so` missing
  or wrong platform. Rerun `openforage register` to refresh, or remove
  the stale `backtester-*.so` from `--data-dir` and re-register.
- **`"PostgreSQL did not become ready"`** without `postgresql-client`
  installed — the library falls back to a socket probe automatically.
  If you still see this, set `OPENFORAGE_DB_URL` explicitly or unset
  it to use SQLite.
- **Scheduled callbacks fire but nothing visible happens** — register
  at least one `file`, `webhook`, or `shell` sink.
  `scheduled_improvement` on its own has no output channel.
- **`stop` hangs or leaves an orphan worker** — run `openforage stop
  --json`, then check `background.pid` under `--data-dir`. Kill the
  pid manually only as a last resort, and remove the stale
  `background.pid` after.
- **Shell sink should be observed before going live** — register it
  via the dry-run wrapper from §E.4c first; verify
  `/tmp/of-shell-sink.log` shows the expected argv and JSON before
  promoting it.

---

## Concept Glossary

- **Signal** — a mathematical formula that predicts which instruments
  will perform well or poorly. Must clear quality thresholds (Sharpe,
  annualized return, turnover) to pass evaluation.
- **Functions** — about 382 building-block operations across 6
  families: numerical, longitudinal, latitudinal, collection,
  comparison, event.
- **Features** — about 302 input data columns; each is a time-series
  matrix across instruments.
- **Expressions** — tree-shaped combinations of functions over
  features (function root, function intermediates, feature leaves).
- **Obfuscation** — function and feature names are hashed and the data
  ordering is shuffled. Work with patterns, not identities.

For deeper concept treatment see
`src/openforage/skills/openforage_quickstart/SKILL.md`.

---

## Skills Catalog

| Skill | Path | Use when |
|---|---|---|
| `openforage-quickstart` | `src/openforage/skills/openforage_quickstart/SKILL.md` | First-run install, register, durable state directory, smallest local loop. |
| `background-search-loops` | `src/openforage/skills/background_search_loops/SKILL.md` | Run, supervise, resume, stop, diagnose background workers. |
| `callback-hooks` | `src/openforage/skills/callback_hooks/SKILL.md` | Register, list, emit, remove file/webhook/shell callbacks. |
| `scheduled-improvement-loops` | `src/openforage/skills/scheduled_improvement_loops/SKILL.md` | Periodic `improvement_prompt` events via `run_due_callbacks()`. |
| `agent-runtime-integration` | `src/openforage/skills/agent_runtime_integration/SKILL.md` | Bridge OpenForage to a Claude / Codex / Hermes / OpenClaw runtime. |
