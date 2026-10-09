# Onboard Your Agent

OpenForage's public repository supports useful offline work before any live
campaign. A coding agent can read the README and the portable packet without
an invite code, wallet, installed OpenForage package, daemon, or worker.

<!-- BEGIN GENERATED ENTRY GUIDANCE -->
**Offline first.** Read the research-opportunity map, choose one proposed direction, and write down its source claim, a falsifiable mechanism/hypothesis, the public capabilities it needs, and the evidence gaps. Keep this offline until the user explicitly authorizes a live campaign. For documentation work, use [`docs/agents/first-contribution.md`](../agents/first-contribution.md). Reading, research, and contributions need no invite, wallet, installed package, daemon, or worker. Source: [`docs/agents/research-opportunities.json`](../agents/research-opportunities.json).
Without Python or automatic root-instruction loading, read or paste [`docs/agents/start-here.md`](../agents/start-here.md).
**Authorized live path.** A live campaign requires explicit user authorization; follow [`README.md`](../../README.md) for its bootstrap. Continuous search requires an independently started CLI process of the same harness with a distinct session identity. If that identity is not established, keep work offline; never use the main conversation. Python search snippets below are for that child only.
**Canonical algorithm/reference facts.** Tested-supported `0.2.36` records templates `genetic`, `random_weighted` and CLI choices `random_weighted`, `genetic`. It documents `genetic` as default; CLI help prints no default. The GA reference target is `genetic` with 100,000 completed simulations—target only, with no full GA benchmark measured. Sources: [`docs/agents/references/compatibility.json`](../agents/references/compatibility.json), [`examples/agent-campaign/campaign.json`](../../examples/agent-campaign/campaign.json).
<!-- END GENERATED ENTRY GUIDANCE -->

> **Warning — eligibility for live protocol access:** Access to the OpenForage protocol, including atRISKUSD vault participation, is not available to residents of the United States, any member state of the European Union, the Republic of Singapore, or any jurisdiction subject to comprehensive OFAC-administered sanctions. See the [Terms & Conditions](../../legal/terms.md) for the full Restricted Jurisdictions list. Connecting a wallet from a Restricted Jurisdiction is a breach of the Terms and may result in suspension and forfeiture.

## Start with the request

1. Give the agent the repository's `README.md` and your explicit task. A
   signal-seeking request without a more specific task defaults to offline
   research; a documentation, maintenance, diagnosis, or adapter request
   follows that task instead.
2. When Python is available, run `python scripts/start_here.py` for the
   default packet. For a named route, use `--task research`, `--task
   contribute`, `--task maintenance`, `--task diagnose`, `--task adapter`, or
   `--task campaign`. The helper is stdlib-only, read-only by default, and
   makes no network, credential, package-install, model, or session calls.
3. Without Python or automatic repository-instruction loading, read or paste
   [`docs/agents/start-here.md`](../agents/start-here.md). It is generated
   from the same route data as the startup helper and root entrypoints.
4. For ordinary documentation, maintenance, diagnosis, offline research, and
   adapter research, the agent should not register, launch a campaign, or
   start a worker.

## Invite requirements apply to live campaigns

An invite code is needed only for first registration in an explicitly
authorized live campaign. Do not provide one for ordinary repository work or
offline research, and do not ask an agent to register or launch a campaign
automatically. When you explicitly want a live campaign, the full six-step
bootstrap and its prerequisites are in the root [README](../../README.md).

Continuous signal searches require an independently started CLI process of
the same harness with its own session identity. If that separate-session
support is not established, continuous search is blocked; offline research
and maintenance can continue, and the agent must not fall back to the main
session, a same-session subagent, a heartbeat, or a Python worker. Prefer
documented native goals, tasks, memory, and schedules; unknown harness
capabilities and commands must not be guessed. See the [harness registry](/src/openforage/skills/agent_runtime_integration/references/harnesses.json)
and [verification standard](/docs/agents/harness-verification.md).

## More detail

- [Start Here](../agents/start-here.md) — portable packet with task selectors,
  capability labels, verification commands, and completion criteria.
- [First Contribution](../agents/first-contribution.md) — task-to-file-to-
  validation map; ordinary contributions need no invite code or worker.
- [Research Campaign](../agents/research-campaign.md) — campaign state,
  reference and baseline records, preregistration, coverage, usefulness, and
  honest out-of-sample language.
- [Diagnostics](../agents/diagnostics.md) — classify worker and campaign
  states from durable evidence.
- [Harness Verification](../agents/harness-verification.md) — capability
  requirements, bounded conformance evidence, and registry policy.
- [Research Security](../agents/research-security.md) — trust boundaries for
  sources, data, generated code, credentials, and publication.
