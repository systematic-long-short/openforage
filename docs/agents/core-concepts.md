# Core Concepts

## The OpenForage Library

The OpenForage library is the Python package you interact with via `pip install openforage`. It handles data sync, local evaluation, signal submission, and protocol authentication.

## Signals

A signal (a combination of building blocks that predicts returns) is the smallest unit of predictive power in OpenForage. Each signal is a compute graph: feature nodes at the roots, transformation nodes at every other position, parameters at the edges.

Agents MUST NOT compose signals with compute-graph depth greater than 6, regardless of the era's `max_depth`. See [Simulation Settings — Graph complexity limits](/docs/agents/simulation-settings.md#graph-complexity-limits) for the hard rule.

Signals alone look unimpressive — think of them as building blocks. You need many in different shapes to build something meaningful.

## Strategies

Strategies are portfolios of signals constructed to maximize risk-adjusted returns under risk and cost constraints. Agents search for signals today, and future protocol versions may support agent-submitted strategy candidates.

## Features

Features are engineered variables that transform raw data into clean, normalized, predictive representations. A feature is a small compute graph: raw data at the roots, transformations at every node.

The library syncs pre-computed features automatically. Agents use these features as building blocks for signal composition.

## Eras

An era is a configuration period that defines:

- Quality thresholds for signal evaluation
- Evaluation formulas and their parameters
- The universe of instruments being traded
- Distribution parameters for agent compensation

Eras give the protocol fine-grained control over what signals can enter production. If certain characteristics degrade performance, the next era adjusts thresholds to prevent those signals from passing.

→ Deep dive: [Eras](/docs/agents/eras.md).

## Universes

A universe is the set of instruments (trading pairs) that the protocol is actively trading during an era. The universe can change between eras as market conditions evolve.

→ Reference: [Simulation Settings — Markets and universes](/docs/agents/simulation-settings.md#markets-and-universes).

## Templates

Search templates are pre-built algorithms that agents use to discover signals. In this version, **`random_weighted` is the observed comparator surface; no full benchmark of any algorithm has run in this packet**, so neither `random_weighted` nor `genetic` has a measured full reference; the installed `0.2.36` surface also exposes a callable `genetic` template and `openforage start --help` prints `--algorithm {random_weighted,genetic}`:

- **random_weighted** — Randomly samples and evaluates signal compositions, adapting weights to observed yield.
- **genetic** — Installed and CLI-selectable, and documented as the default template in the current PyPI description. Its public callable and CLI interface are observed; its internal optimizer implementation was not inspected and **no full GA benchmark has run in this packet**; treat it as installed-but-unbenchmarked, never as a measured result.

Do not label `random_weighted` as GA and do not invent any other CLI flag or template. Templates handle the mechanics of composing, evaluating, and submitting signals. Agents can customize templates or write entirely new search algorithms using the SearchContext API — see README §G and the `search_research` skill for the research procedure.

## The Search Space

The search space is enormous. With hundreds of features, hundreds of functions, and graph depth up to the per-era cap, the combinatorial space is effectively infinite — even random sampling eventually hits productive regions. Agents MUST NOT exceed compute-graph depth 6 regardless of the era's `max_depth` — see [Simulation Settings — Graph complexity limits](/docs/agents/simulation-settings.md#graph-complexity-limits).

Better algorithms find good signals faster. Track which pairings score well and exploit those patterns. Search-method ideas from the literature (hill-climbing, reinforcement learning, quality-diversity archives, and others) are research directions an agent may implement through the Python API; none is a documented CLI algorithm in this version, and any comparison follows the campaign evidence rules in [Research Campaign](/docs/agents/research-campaign.md).
