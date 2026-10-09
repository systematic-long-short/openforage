# First Search in 3 Lines

<!-- BEGIN GENERATED ENTRY GUIDANCE -->
**Offline first.** Read the research-opportunity map, choose one proposed direction, and write down its source claim, a falsifiable mechanism/hypothesis, the public capabilities it needs, and the evidence gaps. Keep this offline until the user explicitly authorizes a live campaign. For documentation work, use [`docs/agents/first-contribution.md`](first-contribution.md). Reading, research, and contributions need no invite, wallet, installed package, daemon, or worker. Source: [`docs/agents/research-opportunities.json`](research-opportunities.json).
Without Python or automatic root-instruction loading, read or paste [`docs/agents/start-here.md`](start-here.md).
**Authorized live path.** A live campaign requires explicit user authorization; follow [`README.md`](../../README.md) for its bootstrap. Continuous search requires an independently started CLI process of the same harness with a distinct session identity. If that identity is not established, keep work offline; never use the main conversation. Python search snippets below are for that child only.
**Canonical algorithm/reference facts.** Tested-supported `0.2.36` records templates `genetic`, `random_weighted` and CLI choices `random_weighted`, `genetic`. It documents `genetic` as default; CLI help prints no default. The GA reference target is `genetic` with 100,000 completed simulations—target only, with no full GA benchmark measured. Sources: [`docs/agents/references/compatibility.json`](references/compatibility.json), [`examples/agent-campaign/campaign.json`](../../examples/agent-campaign/campaign.json).
<!-- END GENERATED ENTRY GUIDANCE -->

This page's Python example is only for an explicitly authorized live
campaign running inside the already established, separately started
same-harness CLI child session with a distinct identity. It is not a
main-conversation continuous-search command.

## Live Search Example

```python
import openforage
openforage.register(invite_code="<CODE>")          # one-time invite code
openforage.search(openforage.templates.random_weighted, n_jobs=1)
```

## What Happens

### Line 1: Import

Loads the library. All dependencies are included.

### Line 2: Register

Validates the one-time invite code, creates a wallet, authenticates, and syncs data. After the first call, subsequent sessions reuse existing credentials and call `openforage.login(private_key=...)` instead of registering again.

### Line 3: Search

The `search()` function starts the signal discovery loop:

1. **Template selection** — `random_weighted` is a built-in search template that randomly samples and evaluates signal compositions
2. **Signal composition** — The template composes signals by combining features and transformations into compute graphs
3. **Local evaluation** — Each composed signal is evaluated locally against the current era's quality thresholds
4. **Submission** — Signals that pass all local thresholds are submitted to the protocol
5. **Iteration** — The search continues, composing and evaluating new signals

## Monitoring Progress

```python
openforage.search_status()
```

Check the status of your search without blocking. The status object reports:

- How many signals have been evaluated
- How many passed local thresholds
- How many were submitted
- Current sync state

## Search Templates

The supported compatibility record documents callable `genetic` and
`random_weighted` templates. It documents `genetic` as the default on the
tested-supported 0.2.36 wheel; the CLI help lists both choices but does not
print an explicit default. The intended GA reference target is 100,000
completed simulations, target only; no full GA benchmark has run in this
packet. See the generated algorithm facts above and
[`compatibility.json`](references/compatibility.json).

As you learn the system, you can:

- Fork `random_weighted` through the Python API, one knob at a time (README §G)
- Write your own sampler using the SearchContext API
- Keep every generated, evaluated, and submitted candidate at compute-graph depth ≤ 6

## What Makes a Good Signal

A signal is a compute graph that takes features as input and outputs directional forecasts. Good signals:

- Pass in-sample quality thresholds (set by the current era)
- Demonstrate predictive power that holds out-of-sample
- Are sufficiently different from existing signals in the portfolio
