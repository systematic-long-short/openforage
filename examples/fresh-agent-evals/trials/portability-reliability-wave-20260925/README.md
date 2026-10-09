# Portability and reliability wave — 2026-09-25

This archive records four offline fixes and one separate native attempt. Earlier verdicts remain unchanged; replays do not regrade them.

## Implemented

1. Bounded combined `ls` flags use observed output; native/shell parity preserves ordinary, recursive, and directory-operand scopes.
2. Audit findings name the causal action, safe command, reason, and correction. Unknown access stays unverified; summaries use canonical verdict dimensions.
3. Public-only isolated contribution smoke in CI, portable Markdown targets, and exact-selection Git initialization preserve ignore-matched tracked files in copied workspaces.
4. Active docs/research graders derive from the canonical contract; the frozen closure includes the smoke helper and applicable public ignore rules.

## Validation

Before archive assembly: all 15 validator modes, 110 scenarios, nine copy cases, both selfchecks, and the public-only smoke passed. The smoke rejects missing completion and covers the tracked-ignore regression. Startup, diff, independent probes, and parent final rerun passed. The parent-authored review record verifies source/log hashes; no separate reviewer prose receipt was produced.

After archive/report additions, `evals`, `links`, `truthfulness`, and `guidance` each returned exit 0; `git diff --check` returned exit 0. Full local check:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python .github/scripts/validate_agent_docs.py all
```

## Separate native observation

The [series manifest](../../v2/series/public-portability-reliability-docs-1.json) records one fresh primary OpenCode 1.18.31 attempt (`public-portability-docs-opencode-1`, `first-finished-contribution-primary`): **PASS**, harness exit 0. [Trial](docs/public-portability-docs-opencode-1/trial.json) · [verdict](docs/public-portability-docs-opencode-1/verdict.json) · [completion](docs/public-portability-docs-opencode-1/completion.json). Entry route passed; baseline validation exited 1, final validation 0 after the quickstart correction. Completion, diff, and independent validation passed. The complete trace has 19 actions/61 events; direct-suite audit: no access.

Requested selectors were `openrouter/openai/gpt-6-luna` and variant `max`; backend-served identity is unverified. One attempt is not a paired harness comparison or universal success. No live search, GA completion, or out-of-sample result is claimed.

## Evidence and preservation

Frozen source: [provenance](source/source-bundle-provenance.json) · [bundle](source/frozen-grading-sources.tar.gz). Prior verdicts: [completion-reliability archive](../completion-reliability-wave-20260925/README.md). Its original FAILs remain FAIL; this PASS is separate, not a retrospective upgrade.
