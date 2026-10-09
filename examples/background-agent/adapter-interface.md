# Background adapter interface

The supervisor remains one lifecycle implementation. Product variation is
limited to the explicit interface in `scripts/background_adapters/contract.py`:

| Method / field | Contract |
|---|---|
| `validate_selector(...)` | Reject unsupported model/variant/effort combinations before a process starts. No model default is supplied. |
| `build_argv(...)` | Return an argv list for a fresh invocation or one exact recorded session. No shell string, `--continue`, `--last`, or implicit recent-session fallback. |
| `version_probe_argv(binary)` / `parse_version(...)` | Use the product's explicit version command and retain its observed version string. |
| `help_probe_argv(binary)` / `parse_probe(...)` | A read-only local-help check. Option presence is routing evidence only; it never establishes successful feature use. |
| `normalize_trace(raw_text)` | Parse documented machine events only; return a validated final structured result, exact session identity, and allowlisted tool/event identities. Raw output stays in private scratch. |
| `extract_session_identity(events)` | Read only product-specific session fields and fail closed on conflicting identities. |
| `declared_primitives` | Describe known/documented goal, task, memory, and scheduler routes. Declarations do not produce observed outcomes. |

`scripts/background_agent_adapters.py` preserves the historical `build_argv`,
`parse_trace`, selector, acknowledgement, and prompt helpers. The existing
controller, store, ownership guard, fencing, recovery, pause/drain, terminal
stop, duplicate-owner exclusion, and owned-process cleanup remain shared. A new
adapter does not install software, mutate authentication/configuration, run a
session, or take ownership of lifecycle supervision.

## Typed operation evidence

`scripts/background_adapters/native_evidence.py` emits a record for each
operation, including goal declaration, task creation, task-list write, durable
task reread, task update, memory write, durable memory reread, native scheduler
creation, and real native scheduler delivery. Each record includes:

- `availability`: `documented`, `unknown`, or `unavailable`;
- `outcome`: an explicit observed success/failure/in-progress/mixed result, or
  `unknown` / `unavailable` when evidence is absent;
- allowlisted tool name, event type/reference, and exact request/turn/fence/session
  scope when an operation event exists.

The adapter may use a tool declaration to recognize an event name, but not to
claim that the tool was available or used. A final message, plan/todo metadata,
CLI exit, help text, or feature flag is not a native operation result. Durable
task reread requires an explicit completed result correlated to the created
task identity. Native scheduler delivery requires an identified native delivery
event; an `external_due_time` row is always external-controller evidence.

## Claude Code routes

The `claude-code` adapter is based on official CLI/headless documentation and
local `claude --help`/`claude --version` shape. The observed local version output
was `2.1.281 (Claude Code)`; that is an installed-version observation only,
not a native lifecycle or capability proof. Relevant official sources are:

- <https://code.claude.com/docs/en/cli-reference>
- <https://code.claude.com/docs/en/headless>
- <https://code.claude.com/docs/en/agent-view>
- <https://code.claude.com/docs/en/tools-reference>

The external-controller adapter uses `claude --print --output-format
stream-json --verbose --include-partial-messages`, a generated UUID with
`--session-id` for a fresh parent/child invocation, and `--resume EXACT_ID` for
later turns. It accepts a final acknowledgement only from the structured
`result` event and session identity only from documented `system/init` or
`result` fields. It records `stream_event` tool-use starts as unconfirmed; a
start event alone is not successful operation evidence. `--continue`,
`--fork-session`, `--bg`, and a session-name/latest-session fallback are not in
this argv.

The native background route is separate: `claude --bg`, `claude agents --json`,
`claude logs EXACT_ID`, `claude stop EXACT_ID`, `claude respawn EXACT_ID`, and
`claude rm EXACT_ID` are documented. `--bg` conflicts with `-p`, so the native
background command is not folded into the print/stream-json controller route.
The native route is documented-unverified here: the reviewed CLI descriptions
do not supply this proof's per-request run/task/turn/fence acknowledgement
binding, and `respawn` is not treated as exact-child continuation when saved
history is absent. Native background sessions are distinct product facilities;
the Linux external-controller proof has its own scope and does not promote the
native route.

The default profile inherits the user's normal configuration and credentials.
The explicit isolated proof scopes isolation to its temporary workspace and
workspace-local fixture instructions. It does not use `--bare`, permission
bypass flags, allowlists, custom settings, or authentication changes. The
proof's child session inherits installed safety and auth behavior. Task tools
`TaskCreate`, `TaskGet`, `TaskList`, and `TaskUpdate` are documented, and naming
them in `--tools` / `--allowedTools` can opt them in on some models; this adapter
does not force that opt-in and does not count help/availability as an operation.

## Discovery and scaffold

The offline command surface is:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/background_adapters/cli.py selfcheck
python scripts/background_adapters/cli.py discover --product-label "unresolved product"
python scripts/background_adapters/cli.py probe --adapter claude-code --binary <chosen-cli>
python scripts/background_adapters/cli.py scaffold --directory ./new-adapter-skeleton
```

`discover` stores only fingerprints of caller-supplied labels/source/version as
unverified, so it does not echo possible credentials or map ambiguous names
such as Muse or Grok to another product. `probe`
reads the selected built-in adapter's version/help argv without starting a
session and outputs hashes rather than raw help/transcript text. `scaffold`
requires a new caller-selected directory and writes only an interface skeleton,
empty evidence fields, inert fixtures, and a fail-loud checklist. None of these
routes changes the canonical capability registry. Actual observations require
the parent to run the bounded proof later and manually review typed evidence
before adding a scoped record through `project_capability_records`.
