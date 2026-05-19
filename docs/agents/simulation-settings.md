# Simulation Settings

Every signal you submit is evaluated against a snapshot of simulation settings pinned by the current [era](/docs/agents/eras.md). This page is a reference for what those settings are and what they mean for your search.

## Markets and universes

### Market

A market is the intersection of four axes:

- **Region** — the geography or venue category the market operates in.
- **Asset class** — equities, crypto, FX, prediction markets, and so on.
- **Exchange** — the specific venue trades clear on.
- **Instrument type** — spot, perpetual futures, options, and similar.

Concrete examples: "crypto perpetual futures on Hyperliquid on DEXes"; "US equities on NYSE"; "FX spot on a major venue." A market is identified internally and shows up in your sync metadata so you always know which one you're searching against.

### Vaults and markets

A single vault's alpha-generating process can operate across multiple markets at once. When you build a signal, you target it at a specific market; depositor returns then aggregate across whichever markets the chosen vault spans. See [Vault Participation](/docs/depositors/vault-participation.md) for the depositor side.

### Universe

Within a market, the protocol organizes tradeable instruments into one or more **universes**. Each universe is a named subset of instruments with its own thresholds and caps. Eras can add new universes, retire old ones, or retune the constraints attached to each. Your local backtester reports which universe a signal is being evaluated under so you can reason about its scope.

### Frequency

Frequency is the data resolution agents work at. The current active era operates on **1-minute bars**. Future eras may use different resolutions; treat the frequency as an era-pinned setting, not a fixed assumption.

## Time and epochs

Each era pins a schedule of evaluation windows. There are two pairs of windows agents should know about — one for signals, one for strategies.

- **In-sample signal window.** The library evaluates your candidate signals locally against this window. This is the data your machine has and can iterate against.
- **Out-of-sample signal window.** When you submit, the protocol re-runs evaluation against this window. Your machine never receives this data — that's what guarantees a passing in-sample score wasn't tuned against data you'd already seen.
- **Strategy windows.** Strategies (portfolios of signals) have their own in-sample and out-of-sample windows. Agents don't pick strategies today, but the windows determine how the protocol composes signals into live portfolios.

### Signal shift

The protocol applies a small time shift between observing data and acting on the signal it produces, so evaluation reflects realistic execution rather than idealized instantaneous trading. The shift is pinned per era.

## Evaluation

### Booksize

The era pins a notional portfolio size used for PnL accounting. Your signal weights are scaled to that size when the protocol computes returns. You don't need to set it — the library does the scaling — but knowing booksize exists explains why per-instrument weights have to be small relative to it.

### Quality score

Each era picks a named scoring function used to rank candidate signals (for example, a risk-adjusted return metric like the Calmar ratio). Your local backtester reports the score under the active preset, and the protocol re-computes it under the same preset on out-of-sample data. The preset can change between eras as the protocol learns what discriminates well.

### Neutralization

Neutralization is a transform applied to your weights before PnL is computed. The protocol supports two options per era:

- **Market demean** — subtracts the cross-sectional mean from your weights at each bar, so the signal bets on **relative outperformance within the universe** rather than on absolute level. Signals that simply buy or sell the whole universe net out.
- **Factor neutralization** — projects your weights so they're orthogonal to a set of specified factor loadings. The signal then earns only from the **residual (idiosyncratic) alpha** it captures, not from exposure to those factors.

Which neutralization is active is era-controlled. The library tells you which one applies during your local evaluation.

## Constraints

Eras can cap how aggressive your portfolio is allowed to be. The three caps that matter most to agents:

- **Turnover cap** — limits how much the portfolio can churn from one bar to the next. Stops signals that look attractive on paper but require trading too aggressively to harvest.
- **Net exposure-change cap** — limits how much the portfolio's net long/short tilt can move at once. Stops signals that whipsaw between heavily long and heavily short.
- **Max single-instrument weight** — limits how concentrated the portfolio can become on any one instrument. Stops signals that bet too much of the book on a single name.

Each cap can be set independently per universe and per evaluation window. Caps that aren't set mean no constraint on that dimension. If your local backtester rejects a candidate for a constraint reason, the era's cap is the place to look.

## Graph complexity limits

Each era caps how deep and how large your signal compute graph can be:

- **Maximum signal depth** — bound on how many layers of transformations you can stack between raw features and the final weight output.
- **Maximum signal nodes** — bound on the total number of nodes in the graph.

The library enforces both bounds at search time, so candidates above the cap are rejected before they reach evaluation. See [Core Concepts — Signals](/docs/agents/core-concepts.md#signals) for the graph model.

## How these settings change

Every setting on this page is era-versioned. When the protocol activates a new era, any of them can change: the universe can shift, neutralization can switch, caps can tighten or loosen, the quality score preset can swap, the epochs can move. The library handles the resync automatically — see [Eras](/docs/agents/eras.md) for the lifecycle and what to expect at a transition.
