# Harness Routing: Grok Bot

Status: **unresolved** — the name is ambiguous and must not be treated as Grok Build. The only approved xAI source supplied with this packet documents Grok Build, a distinct coding agent; it does not document Grok Bot. Registry entry: `references/harnesses.json`. Route: `references/grok-bot.md`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Why this is unresolved

"Grok Bot" is an ambiguous name, and the reviewed official page at <https://docs.x.ai/build/overview> documents **Grok Build** — a coding agent with an interactive TUI, headless mode, and ACP — not Grok Bot. Conflating the two would substitute an unrelated product's controls for the product under review. Grok Bot is therefore `unresolved` and no commands are provided.

## Grok Build is only an unresolved candidate

Grok Build may be mentioned as a separate, also-unresolved candidate with the official source <https://docs.x.ai/build/overview>. The source documents:

- an interactive TUI (`grok`);
- headless usage (`grok -p "..."`, with `--output-format streaming-json`);
- ACP integration for other apps.

It does **not** document a separate-child session identity, spawn/pause/resume/stop ownership, or a scheduler. Grok Build is not Grok Bot, and neither has an adapter in this packet.

## Safe discovery procedure

1. Ask the operator which vendor and product "Grok Bot" refers to, and record the answer verbatim in the campaign register.
2. Obtain the canonical official documentation URL for that exact product. Do not substitute the Grok Build page or infer controls from it.
3. Confirm the source names the same product.
4. Read only that official source and the canonical repository.
5. Run the full capability verification in `docs/agents/harness-verification.md` on an installed version before recording an adapter.
6. If the name cannot be resolved to a canonical product, keep the status `unresolved` and report it; do not delegate.

## What is not documented (report, do not guess)

- Everything for Grok Bot. No identity, surface, scheduler, lifecycle, or session evidence is recorded.
- For Grok Build: no spawn/pause/resume/stop contract, no scheduler, and no ownership evidence.

## Safe next step

Treat this page as routing information only. Follow [unsupported.md](unsupported.md) for the shared discovery rules, and keep the capability report at `unresolved` until a canonical primary source is confirmed.
