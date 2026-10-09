# Codex lifecycle marker fixture

This is a small, explicitly invoked fixture for Codex CLI process/thread
identity, exact-ID continuation, and session-history recall. It is not an
adapter, a scheduler, an OpenForage worker, or a measured model result. A
deterministic `--selfcheck` uses inert synthetic events only and never starts
Codex.

## Actual run

Choose a caller-selected model, reasoning effort, output path, unique workspace
path, unique state path, and per-process timeout. There is no model default in
CI. Example shape (choose a new run identity and paths that do not already
exist):

```bash
.venv-docs/bin/python examples/codex-lifecycle/run_fixture.py \
  --model <caller-selected-model> --reasoning-effort <caller-selected-effort> \
  --workspace .tmp/codex-marker-workspace-run01 \
  --state .tmp/codex-lifecycle/run01 \
  --output .tmp/codex-lifecycle/run01.json \
  --timeout-seconds 360
```

Choose unused paths before running. The fixture refuses existing workspace,
state, and output paths, and refuses equality or ancestor/descendant overlap
among all three. State and output must be under ignored `.tmp`. The workspace
may be fresh under ignored `.tmp` or outside the checkout under the system temp
root; symlink escapes and the source checkout are refused. It receives only the
small fixture `AGENTS.md`. A local Git root is created at the workspace and
verified as the actual `git rev-parse --show-toplevel` before the Codex version
probe or any model invocation. Repository-ancestor `AGENTS.md` files are not
preflight blockers when this nearest Git root is established. Raw Codex JSONL
streams remain in ignored `.tmp`, outside the model workspace.

Authentication uses the current Codex environment. The fixture does not change
`CODEX_HOME`, inspect credentials, or reject a user-level `AGENTS.md`. The
record's `environment` metadata includes `os_platform` and only a boolean
`user_agents_md_present` value (or `null` if presence cannot be checked); it
does not store the instruction path or contents and does not prove that Codex
autoloaded the file. User-level instructions may apply, so observations are
scoped to the recorded environment.

Each Codex process receives caller-selected `--model` and
`model_reasoning_effort` through `-c`, `-c features.goals=true`, global
`--no-daemon`, `--ask-for-approval never`, and `--sandbox read-only`, followed
by `exec --json --ignore-user-config`. A continuation uses `exec resume` with
the exact child thread ID, never an implicit last-thread selector. The fixture
does not use `--ephemeral`, because that disables persistent session files.
Prompts prohibit shell, network, library, file-write, nested-agent, and
OpenForage actions. Read-only sandboxing means the marker can be returned in
structured final JSON; no unsafe flags or workspace writes are needed.

The sequence is bounded and sequential:

1. A fresh parent process returns a structured ready acknowledgment and exits.
2. Only after that exit, a fresh child process starts in its own CLI process
   and thread. It receives a random one-time marker, is told to remember it in
   thread history without writing it to a file, and is asked to complete a
   native goal/task only if that mechanism is exposed.
3. Two new Codex processes resume the exact recorded child ID with different
   event IDs. Neither resume prompt contains the marker. Each must return the
   event ID and recalled marker in the native final message, and the native
   returned thread ID must still equal the target.
4. A finite **external** driver waits for one due event and dispatches one more
   exact-ID resume. Paused and terminal-stopped driver states each receive a
   bounded due window with zero dispatches. Duplicate event refusal is
   deterministic external-driver control evidence only.

The record stores hashes for the three direct fixture files and the imported
`examples/opencode-lifecycle/bridge_core.py`, CLI version, selected
model/reasoning value, UTC timestamps, process/thread identity booleans,
explicit exit codes, exact targets, marker hashes/match booleans, sanitized
argv, allowlisted native tool names/statuses, cleanup outcomes, and unknowns.
It never stores the raw marker, prompts, provider/reasoning payloads, usage
fields, or absolute host paths. Raw JSONL remains only in ignored `.tmp`. Native
goal/task events are recorded only when Codex JSON provides an explicit tool
identity and status; malformed or missing identities remain `unknown`, even if
a feature is enabled or the model says it completed a goal. History recall is
not global-memory verification.

The parent exits before the child begins. This proves only that sequential
parent-exit-before-child-continuation path if observed; it does not test a
parent-created concurrent daemon, parent-kill, hangup, reboot, or active
process survival. External paused/stopped checks prevent later driver
invocations; they do not pause or kill active Codex work. No native scheduler,
global memories, crash recovery, duplicate Codex process ownership, OpenForage
callbacks, signal search, simulation, or submission is claimed. A failed model,
CLI, parse, ID, history, or cleanup check remains a failed fixture record; no
simulated pass fallback exists.

One bounded run is archived as [sanitized evidence](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json), with a [provenance and limits note](../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.md). On Linux with Codex CLI `0.156.1` and caller-selected Astra/medium settings (2026-09-23 12:42:47–12:43:30 UTC), five CLI calls exited 0, two fresh thread IDs were distinct, and three exact-child-ID resumes returned the recorded history marker. The parent exited before the child. These finite external-driver checks do not establish a native scheduler or active-process pause/stop. Native goal/task operation, global memory, and active pause/stop remain unknown; the overall harness status remains documented-unverified.

## Offline selfcheck

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/codex-lifecycle/run_fixture.py --selfcheck
```

This checks synthetic JSONL thread/tool/message events, malformed item types,
missing and wrong IDs, mismatched history markers, exact-target matching,
scratch admission and path-separation/refusal cases, inherited-instruction
presence metadata, nested Git-root creation/verification, argv placement and
forbidden implicit-history flags, parent/child exit evidence, exact-identity timeout-cleanup wiring,
external-driver due/duplicate controls, and paused/stopped zero-dispatch
controls. Its label is `inert-synthetic-selfcheck`; it is not an
actual-native observation.
