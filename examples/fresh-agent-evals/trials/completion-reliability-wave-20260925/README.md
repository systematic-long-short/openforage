# Completion-reliability wave — 2026-09-25

This archive records fixes to native-evidence recognition and completion grading: OpenCode envelope and paginated-read handling; research-manifest version validation; consistent repair/recheck instructions; and a clear boundary between the disclosed completion schema and protected grader/oracle material. Completion metadata is separated from contributor `changed_paths`, and root-file reads count only when all required continuations are followed.

## Latest first attempts

Series 4 is the final four-run native series: **3 PASS, 1 FAIL**. All four entry routes passed. Both documentation attempts passed their final content validators, `git diff --check`, and completion checker. The OpenCode documentation verdict remains FAIL because its direct-suite access audit is unknown: combined `-la` is outside the current bounded shell grammar. The [trial](docs/complete-docs-opencode-4/trial.json) records `ls -la` at action 15, and the [verdict](docs/complete-docs-opencode-4/verdict.json) reports the audit as unknown. This is an audit-grammar limitation, not evidence of protected access or a contributor failure. Codex documentation and both research attempts passed.

Older first-attempt counts remain historical and unchanged: Series 1 **1/4 PASS**, Series 2 **1/4 PASS**, Series 3 **3/4 PASS**. The four series contain 16 original first attempts. No later replay, source repair, or independent check replaces or upgrades an original verdict.

## Four-run first-attempt record

`First read` is recorded separately from the overall original verdict. Each row links the exported trial and verdict. Research report links point to the authored development report; the verdict contains the separate independent recomputation.

### Series 1 — initial completion evidence (1/4 PASS)

| Run | First read | Original result | Evidence and observed outcome |
|---|---|---|---|
| Docs · OpenCode | FAIL | [FAIL](series-1/docs/complete-docs-opencode-1/verdict.json) | [Trial](series-1/docs/complete-docs-opencode-1/trial.json): required root and authoritative-source reads were completed, but the frozen adapter did not recognize OpenCode's native read envelopes; the FAIL reflects missing recognized evidence, not omitted reads. |
| Docs · Codex | PASS | [PASS](series-1/docs/complete-docs-codex-1/verdict.json) | [Trial](series-1/docs/complete-docs-codex-1/trial.json): entry route and task-specific source reads were observed. |
| Research · OpenCode | FAIL | [FAIL](series-1/research/complete-research-opencode-1/verdict.json) | [Trial](series-1/research/complete-research-opencode-1/trial.json) · [development report](series-1/research/complete-research-opencode-1/comparison-report.json): the frozen evaluator incorrectly rejected the checker manifest version and did not recognize OpenCode native read envelopes; the FAIL reflects evaluator defects, not failure to execute valid instructions. |
| Research · Codex | PASS | [FAIL](series-1/research/complete-research-codex-1/verdict.json) | [Trial](series-1/research/complete-research-codex-1/trial.json) · [development report](series-1/research/complete-research-codex-1/comparison-report.json): the frozen evaluator incorrectly rejected the checker manifest version; the FAIL reflects an evaluator defect, not failure to execute valid instructions. |

Paired input records: [docs](series-1/docs/same-input.json) · [research](series-1/research/same-input.json).

### Series 2 — checker and recheck ordering (1/4 PASS)

| Run | First read | Original result | Evidence and observed outcome |
|---|---|---|---|
| Docs · OpenCode | PASS | [FAIL](series-2/docs/complete-docs-opencode-2/verdict.json) | [Trial](series-2/docs/complete-docs-opencode-2/trial.json): the post-repair recheck succeeded, but the frozen grader rejected earlier/final `--check-completion` and `git diff --check` attempts as duplicates; the original FAIL remains. |
| Docs · Codex | PASS | [FAIL](series-2/docs/complete-docs-codex-2/verdict.json) | [Trial](series-2/docs/complete-docs-codex-2/trial.json): final repair was not followed by a recheck; the `stop after check` prompt contributed, but the original unfinished-evidence FAIL remains. |
| Research · OpenCode | FAIL | [FAIL](series-2/research/complete-research-opencode-2/verdict.json) | [Trial](series-2/research/complete-research-opencode-2/trial.json) · [development report](series-2/research/complete-research-opencode-2/comparison-report.json): required entry and research-route reads were incomplete. |
| Research · Codex | PASS | [PASS](series-2/research/complete-research-codex-2/verdict.json) | [Trial](series-2/research/complete-research-codex-2/trial.json) · [development report](series-2/research/complete-research-codex-2/comparison-report.json): first read and required native completion evidence passed. |

The completion and recheck directions in this source series were contradictory; later source revisions aligned the permitted order, outcome evidence, and grading. Paired input records: [docs](series-2/docs/same-input.json) · [research](series-2/research/same-input.json).

### Series 3 — disclosed-schema access contradiction (3/4 PASS)

| Run | First read | Original result | Evidence and observed outcome |
|---|---|---|---|
| Docs · OpenCode | PASS | [FAIL](series-3/docs/complete-docs-opencode-3/verdict.json) | [Trial](series-3/docs/complete-docs-opencode-3/trial.json): grader marked a completed read of the disclosed `completion.schema.json` as direct evaluation-suite access; final artifact, completion checker, and diff check passed. The original FAIL is retained. |
| Docs · Codex | PASS | [PASS](series-3/docs/complete-docs-codex-3/verdict.json) | [Trial](series-3/docs/complete-docs-codex-3/trial.json): route and completion evidence passed. |
| Research · OpenCode | PASS | [PASS](series-3/research/complete-research-opencode-3/verdict.json) | [Trial](series-3/research/complete-research-opencode-3/trial.json) · [development report](series-3/research/complete-research-opencode-3/comparison-report.json). |
| Research · Codex | PASS | [PASS](series-3/research/complete-research-codex-3/verdict.json) | [Trial](series-3/research/complete-research-codex-3/trial.json) · [development report](series-3/research/complete-research-codex-3/comparison-report.json). |

The schema contradiction was resolved separately without changing this verdict. Independent final probes established that valid shell-quoted access to the explicitly disclosed schema is allowed; 24 protected transformations remained blocked, and replay against the original workspace matched the retained actions exactly. The six initial review findings based on rejecting those valid disclosed-schema reads were incorrect expectations. The independent reviewer reached both allotted CLI endpoints without the separately requested summary receipt; the parent accepted the exact final probe, native exits, and source hashes in `parent-schema-review-handoff.json`. No worker summary receipt was created. Paired input records: [docs](series-3/docs/same-input.json) · [research](series-3/research/same-input.json).

### Series 4 — final completion contract (3/4 PASS)

| Run | First read | Original result | Evidence and observed outcome |
|---|---|---|---|
| Docs · OpenCode | PASS | [FAIL](docs/complete-docs-opencode-4/verdict.json) | [Trial](docs/complete-docs-opencode-4/trial.json) · [completion](docs/complete-docs-opencode-4/completion.json): content validation, final checker, and diff check passed; only the direct-suite audit remains unknown. |
| Docs · Codex | PASS | [PASS](docs/complete-docs-codex-4/verdict.json) | [Trial](docs/complete-docs-codex-4/trial.json) · [completion](docs/complete-docs-codex-4/completion.json): entry route, complete root reads, validators, final checker, diff check, and audit passed. `contribution.changed_paths` lists the document only; the separate artifact diff also records `trial-output/completion.json`. |
| Research · OpenCode | PASS | [PASS](research/complete-research-opencode-4/verdict.json) | [Trial](research/complete-research-opencode-4/trial.json) · [development report](research/complete-research-opencode-4/comparison-report.json) · [research result](research/complete-research-opencode-4/research-result.json): first read and native completion evidence passed. |
| Research · Codex | PASS | [PASS](research/complete-research-codex-4/verdict.json) | [Trial](research/complete-research-codex-4/trial.json) · [development report](research/complete-research-codex-4/comparison-report.json) · [research result](research/complete-research-codex-4/research-result.json): first read and native completion evidence passed. |

Paired input records: [docs](docs/same-input.json) · [research](research/same-input.json). In all four series, paired initial file maps match but requested model selectors differ. Served model identity is unknown; there is no harness-only causal ranking.

## Frozen grading sources

Each tarball is an exact frozen grading-source bundle; its provenance record gives the selected manifest hash, bundle hash, file count, and scope. These bundles contain no raw native streams.

| Series | Docs source bundle | Research source bundle |
|---|---|---|
| 1 | [tarball](series-1/docs-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-1/docs-frozen-sources/source-bundle-provenance.json) | [tarball](series-1/research-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-1/research-frozen-sources/source-bundle-provenance.json) |
| 2 | [tarball](series-2/docs-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-2/docs-frozen-sources/source-bundle-provenance.json) | [tarball](series-2/research-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-2/research-frozen-sources/source-bundle-provenance.json) |
| 3 | [tarball](series-3/docs-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-3/docs-frozen-sources/source-bundle-provenance.json) | [tarball](series-3/research-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](series-3/research-frozen-sources/source-bundle-provenance.json) |
| 4 | [tarball](docs-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](docs-frozen-sources/source-bundle-provenance.json) | [tarball](research-frozen-sources/frozen-grading-sources.tar.gz) · [provenance](research-frozen-sources/source-bundle-provenance.json) |

## Verification and limits

The final source checks completed before narrative assembly: all 15 documentation validator modes and 110 self-test scenarios passed. Earlier source fixes also corrected OpenCode envelope/pagination recognition, research-manifest version validation, the disclosed-schema access contradiction, completion-metadata treatment, and continuation-aware root-read evidence. Historical verdicts remain byte-preserved. The public candidate remains local and uncommitted.

Research is synthetic fixture evidence only. Its uniform reference is not the OpenForage genetic algorithm; the intended `genetic` comparison target remains 100,000 completed simulations and is unmeasured here. Construction coverage does not measure out-of-sample return correlation, financial performance, or server acceptance. No live search or submission occurred. See the [development research note](research/development-research-report.md) for the exact Series 4 paired values and paper scope.
