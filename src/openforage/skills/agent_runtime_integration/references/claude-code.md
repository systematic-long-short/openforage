# Harness Routing: Claude Code

Status: **documented-unverified**. Official Anthropic CLI/headless/agent-view
documentation describes the routes below. A local read-only `claude --version`
reported `2.1.281 (Claude Code)` during adapter implementation; this is a
version observation, not a lifecycle or capability proof. The routing registry
contains no Claude capability observations.

Primary sources, reviewed 2026-09-24:

- <https://code.claude.com/docs/en/cli-reference>
- <https://code.claude.com/docs/en/headless>
- <https://code.claude.com/docs/en/agent-view>
- <https://code.claude.com/docs/en/tools-reference>
- <https://code.claude.com/docs/en/goal>
- <https://code.claude.com/docs/en/scheduled-tasks>
- <https://code.claude.com/docs/en/routines>

## External-controller CLI route

The built-in `claude-code` adapter uses the non-interactive CLI route for one
bounded request per process:

```text
claude --print --output-format stream-json --verbose
       --include-partial-messages --model <caller-model>
       [--effort <caller-effort>]
       --session-id <generated-UUID> <prompt>

claude --print --output-format stream-json --verbose
       --include-partial-messages --model <caller-model>
       [--effort <caller-effort>]
       --resume <exact-recorded-UUID> <prompt>
```

The actual implementation passes argv tokens, not a shell command string. The
external controller selects and records the fresh UUID before launch, then uses
only the recorded child UUID for later turns. `--continue`, `--fork-session`,
recent-session selection, and session-name fallback are not used. Claude's
`--bg` conflicts with `-p`/`--print`; the adapter does not combine them.

The event normalizer reads the documented `system/init` and final `result`
session IDs, and accepts an acknowledgement only when the final structured
`result` field is a JSON object matching the request/run/task/turn/fence. It
records documented `stream_event` tool-use starts as unconfirmed events; a
tool-use start alone does not establish a completed operation. Tool result
status is kept unknown unless an explicit normalized event carries that
outcome. Final prose, plans, CLI help, and declared primitive availability are
not operation evidence.

The retained 2026-09-24 isolated-profile attempt used Claude Code `2.1.281`
with native permissions inherited. The native `Write` permission denied the
fixture marker; the child returned blocked explanatory prose/fenced JSON rather
than the required structured acknowledgement. The CLI exit code was 0, but the
task and proof failed and cleanup was confirmed. This run adds no verified
Claude capability, and its native goal/task/memory/scheduler outcomes remain
unknown. No permission bypass or retry was attempted. Keep a denied operation
blocked and report it as such rather than synthesizing an acknowledgement. See
the [retained proof record](../../../../../examples/background-agent/contributor-reliability-wave-20260924/background-claude-code-1.json).

The adapter uses no `--bare`: the official headless docs say bare mode skips
OAuth/subscription credentials and multiple native configuration sources. It
also uses no permission bypass, custom allowlist, `--tools`, `--allowedTools`,
`--settings`, or authentication/configuration mutation. The default profile
inherits the user's normal config, credentials, and permissions. The explicit
isolated proof scopes only a throwaway workspace and its workspace-local
fixture `CLAUDE.md`; that scope is disclosed in the proof.

## Separate native background route

The official CLI documents a distinct native route:

- `claude --bg <prompt>` dispatches a new background session and returns its ID;
- `claude agents --json` reports active/completed background session state;
- `claude logs <ID>`, `claude stop <ID>`, `claude respawn <ID>`, and `claude rm <ID>` manage an exact ID.

These commands are not the external print/stream-json adapter. The reviewed
CLI descriptions do not provide the per-request `run_id` / `task_sha256` /
`turn_id` / fencing-token acknowledgement binding required by the bounded
controller proof. The native route is therefore documented separately and
remains unverified. In addition, `respawn` is not treated as proof of exact-child
continuation if saved conversation history is absent and the original prompt
would be started as a new conversation. `/bg` and `/fork` inside the parent
conversation do not substitute for an independently started child CLI.

## Native goals, task tools, memory, and schedules

Official docs describe session-scoped `/goal`, `/loop`, and Cron tools including
`CronCreate`, `CronList`, and `CronDelete`; cloud Routines are a separate
Anthropic-managed surface. The tools reference names `TaskCreate`, `TaskGet`,
`TaskList`, and `TaskUpdate`. Task tools are available by default only to some
models; explicitly naming one in `--tools` or `--allowedTools` can opt it in on
other models. The adapter does not force that opt-in. Availability in help/docs
does not show that the tool ran.

Native evidence is recorded separately for goal declaration, task creation,
task durable reread, memory write/reread, schedule creation, and actual native
delivery. Task planning metadata is not a shell exit or durable reread. A real
native delivery requires its own identified delivery event; an external
controller `external_due_time` request is never called native scheduling.
No Claude native primitive has been observed by this routing entry.

## Proof and discovery

The reusable external-controller proof is caller-run after the implementation
session exits, with a caller-selected model/effort and explicit CLI binary:

```bash
.venv-docs/bin/python examples/background-agent/proof.py \
  --harness claude-code --model <model> --effort <effort> \
  --binary /path/to/chosen/claude
```

This command is a route description, not an observation or launch
authorization. The native proof must be run separately by the parent. Offline
adapter and synthetic conformance selfchecks do not invoke Claude or a model.
For unresolved products, use the read-only discovery route and caller-requested
scaffold in [unsupported.md](unsupported.md); neither guesses a product identity
or command.

See also [the shared adapter interface](../../../../../examples/background-agent/adapter-interface.md),
[Harness Verification](../../../../../docs/agents/harness-verification.md), and
[Background Agent](../../../../../docs/agents/background-agent.md).
