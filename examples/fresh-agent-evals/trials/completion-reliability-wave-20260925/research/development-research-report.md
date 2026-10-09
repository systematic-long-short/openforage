# Series 4 synthetic research note

## Question and source scope

The two first-attempt research callbacks tested whether breadth-first proposal scheduling across eligible feature × rotation cells could reach more distinct valid construction cells than `synthetic_uniform_pair_selector_v1` under equal completed-call targets. The reading input was a frozen excerpt from Mouret and Clune, “Illuminating search spaces by mapping elites” (2015, [arXiv:1504.04909](https://arxiv.org/abs/1504.04909)); the excerpt is [retained here](../../../../../examples/fresh-agent-evals/fixtures/challenger-research/trial-inputs/primary-source-excerpt.md) (SHA-256 `08ecc9767bf0f37055bbda96999dc97c80a86918038ef3ca833b5b3367bd7373`). This was a bounded paper-backed mechanism read, not a full literature review.

The callback uses the paper’s descriptor-map idea as motivation: it cycles proposals across eligible cells, varies starting order by seed, and suppresses duplicate payloads. It does not use evaluator fitness to maintain an elite archive, so it is a coverage-oriented scheduling adaptation, not a MAP-Elites implementation. The evaluator and report are synthetic-fixture-only.

## Measured paired results

All development pairs were complete and equal-call: four evaluator calls per arm per pair. The independent report in each exact verdict adds two reserved-heldout synthetic-fixture pairs to the four development pairs. Deltas below are challenger minus the synthetic uniform reference; `cells` means unique valid feature × rotation construction cells, not financial performance.

| Attempt | Development pairs: cell deltas (mean) | Development quality-pass deltas | Independent six-pair cell deltas (mean) | Independent quality-pass deltas |
|---|---:|---:|---:|---:|
| OpenCode | `0, +1, +1, +2` (`+1.00`) | `+1, 0, +1, -1` | `0, +1, +1, +2, +1, 0` (`+5/6`) | `+1, 0, +1, -1, -1, 0` |
| Codex | `0, 0, +1, +2` (`+0.75`) | `+1, -1, 0, -1` | `0, 0, +1, +2, 0, 0` (`+1/2`) | `+1, -1, 0, -1, 0, 0` |

Exact evidence: OpenCode [development comparison](complete-research-opencode-4/comparison-report.json), [independent six-pair report and original verdict](complete-research-opencode-4/verdict.json), [source reading, hypothesis and interpretation](complete-research-opencode-4/research-result.json); Codex [development comparison](complete-research-codex-4/comparison-report.json), [independent six-pair report and original verdict](complete-research-codex-4/verdict.json), [source reading, hypothesis and interpretation](complete-research-codex-4/research-result.json). These are separate callbacks and reports, not a harness ranking: their requested model selectors differ, and served identity is unknown.

## Interpretation boundary

These small descriptive fixture results show positive mean construction-cell differences in this sample and mixed quality-pass differences. They establish neither statistical superiority nor generalization. The reference is uniform synthetic selection, not OpenForage or its GA. No real library run, out-of-sample return correlation, server acceptance, live performance, or financial result was measured. The 100,000-completed-simulation `genetic` target remains unmeasured. Feature × rotation coverage must not be interpreted as return correlation.
