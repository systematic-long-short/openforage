# Contributor reliability wave — local evidence archive

This is the default local entry for the documentation and synthetic-research
trials in the 2026-09-24 contributor-reliability wave. The `docs/` and
`research/` directories retain the parent-verified sanitized exports byte for
byte. `source-snapshots/` retains all four exact grading-source bundles, their
provenance, and their matching immutable series manifests. Raw native streams,
authentication/configuration contents, and private host data are not included.

The separate [background-agent archive](../../../background-agent/contributor-reliability-wave-20260924/README.md)
is linked here and is not duplicated.

## Results at a glance

| Evidence | Original native verdicts | Separate evidence |
| --- | --- | --- |
| Documentation contribution | OpenCode **FAIL**; Codex **FAIL** | Both independently validated final documents **PASS** and have the same exact SHA-256. These are not full contribution-protocol passes. |
| Synthetic research | OpenCode **FAIL**; Codex **FAIL** | Each independent comparison recomputed six paired synthetic comparisons at equal four-completed-call budgets per arm, with mixed synthetic-quality outcomes. This is not a research win or a live-performance result. |

Original verdicts remain as recorded. Focused repairs explain specific grader/evidence defects, but did not regrade or replace any original result.

## Documentation contribution trials

**Series:** `contributor-reliability-docs-1` — manifest SHA-256
`505e31e9b0d56b3e65c477d1ec8270802f017afc6da4eed53fbdf171715bca1d`.
Both runs used the same prepared snapshot,
`14854d82728c1c4a3adf07b226985d8bff5a3b6da3486a8d3e0e070178e18b19`
(368 files; source-tree SHA-256
`b8de481d974cb6e764eee00d173cd61f380d9501232abc112990cad360fe7fde`). See
the [same-input record](docs/same-input.json).

| Run | Original result and diagnosis | Preserved run records and exact contribution |
| --- | --- | --- |
| OpenCode — `reliability-docs-opencode-1` | **FAIL.** The completion reported a chain of three validators plus `git diff --check`, outside the frozen one-validator evidence contract. The final document independently passed. See the [diagnosis](docs/docs-opencode-1-diagnosis.json). | [Trial](docs/reliability-docs-opencode-1/trial.json) · [completion](docs/reliability-docs-opencode-1/completion.json) · [original verdict](docs/reliability-docs-opencode-1/verdict.json) · [exact final document](docs/reliability-docs-opencode-1/final-document.md.txt) · [portable contribution patch](docs/reliability-docs-opencode-1/contribution.patch) · [artifact provenance](docs/reliability-docs-opencode-1/artifact-provenance.json) |
| Codex — `reliability-docs-codex-1` | **FAIL.** The observed outer native shell wrapper contained a nested `bash -lc`, outside the single-wrapper contract; the original verdict also records route, source-read, and causal-validation evidence gaps. The final document independently passed. See the [diagnosis](docs/docs-codex-1-diagnosis.json). | [Trial](docs/reliability-docs-codex-1/trial.json) · [completion](docs/reliability-docs-codex-1/completion.json) · [original verdict](docs/reliability-docs-codex-1/verdict.json) · [exact final document](docs/reliability-docs-codex-1/final-document.md.txt) · [portable contribution patch](docs/reliability-docs-codex-1/contribution.patch) · [artifact provenance](docs/reliability-docs-codex-1/artifact-provenance.json) |

Both exact final documents have SHA-256
`1350e9e5438cc2a47dbac85acfb68528c77a5b89a6451c4120053a0284c3d379`.
Their matching independently validated bytes establish document validity;
they do not erase either original protocol failure. The archive filenames end
in `.md.txt` so the retained artifact is not treated as a live repository page;
the document bytes and recorded hashes are unchanged.

## Synthetic research trials

**Series:** `contributor-reliability-research-1` — manifest SHA-256
`d9a9458b921020f6601a5225790689057e78639e568f0ea838de9c3b027058b4`.
Both runs used prepared snapshot
`a2c7a7844c027e75bc35fdafc583e8ab8df7f262d2bebd2072452471a3d9236a`
(source-tree SHA-256
`b8de481d974cb6e764eee00d173cd61f380d9501232abc112990cad360fe7fde`). See
the [same-input record](research/same-input.json).

| Run | Original result and preserved diagnosis | Authored callback, comparison, and provenance |
| --- | --- | --- |
| OpenCode — `reliability-research-opencode-1` | **FAIL.** The original out-of-allowlist native file-tool read and incomplete native file-path audit remain distinct failures. A later focused diagnosis identified a false report-write attribution for read-only `sha256sum` actions at sequences 55 and 66; the original verdict is unchanged. See the [diagnosis](research/opencode-write-diagnosis.json). | [Trial](research/reliability-research-opencode-1/trial.json) · [original verdict](research/reliability-research-opencode-1/verdict.json) · [exact authored callback](research/reliability-research-opencode-1/challenger.py) · [portable patch](research/reliability-research-opencode-1/contribution.patch) · [research result](research/reliability-research-opencode-1/research-result.json) · [development comparison report](research/reliability-research-opencode-1/comparison-report.json) · [export provenance](research/reliability-research-opencode-1/export-provenance.json) · [run provenance](research/reliability-research-opencode-1/provenance.json) |
| Codex — `reliability-research-codex-1` | **FAIL.** The shared normalizer truncated the declared 311-byte evaluator command to a 300-character preview, so the exact approved-evaluator exception was not established and a protected-source warning followed. `fixture.schema.json` is explicitly allowed; the allowed schema read is not reserved fixture access. See the [diagnosis](research/codex-normalizer-diagnosis.json). | [Trial](research/reliability-research-codex-1/trial.json) · [original verdict](research/reliability-research-codex-1/verdict.json) · [exact authored callback](research/reliability-research-codex-1/challenger.py) · [portable patch](research/reliability-research-codex-1/contribution.patch) · [research result](research/reliability-research-codex-1/research-result.json) · [development comparison report](research/reliability-research-codex-1/comparison-report.json) · [export provenance](research/reliability-research-codex-1/export-provenance.json) · [run provenance](research/reliability-research-codex-1/provenance.json) |

Each linked `comparison-report.json` is a native-produced report containing
four paired development-only runs. The six-pair independent recomputations
across development and reserved-heldout are the `independent_report` objects in
the [OpenCode original verdict](research/reliability-research-opencode-1/verdict.json)
and [Codex original verdict](research/reliability-research-codex-1/verdict.json).
The preserved [pair summary](research/pair-summary.json) is derived from those
objects and reports six paired comparisons per run, with equal
four-completed-call budgets in each arm. The four development unique-cell
deltas are `0, 0, +1, +2` for each run; the synthetic quality deltas are mixed.
These are fixture-only, descriptive
observations. The reference is `synthetic_uniform_pair_selector_v1`, not the
genetic algorithm. The `genetic`/100,000-completed-simulation reference remains
an unmeasured target. Live performance, out-of-sample return correlation, and
server acceptance remain unknown; neither structural coverage nor synthetic
counters imply any of those outcomes.

### Focused repair evidence

The two research grader repairs and their closeouts are retained unchanged:

- [Write-attribution repair closeout](research/packet-research-write-repair-closeout.json)
- [Command-evidence repair closeout](research/packet-command-evidence-repair-closeout.json)
- [Parent action-level replay verification](research/repairs-parent-verification.json)

The parent replay passed the focused command-evidence checks. OpenCode's
current action-level replay correctly leaves its out-of-allowlist read and
incomplete file-path audit findings visible; Codex's replay admits the full
evaluator command and has no access findings. These replays are not new native
runs and do not replace the original FAIL verdicts.

## Immutable source snapshots

Each row links the exact series manifest, tarball, and source-bundle
provenance. The `-1` bundles preserve the source closures used for the
original native verdicts. The corrected `-2` bundles are current-code freezes
for regression and offline checks only; no native trial was run under either
series-2 manifest.

| Series | Frozen source files | Manifest SHA-256 | Bundle SHA-256 / bytes | Exact retained files |
| --- | ---: | --- | --- | --- |
| Docs 1 — original | 73 | `505e31e9b0d56b3e65c477d1ec8270802f017afc6da4eed53fbdf171715bca1d` | `c9c050f6b0e2269899cdf3fe45f00d7cf26d93a113d7fb20b572939c27cec9b5` / 290,414 | [manifest](source-snapshots/docs-1/series-manifest.json) · [tarball](source-snapshots/docs-1/frozen-grading-sources.tar.gz) · [provenance](source-snapshots/docs-1/source-bundle-provenance.json) |
| Docs 2 — current, offline/regression only | 73 | `b2ac2b1454acec526f407cf27415e1f4836bc1cde98df6412c2c797e315ea186` | `c472f99814621b03723700f47eda54717743502e7e4b76dae1fee78f5200f0a4` / 291,542 | [manifest](source-snapshots/docs-2-current/series-manifest.json) · [tarball](source-snapshots/docs-2-current/frozen-grading-sources.tar.gz) · [provenance](source-snapshots/docs-2-current/source-bundle-provenance.json) |
| Research 1 — original | 127 | `d9a9458b921020f6601a5225790689057e78639e568f0ea838de9c3b027058b4` | `22c2cbac172aeddf31d1460981b5efe624ba526f83aaefe181956c96c1add4e1` / 476,693 | [manifest](source-snapshots/research-1/series-manifest.json) · [tarball](source-snapshots/research-1/frozen-grading-sources.tar.gz) · [provenance](source-snapshots/research-1/source-bundle-provenance.json) |
| Research 2 — current, offline/regression only | 127 | `1bfb6b7adf86f0971bb5480eb883b7fa366f67dcd87e02f0886f5a53879aff2e` | `6a65f87eb9be34c330619e6f7edb60b315bb6e71f180e40145667553927e5256` / 482,039 | [manifest](source-snapshots/research-2-current/series-manifest.json) · [tarball](source-snapshots/research-2-current/frozen-grading-sources.tar.gz) · [provenance](source-snapshots/research-2-current/source-bundle-provenance.json) |

## Requested model selectors and comparison limits

The recorded selectors are caller requests only; backend-served model values
were not observed. The same requested pair appears in both workstreams:

| Harness | Requested selector | Other requested setting |
| --- | --- | --- |
| OpenCode | `openrouter/openai/gpt-6-luna` | variant `max` |
| Codex | `gpt-6-astra` | reasoning effort `medium` |

The requested models differ, so these pairs do **not** establish harness-only
causality. The native protocol verdicts and the synthetic research comparisons
are separate outcomes: neither a protocol pass nor a synthetic comparison
alone proves a research win.

## Scope

This archive preserves sanitized evidence and does not include raw native
transcripts. Original outcomes were not rewritten or regraded. No current
series-2 native run, GA run, live library action, out-of-sample evaluation,
return-correlation measurement, or server-acceptance result is represented.
