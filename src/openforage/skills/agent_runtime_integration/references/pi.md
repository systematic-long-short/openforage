# Harness Routing: Pi

Status: **discovery-only** — the exact upstream URL supplied for this product did not resolve to the stated repository when reviewed 2026-09-22 (GitHub redirected to a different owner), so no product facts, controls, or adapter are recorded here. Registry entry: `references/harnesses.json`. Route: `references/pi.md`.

> **Contract reminders — these apply to every harness.**
>
> - Use native goals, tasks, memory, scheduler, and background controls first.
> - Launch the same harness as a new CLI process for the background research agent.
> - Record a distinct session/conversation identity before handoff.
> - Prove child ownership and pause/resume/stop with observed evidence.
> - Plan records never schedule; register ticks with a native or verified external scheduler.
> - Unsupported facts must be reported rather than guessed.

## Why this is discovery-only

The supplied source is <https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent>. At review time that exact URL did not serve the stated `badlogic/pi-mono` repository; it resolved to a different owner/repository. Because the canonical primary source cannot be verified, this packet records **no** commands, session flags, scheduler behavior, or lifecycle claims for Pi. Anything recorded here without that verification would be a guess.

## Safe discovery procedure

1. Confirm the canonical upstream repository and owner from an official channel (the maintainer's own site or the repository the supplied URL resolves to), and record the exact URL and retrieval date.
2. Confirm that the package is the intended product named "Pi" and not a different project that shares the name.
3. Read only official product documentation and the canonical repository for that product.
4. Re-run the full capability verification in `docs/agents/harness-verification.md` against an installed version: distinct session/conversation identity, separate CLI invocation, scheduler or verified external scheduler, pause/resume/stop, shell execution, workspace access, and parent/child status reporting.
5. Record every probe and result. Until every required capability is observed, keep the status `discovery-only` and do not delegate.
6. If any step is ambiguous, leave the status unresolved and report it instead of filling the gap by analogy.

## What is not documented (report, do not guess)

- Any spawn, pause, resume, stop, or capability-probe command.
- Any session or conversation identity flag.
- Any scheduler, memory, or background-control claim.

## Safe next step

Treat this page as routing information, not as an adapter. See also [unsupported.md](unsupported.md) for the shared discovery rules, and record `discovery-only` in the capability report.
