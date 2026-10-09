# Harness Routing: Codex

Status: **documented-unverified (partial scoped evidence)** — Codex CLI `0.156.1` has an earlier Linux marker observation and a separate completed Linux proof using the isolated profile of the external background controller. The canonical records and their exact scopes appear in the generated [Harness Verification projection](/docs/agents/harness-verification.md#canonical-scoped-capability-projection). Overall status remains `documented-unverified`; neither run certifies an adapter or the broader OpenForage lifecycle contract. The earlier installation's feature list showed goals enabled and memories disabled; those are availability facts, not evidence that a goal operation or global-memory operation succeeded. Registry entry: `references/harnesses.json`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first, but record operation evidence rather than inferring it from availability.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Documented CLI surfaces

- `codex exec` is the non-interactive CLI entry point. `--json` emits JSONL events, and the event stream can carry a thread ID and native item/tool events. Documentation: <https://learn.chatgpt.com/docs/non-interactive-mode> and <https://learn.chatgpt.com/docs/developer-commands?surface=cli>.
- `codex exec resume <THREAD_ID> <PROMPT>` continues a specific recorded thread. Use the exact ID from the child bootstrap; do not resume the last or most recent thread as a substitute. A resumed process is a new CLI invocation, but continuation retains the selected thread's context; it does not by itself prove parent independence or process lifecycle control. The documented command surface is not a measured identity result.
- `--ephemeral` disables persistent session files. Do not use it for a fixture that must resume history.
- `--ignore-user-config` can isolate configuration for a probe. Authentication still uses the current `CODEX_HOME`; do not reset it or inspect credential values. When requesting native goals under ignored user config, pass `-c features.goals=true` for that process rather than relying on a persistent config change.
- Local help for the observed installation exposed global `--no-daemon`, `--ask-for-approval never`, `-c key=value`, `--model`, `--cd`, and `--sandbox`, plus `exec --json`, `--ignore-user-config`, and structured-output options. Check the installed `exec` and `exec resume` help for exact argument placement before a run.

Examples of the documented CLI shape, with caller-selected values:

```text
codex --no-daemon --ask-for-approval never -c features.goals=true --model <MODEL> --cd <WORKSPACE> --sandbox read-only exec --json --ignore-user-config "<BOUNDED PROMPT>"
codex --no-daemon --ask-for-approval never -c features.goals=true --model <MODEL> --cd <WORKSPACE> --sandbox read-only exec resume --json --ignore-user-config <RECORDED_THREAD_ID> "<BOUNDED PROMPT>"
```

These are documentation examples, not evidence that a command was run or that a native lifecycle capability passed.

## Goals, memory, and scheduler

- The Codex goals guide describes goals as thread-scoped: a goal belongs to the thread where it is created. Request and observe a native goal/task action only if the CLI exposes it; a model's prose or an enabled feature flag is not operation evidence. Source: <https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex>.
- History recall after `exec resume <THREAD_ID>` is session-history continuation, not proof of a global memories feature. The observed local feature list reported memories disabled.
- ChatGPT desktop/web automations are a separate scheduling surface. Standalone scheduled tasks start a new chat per run; tasks created inside a chat return to that chat. The reviewed docs state the Codex CLI does not provide the Scheduled management interface. Source: <https://learn.chatgpt.com/docs/automations?surface=app>.
- The reviewed developer-commands source documents `/goal`; no `/schedule` entry was found on 2026-09-23. The local feature flag `in_app_local_automation=true` is an availability flag, not evidence of an exec-session scheduler. These observations do not establish that every Codex scheduling surface is unavailable. A CLI tick needs a verified native scheduler connected to delivery or a verified external scheduler. A finite driver or controller interval that waits for a due time and calls exact-thread `exec resume` is external scheduling, not a Codex scheduler. Pausing future dispatches does not pause active Codex work.

## Scoped marker fixture

[`examples/codex-lifecycle/`](../../../../../examples/codex-lifecycle/README.md) contains a bounded candidate fixture for a fresh parent `exec`, a distinct child `exec`, two exact-ID history resumes, and one due event dispatched by a finite external driver. Its deterministic selfcheck is inert.

The runnable external background process controller in
[`examples/background-agent/`](../../../../../examples/background-agent/README.md)
adds an explicit-command durable-task CLI, detached ownership acknowledgement,
exact child-thread continuation, process-identity pause/stop, fenced
reconciliation after interruption, and an opt-in `--interval-seconds` external
due-time mode with a required bounded continuation command. Normal launches
retain the caller's native Codex configuration; only its isolated proof profile
adds `--ignore-user-config`. Both profiles retain `--sandbox workspace-write`,
avoid `--ignore-rules`, `--last`, and sandbox bypass, and target exact
`exec resume <THREAD_ID>`. The controller is external process/request plumbing,
not a Codex scheduler or proof of OpenForage worker checkpoint/stop semantics.
Its source and inert selfcheck alone do not count as an observation. The later
retained proof is linked below; native tool use stays unknown unless explicitly
present in that proof, with goal, task, and memory observations kept separate.
The offline `selfcheck` uses fake local processes only.

One actual bounded run is recorded in the [sanitized native marker evidence](evidence/codex-native-marker-20260923.json), with a [scope and provenance note](evidence/codex-native-marker-20260923.md): Codex CLI `0.156.1` on Linux, caller-selected Astra model at medium reasoning effort, 2026-09-23 12:42:47–12:43:30 UTC. All five CLI calls exited 0; the parent and child used distinct thread IDs, the parent exited before the child, and three later resumes addressed the exact child ID and matched the history marker. Six bounded capabilities passed; native goal/task operation, global memory, and active-process pause/stop remain unknown. This is a scoped fixture observation, not adapter certification.

The finite external driver in this earlier fixture dispatched one due resume and its bounded paused/stopped checks prevented future dispatches. Those checks are not native scheduling or native Codex active-process pause/stop evidence. Native tool/event evidence for a goal/task operation was absent in that run, so no operation is inferred from the enabled feature flag or final prose.

The earlier fixture's scope is deliberately limited: the child starts only after the parent exits; it is not a parent-created concurrent daemon, parent-kill, hangup, reboot, or active-process pause/kill survival test. Paused/stopped checks cover only zero future invocations by the finite external driver over bounded due windows. Duplicate refusal is driver-control evidence only. It does not establish a native Codex scheduler, global memory, OpenForage callbacks, search, simulation, or submission.

## Scoped external-controller proof

The separate completed proof in
[`examples/background-agent/native-contribution-wave-20260924/codex-proof.json`](../../../../../examples/background-agent/native-contribution-wave-20260924/codex-proof.json)
records a distinct parent/child thread, parent exit before child bootstrap,
exact-child continuation and history recall, two acknowledged external due-time
turns, controller pause/reconciliation/recovery/terminal-stop edges, and
cleanup. These records apply only to Codex CLI `0.156.1`, Linux, and the
evidence-specific `isolated` profile. The delivery owner is the external
controller, never a native Codex scheduler. Native goal/task/global-memory
operations and OpenForage worker checkpoint/stop state remain unknown. The run
does not certify host reboot or terminal-hangup survival, every user
plugin/configuration, or live OpenForage work. The generated projection above
is the capability summary; do not combine these records with the older fixture's
different profile or mechanism.

## What remains unverified

- A native Codex scheduler; observed recurrence belonged to an external finite driver or background controller.
- Native goal/task tool operations and their statuses, global memory, and native Codex active-process pause/stop. Missing native events are `unknown`, not a failure inferred from model prose and not a pass inferred from feature availability.
- Native Codex crash recovery, duplicate process ownership, concurrent daemon behavior, or parent-death/hangup/reboot survival. The external controller's bounded recovery record is mechanism-specific.
- OpenForage worker checkpoint/stop behavior, live OpenForage work, or every user plugin/configuration.
- That a scheduled app task can reach an OpenForage host data directory.

Use this page as routing information, not as an adapter. Keep the registry entry `documented-unverified` until evidence justifies scoped capability updates; a bounded marker fixture alone does not certify an adapter. See [unsupported.md](unsupported.md) and [`docs/agents/harness-verification.md`](/docs/agents/harness-verification.md).
