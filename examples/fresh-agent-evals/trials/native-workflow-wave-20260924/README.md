# Native workflow follow-up — 2026-09-24 archive

This archive preserves the exact supplied exports for four completed first-
attempt native runs: OpenCode and Codex on the documentation task, and OpenCode
and Codex on the synthetic research task. Trial, original verdict, provenance,
contributed-artifact, and frozen-source bytes are copied unchanged. No original
verdict was edited or regraded. Raw native/model streams and private parent
control or audit records are not included. Root first-read guidance is unchanged
by this archive.

All four entry routes passed. The **original protocol verdicts** were one
PASS and three FAILs; independent artifact or comparison evidence is reported
separately and does not upgrade a protocol verdict.

## Scope, versions, and paired inputs

Both suites use the `entrypoint-first-v1` entry-route contract and the
`first-finished-contribution-primary` documentation case or
`paper-backed-coverage-challenger` research case.

The documentation fixture is `docs/agents/quickstart.md`, seeded SHA-256
`8aa6d00c03210614aa655f3bce19d7afc89ded1ddd1344e3f9dba45550b39d5a`. The
research fixture is `paper-backed-coverage-fixture-v1`, SHA-256
`6baefe26ad0c14a741de14da835d7439d40ab891a6119381a3f89c4acfbd1b4f`. Both
same-input records retain source-tree SHA-256
`2a37337d07bb9d149056d898ac600c6bcef610553922305d9ce3d596f176372f`.

| Suite | Series / manifest SHA-256 | Snapshot SHA-256 | Same-input evidence |
|---|---|---|---|
| Documentation | `native-workflow-docs-1` / `d6655322dbfb9eb13c3a96671aa83d9de7d9a2a04091cfb07e4c712b698ccb3d` | `038800bac345df369c42f7c0245edf1e69d1bd391138aa1bae31ee82a3005786` | [same-input record](docs/same-input.json) |
| Synthetic research | `native-workflow-research-1` / `19b6297970155929ee928a4197c24105578138bfeb23044e0f1d16c4a1090ab3` | `e662d6274067d652b10685b0153348d71c177e4e33f269d414035f959b0dbfcf` | [same-input record](research/same-input.json) |

Within each suite, the record confirms equal initial file maps and that Codex
was pinned to the OpenCode snapshot. The requested model selectors differ
between harnesses, so these observations do not support a harness-only causal
ranking. Selector values are CLI requests only; backend-served model identity
was not observed.

| Harness | Version | Requested model | Other requested setting |
|---|---|---|---|
| OpenCode | `1.18.31` | `openrouter/openai/gpt-6-luna` | variant `max` |
| Codex | `0.156.1` | `gpt-6-astra` | reasoning effort `medium` |

## Per-run outcomes

| Run | First-read route evidence | Original protocol verdict | Independent result |
|---|---|---|---|
| Documentation — OpenCode | PASS; see [route evidence](docs/workflow-docs-opencode-1/verdict.json) | **PASS**; no failure reasons or structured diagnostics | Independent `truthfulness` + `links` validation **PASS**; exact two-file contribution diff |
| Documentation — Codex | PASS; see [route evidence](docs/workflow-docs-codex-1/verdict.json) | **FAIL**; original diagnostics include `protocol.requirement_unmet` and `artifact.schema_or_content_invalid` | Independent `truthfulness` + `links` validation **PASS**; final workspace diff had an extra `trial-output/context.txt` |
| Research — OpenCode | PASS; see [route evidence](research/workflow-research-opencode-1/verdict.json) | **FAIL**; `protocol.requirement_unmet` for a native directory read outside the content-read allowlist | Independent report in the verdict: six equal-call synthetic pairs **complete**; contributed report: four development pairs |
| Research — Codex | PASS; see [route evidence](research/workflow-research-codex-1/verdict.json) | **FAIL**; `protocol.requirement_unmet` because the bounded shell-inspection audit was classified as unknown | Independent report in the verdict: six equal-call synthetic pairs **complete**; contributed report: four development pairs |

### First-read observations

Sequence numbers below are native observations retained in the original trial
records/verdicts. They describe content reads and the task selector, not prompt
text alone.

| Run | Root reads | Task selector | Task-route reads |
|---|---|---|---|
| Docs OpenCode | `README.md` 5; `AGENTS.md` 8 | `contribute` 11 | `docs/agents/first-contribution.md` 14 |
| Docs Codex | `README.md` and `AGENTS.md` in the command completing at 4 | `contribute` 6 | `docs/agents/first-contribution.md` 8 |
| Research OpenCode | `README.md` 1; `AGENTS.md` 7 | `research` 10 | research `README.md` 16; `task.md` 19 |
| Research Codex | `README.md` and `AGENTS.md` in the command completing at 4 | `research` 6 | research `README.md` and `task.md` in the command completing at 8 |

### Structured findings and audit interpretation

- **Docs OpenCode — PASS.** Its original verdict has no diagnostics. The
  independent final-document validator passed `truthfulness` and `links`; the
  diff contains the corrected quickstart and `trial-output/completion.json`.
- **Docs Codex — FAIL.** The original `protocol.requirement_unmet` cites direct
  evaluation case/oracle/grader access. The audit review traced that flag to
  exclusion-only filename discovery (`rg --files` for `AGENTS.md`, explicitly
  excluding `examples/fresh-agent-evals/**` and `trial-output/**`): this is an
  audit false positive, not evidence that protected evaluation contents were
  read. Separately, the original artifact diagnostic records three changed
  paths because `trial-output/context.txt` was created in addition to the
  corrected quickstart and completion file. The independent document
  validation still passed; the original protocol verdict remains FAIL.
- **Research OpenCode — FAIL.** The recorded native action at sequence 37 was a
  `read` of the `trial-output` directory, not a specific content file. The
  parent audit treats this as a native directory-read compatibility limitation;
  it does not allege that protected evaluation contents were read. The original
  verdict also includes an informational `research.comparison_outcome`
  diagnostic: the synthetic comparison completed, while live and
  out-of-sample evidence remain unknown.
- **Research Codex — FAIL.** The original protocol diagnostic says the bounded
  shell-inspection audit was unknown. The audit review identified the relevant
  action as a 375-byte `cat` command that exited 1 at event 9/completion 10
  after naming the mistaken, nonexistent
  `examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/source-provenance.json`.
  The permitted source is `examples/fresh-agent-evals/research/source-provenance.json`.
  The full command was retained, not clipped; this path error is not evidence
  that the missing file's contents were read. The verdict also has an
  informational `research.comparison_outcome` diagnostic. Its original FAIL
  remains unchanged.

These records distinguish protocol, artifact, path/format, directory-read
compatibility, and comparison findings. Both residual research audit limits
remain visible. Native-tool compatibility and attribution improved relative to
the earlier original failures, but these residual limits remain; this
follow-up does not claim all subjects passed.

## Evidence index

| Evidence | Documentation suite | Research suite |
|---|---|---|
| Exact frozen grading source bundle | [tarball](docs-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](docs-frozen-sources/source-bundle-provenance.json) | [tarball](research-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](research-frozen-sources/source-bundle-provenance.json) |
| OpenCode run | [trial](docs/workflow-docs-opencode-1/trial.json) · [original verdict](docs/workflow-docs-opencode-1/verdict.json) · [artifact provenance](docs/workflow-docs-opencode-1/artifact-provenance.json) · [patch](docs/workflow-docs-opencode-1/contribution.patch) · [exact document](docs/workflow-docs-opencode-1/final-document.md.txt) · [completion](docs/workflow-docs-opencode-1/completion.json) | [trial](research/workflow-research-opencode-1/trial.json) · [original verdict](research/workflow-research-opencode-1/verdict.json) · [run provenance](research/workflow-research-opencode-1/provenance.json) · [export provenance](research/workflow-research-opencode-1/export-provenance.json) · [patch](research/workflow-research-opencode-1/contribution.patch) · [challenger](research/workflow-research-opencode-1/challenger.py) · [research result](research/workflow-research-opencode-1/research-result.json) · [authored comparison](research/workflow-research-opencode-1/comparison-report.json) |
| Codex run | [trial](docs/workflow-docs-codex-1/trial.json) · [original verdict](docs/workflow-docs-codex-1/verdict.json) · [artifact provenance](docs/workflow-docs-codex-1/artifact-provenance.json) · [patch](docs/workflow-docs-codex-1/contribution.patch) · [exact document](docs/workflow-docs-codex-1/final-document.md.txt) · [completion](docs/workflow-docs-codex-1/completion.json) | [trial](research/workflow-research-codex-1/trial.json) · [original verdict](research/workflow-research-codex-1/verdict.json) · [run provenance](research/workflow-research-codex-1/provenance.json) · [export provenance](research/workflow-research-codex-1/export-provenance.json) · [patch](research/workflow-research-codex-1/contribution.patch) · [challenger](research/workflow-research-codex-1/challenger.py) · [research result](research/workflow-research-codex-1/research-result.json) · [authored comparison](research/workflow-research-codex-1/comparison-report.json) |
| Development research summary | — | [four-pair report](research/development-research-report.md); each verdict's `independent_report` contains the separate six-pair development/held-out comparison |

The exact contributed documentation is named `final-document.md.txt` so it is
preserved as detached artifact bytes rather than treated as a new repository
page. Earlier original failures remain immutable; see the [contributor
reliability archive](../contributor-reliability-wave-20260924/README.md) and
[native contribution archive](../native-contribution-wave-20260924/README.md)
for historical context.

## Limits

The research evidence is synthetic fixture output. Its reference is
`synthetic_uniform_pair_selector_v1`, not OpenForage's genetic algorithm and
not a library measurement. Prototype completion is not a live GA benchmark,
OpenForage library performance, or an out-of-sample correlation result. Live
performance, server acceptance, and out-of-sample return correlation remain
unknown. The intended `genetic` target of 100,000 completed simulations remains
an unmeasured target.
