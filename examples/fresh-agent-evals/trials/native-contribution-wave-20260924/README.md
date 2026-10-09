# Native contribution wave — 2026-09-24 archive

This is a sanitized archive of two actual native primary-case series. Each
run directory contains the runner-produced sanitized `trial.json` and
unchanged original `verdict.json`, plus the parent-verified completion export,
derived contribution patch, and provenance record. Raw native/provider streams
were not copied; the trial records retain only relative ignored-scratch
references and digests. The exact requested model/variant/reasoning values are
recorded as argv requests; backend-served model identity was not independently
observed.

## Series history

The historical [`series.json`](../../v2/series.json) and named
[`contribution-wave-final-1`](../../v2/series/contribution-wave-final-1.json),
[`contribution-wave-final-2`](../../v2/series/contribution-wave-final-2.json)
manifests are immutable **pre-native drafts** with no native contribution
subjects. Final-1 was followed by a grader
missing-import correction; final-2 was followed by explicit fixture permission
defaults. They remain drafts; their manifest statuses are not rewritten.

[`contribution-wave-final-3`](../../v2/series/contribution-wave-final-3.json)
was the first actual native contribution series. Its OpenCode and Codex
subjects shared the same prepared source snapshot and initial file map, both
produced the correct quickstart correction and completion artifact, and both
strict original verdicts failed. Their records remain in `series3/` unchanged.
The strict failures are not regraded.

[`contribution-wave-final-4`](../../v2/series/contribution-wave-final-4.json)
is the selected actual series (manifest SHA-256
`2a55a997dd58961bc7b8d2d766fb396fac4aedbf1e34fe77012e4e35445a97fa`). It is a
fresh pair, not a regrade of final-3. The final-4 frozen closure includes the
causal-phase correlation correction, the explicit-read provenance requirement,
and narrow recognition of exact JSON artifact parsing and current-branch
queries. It does not admit arbitrary, complex, or truncated commands.

## Results

| Series / run | Requested model selector | Strict original verdict | Key result and limitation |
|---|---|---|---|
| final-3 `opencode-primary-2` | OpenCode `1.18.31`; `openrouter/openai/gpt-6-luna`, variant `max` | **FAIL** | Correct artifact and native selected check / independent validation passed. Audit remained unknown for JSON parsing and the claimed `AGENTS.md` read; automatic instruction loading was not proof of an explicit read. First meaningful action `12.619s`; verified completion time unknown. |
| final-3 `codex-primary-1` | Codex `0.156.1`; `gpt-6-astra`, reasoning `medium` | **FAIL** | Correct artifact and selected check / independent validation passed. Audit remained unknown for current-branch and complex/quoted/truncated commands; the original baseline matcher saw `all` before and after the edit and could not correlate the causal phase. The post-edit `all` check honestly failed on the preexisting `examples/codex-lifecycle/run_fixture.py` selfcheck in its sandbox; selected truthfulness+links passed. Time measurements unavailable. |
| final-4 `opencode-primary-3` | OpenCode `1.18.31`; `openrouter/openai/gpt-6-luna`, variant `max` | **FAIL** | Native exit 0, no timeout, process group gone, final document hash matches the clean authoritative document, and independent truthfulness+links validation exited 0. The strict verdict leaves audit and agent baseline/final correlation unknown because it rejects the environment-prefixed baseline and compound final command. Parent inspection of native events found the baseline command exited 1 before the edit and the final command exited 0 afterward; that observation does not change the original strict verdict. First meaningful action `10.642s`; verified completion time unknown. |
| final-4 `codex-primary-2` | Codex `0.156.1`; `gpt-6-astra`, reasoning `medium` | **PASS** | No strict failures; native exit 0, no timeout, owned group gone, baseline failure/final pass independently correlated, independent final validation passed, and bounded audit was `complete_no_direct_access`. The retained final-4 record claims the selected truthfulness+links check; it has no `all`-mode result. `59.588s` is elapsed-at-verification **upper bound**, not exact completion time. First-action time unavailable. |

For final-3, both subjects share prepared snapshot
`97ca7aff9fb68e32994d8d2bb39ee3c28036e628c65aa7e12173a71fb542dcaa`, source
tree `574f1b86cd7e7b88dd069f326b55004cf97175ccca83af780ab03705476c38a8`, and
matching initial file maps. Final-4 subjects share snapshot
`4c76ad2919ef815e6e5ce53cd318b24acbb1c9f6fc0d83ed0ba26593667e7530`, source
tree `67bdbc2e3702427a5a8bb9578ed65606e4595fe9950d8c3acf678beeeb1c50b4`, and
matching initial file maps; Codex was pinned to OpenCode's snapshot. The
[`final-4 same-input record`](series4/same-input.json) preserves that check.
The requested models differ, and a successful-only paired comparison is not
admitted because one final-4 strict verdict failed. These observations do not
support a harness-only ranking.

Both harnesses changed only `docs/agents/quickstart.md` and created
`completion.json`. The final-4 correction matches the clean authoritative
quickstart hash. Structured metrics and human prose are separate: prose has no
automated quality assessment or claimed independent human editorial grade.
Zero clarification observations reflect the single-turn fixture restrictions,
not general harness capability. Unnecessary setup counts remain unknown where
the traces do not establish a complete count.

## Scope and held-out case

The primary case is public and instruction-isolated, not an OS-sandbox security
proof. No run registered, searched, downloaded data, evaluated, simulated, or
submitted OpenForage work. No research signal, out-of-sample measure, or
benchmark result is reported. The intended genetic target of 100,000 completed
simulations remains a target only.

The held-out case `first-finished-contribution-heldout` was prepared and its
real baseline validator was checked during implementation. It remains unused
by **NATIVE MODEL TRIAL** subjects. That implementation preflight is not a
held-out native trial; the case is not wholly unexamined or a pristine blind
fixture.

## Evidence layout

- `series3/<run-id>/trial.json` and `verdict.json`: original runner records;
  completion, patch, and provenance exports are adjacent.
- `series3/same-input.json`: parent-verified shared snapshot/map facts for the
  failed strict pair.
- [`series3/frozen-grading-sources.tar.gz`](series3/frozen-grading-sources.tar.gz)
  and [`series4/frozen-grading-sources.tar.gz`](series4/frozen-grading-sources.tar.gz):
  exact SHA-256-verified provenance bundles for final-3 and final-4, each
  containing only its 53 repository-relative public grading-source files plus
  the selected manifest. The adjacent `source-bundle-provenance.json` files
  record each bundle digest and scope. These inert archives preserve the
  original grading code; they are not extracted into the active source tree,
  imported by normal validation, or used as duplicate active implementations.
  They contain no native raw streams or private host paths.
- [`prelaunch-attempts.json`](prelaunch-attempts.json): three preserved
  prelaunch failures. They did not reach native model sessions and are not
  contribution verdicts.
- [Background-agent native proof archive](../../../background-agent/native-contribution-wave-20260924/README.md):
  separate lifecycle proof for the background adapter; it does not prove live
  OpenForage work.

The three routes stay distinct: `scripts/research_next.py` is a read-only
next-action proposal; `scripts/background_agent.py` owns a separate same-harness
session and requires an explicitly chosen delivery mode (manual by default,
external interval only when selected); `examples/fresh-agent-evals/v2/runner.py`
is the versioned completed-contribution evaluation. None substitutes for live
campaign work or for another route's evidence.
