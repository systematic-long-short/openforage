# Revenue Flow and Distribution

## Revenue Sources

Protocol revenue comes from trading operations across atRISKUSD vaults. Strategies are portfolios of signals constructed to maximize risk-adjusted returns under risk and cost constraints; their positions are netted into final vault portfolios before execution. Revenue is the net profit from these trading operations.

## Distribution Split

Revenue is split three ways. Current defaults vary by vault tier and are governance-configurable by FORAGE holders.

| Recipient | Current default | Purpose |
|-----------|-----------------|---------|
| atRISKUSD vault participants | 50-65% (varies by tier) | Exchange-rate appreciation (yield) |
| Agent compensation | 5-20% (varies by tier) | USDC signal payments and performance bonuses |
| Governance treasury | 30% (flat across tiers) | Operations, data, ecosystem grants |

## How Distribution Works

1. Realized PnL is deposited into the ProtocolTreasury contract
2. The treasury automatically splits per governance-configured ratios in a single atomic transaction
3. No party can selectively delay or withhold any portion

The timing of when PnL is deposited is operationally determined. Once deposited, allocation is instant and non-discretionary.

## Comparison to Traditional Operations

OpenForage charges no management fee. Operational allocations fund agent compensation, data, infrastructure, and ecosystem activity while preserving a vault-participant share of trading revenue.

## Governance Treasury Usage

The governance treasury is controlled by FORAGE holders. Funds can only be spent through governance votes with the timelock (8 days at Full Launch; reduced during the closed-beta phase). Typical uses include:

- Protocol operations and infrastructure
- Data acquisition
- Compute infrastructure
- Ecosystem grants
- Voluntary loss coverage for vault participants (at governance discretion)

Approved protocol operators may receive fixed USDC budgets from the governance treasury through governance votes. These are fixed budgets, not revenue shares.
