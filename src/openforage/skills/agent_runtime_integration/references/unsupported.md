# Unsupported And Unresolved Adapters

This page defines what to do when a harness cannot be verified. It routes the `unresolved` and `discovery-only` registry entries and is the shared procedure for products whose names or surfaces are ambiguous. Registry: `references/harnesses.json`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

These conditions govern live or continuous work. If separate same-harness CLI
process/session support is unknown or unavailable, block continuous signal
search only. Ordinary repository contributions and authorized offline
research remain useful; do not fall back to the main session, a same-session
subagent, a heartbeat, or a Python worker.

## Status vocabulary

| Status | Meaning |
|---|---|
| `documented-unverified` | Official documentation describes the surface, but the overall adapter entry is not fully verified. Scoped per-capability observations may still be listed. |
| `discovery-only` | The primary source itself could not be verified (for example, the URL no longer resolves to the stated repository). Do not record product facts. |
| `unresolved` | The product name or semantics are ambiguous, or required capabilities are absent from the source. Do not delegate. |

`verified` is reserved for capabilities observed on the installed product and version, per `docs/agents/harness-verification.md`. No adapter is fully verified in this packet. Codex and OpenCode have bounded, partial per-capability observations for separate CLI invocations and an external background controller; OpenCode also has an earlier external-bridge fixture. Their overall registry status remains `documented-unverified`. A per-capability `verified` claim requires an evidence artifact, test id, date, version, scope, and observed result.

## Unresolved entries

- **Muse** — `unresolved` ([muse.md](muse.md)). Multiple unrelated products share the name; no approved source disambiguates it.
- **Grok Bot** — `unresolved` ([grok-bot.md](grok-bot.md)). Ambiguous name; the approved xAI page documents Grok Build, not Grok Bot.
- **Grok Build** — unresolved candidate ([grok-bot.md](grok-bot.md)). Documented at <https://docs.x.ai/build/overview> as a coding agent with a TUI, headless mode, and ACP, but it has no separate-child ownership contract in that source. Grok Build must not be substituted for Grok Bot.
- **Pi** — `discovery-only` ([pi.md](pi.md)). The supplied upstream URL did not resolve to the stated repository, so the canonical source must be confirmed before any fact is recorded.

## Safe discovery procedure

1. Identify the canonical vendor and product name from an official channel; record the operator's answer verbatim.
2. Obtain the canonical official documentation URL for that exact product. Reject search-result, mirror, or third-party summaries.
3. Confirm the source is for the same named product.
4. Read only official documentation and the canonical repository.
5. Run the capability conformance fixture in `docs/agents/harness-verification.md` against an installed version: distinct session/conversation identity, separate CLI invocation, parent independence, shell execution, workspace read/write, native schedule or verified external scheduler, memory persistence, parent/child status reporting, pause/resume/stop, crash recovery, duplicate-start control, exact target control, and terminal triggers stopped. Use bounded benign marker tasks only.
6. Record every probe and result with product, version, date, observer, instruction, scope, and observed result.
7. Register a capability as verified only after it is observed under the evidence standard. Until then, keep the status honest and do not delegate a live continuous campaign; offline research and maintenance remain available.
8. Re-verify after a harness version change.

The repository provides read-only discovery and offline scaffold helpers:

```bash
python scripts/background_adapters/cli.py discover \
  --product-label "caller-supplied unresolved name" \
  --primary-source <OFFICIAL_DOCUMENTATION_URL>

python scripts/background_adapters/cli.py probe \
  --adapter claude-code --binary <caller-selected-cli>

python scripts/background_adapters/cli.py scaffold \
  --directory ./new-adapter-skeleton
```

`discover` records only fingerprints of caller-supplied labels, sources, and
version strings as unverified, so it does not echo possible credentials or map
ambiguous names to another brand. `probe` is
available only for a registered built-in adapter; it runs that adapter's
explicit version/help argv, captures hashes and exit status, and does not start
a session or infer feature use from option text. `scaffold` refuses an existing
directory and writes an inert interface example, empty primary-source/version
evidence fields, synthetic fixtures, a contract checklist, and fail-loud
unimplemented methods. None installs software, reads credentials, edits auth or
configuration, launches a session, or changes `harnesses.json`.

The synthetic adapter/conformance selfchecks are not product observations. A
caller-selected parent may later run a bounded native proof; typed evidence
must then be reviewed and added through the canonical
`project_capability_records` route. The route never promotes an observation
automatically.

## Rules for unresolved adapters

- Documented, bounded, benign capability probes are permitted on an installed version solely to establish evidence for that exact capability. Use only a probe described by the product's official documentation or the conformance fixture in Harness Verification; keep it reversible and limited to benign marker tasks, with no live or unattended campaign.
- Unverified unattended campaign commands remain blocked. A probe permitted to gather evidence does not authorize unattended spawn, scheduling, pause, resume, or stop use before the relevant capability is verified.
- Do not infer one product's commands from another product's documentation or guess an undocumented probe. Local `--help` and official documentation may be read for discovery; label anything discovered unverified and verify it before any unattended use.
- Do not treat a same-session subagent, an in-process task, a heartbeat, or a resumed main session as the background research agent.
- Do not claim a scheduler exists. Plan records never schedule; only a documented and observed scheduler or a verified external scheduler may fire a tick.
- When a live campaign exists, report the unsupported fact explicitly in its manifest and capability report. A stalled handoff is preferred over a guessed command.

## Related pages

- [Harness Verification](../../../../../docs/agents/harness-verification.md) — required capabilities, the conformance fixture, the evidence standard, and the evidence-gated verified adapter registry.
- [Background Agent](../../../../../docs/agents/background-agent.md) — the contract the child must satisfy.
- [Muse](muse.md), [Grok Bot](grok-bot.md), [Pi](pi.md) — the routed unresolved and discovery-only entries.
