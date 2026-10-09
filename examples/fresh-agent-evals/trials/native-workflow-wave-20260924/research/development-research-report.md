# Development-only research comparison

The two exact native-authored `comparison-report.json` files record the same
four development-only pairs on the synthetic fixture. Each pair used four
completed evaluator calls per arm, with equal calls on the same input and seed.
Differences below are copied as reported fields; they are not live performance
or out-of-sample measures.

| Input | Seed | Pair status | `unique_valid_cell_difference` | `synthetic_quality_pass_difference` |
|---|---:|---|---:|---:|
| `development-alpha` | 11 | `complete_equal_calls` | 0 | 1 |
| `development-alpha` | 29 | `complete_equal_calls` | 0 | -1 |
| `development-beta` | 11 | `complete_equal_calls` | 1 | 0 |
| `development-beta` | 29 | `complete_equal_calls` | 2 | -1 |

Source records: OpenCode [authored report](workflow-research-opencode-1/comparison-report.json)
and [original verdict](workflow-research-opencode-1/verdict.json); Codex
[authored report](workflow-research-codex-1/comparison-report.json) and
[original verdict](workflow-research-codex-1/verdict.json). Each verdict's
`independent_report` separately contains six paired comparisons spanning four
development and two reserved-heldout pairs. Those independent comparisons are
inside the verdicts, not in the four-pair authored reports.

The reference is `synthetic_uniform_pair_selector_v1`; it is neither an
OpenForage library measurement nor the `genetic` algorithm. The reports are
descriptive synthetic-fixture observations only. Live performance, server
acceptance, and out-of-sample return correlation remain unknown. No superiority
or harness-only causal claim follows; the harnesses used different requested
model selectors.
