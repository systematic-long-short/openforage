# atRISKUSD

## What Is atRISKUSD?

atRISKUSD is a yield-bearing vault position. Each atRISKUSD vault is tied to a market and a specific alpha-generating process. When you stake RISKUSD into a vault, you receive atRISKUSD shares at the current exchange rate. As trading generates revenue for that vault, the exchange rate appreciates. If trading loses money, the exchange rate decreases.

## Key Properties

| Property | Value |
|----------|-------|
| Type | ERC-20 on Arbitrum (4 tier instances per vault) |
| Denomination | Floating exchange rate vs RISKUSD |
| Yield | Exchange-rate appreciation from trading |
| Trading risk | Yes — exchange rate can decrease |
| Supply | Elastic — grows with stakes, shrinks with withdrawals |

## Tier System

| Tier | Approximate lock-up | Yield share |
|------|---------------------|-------------|
| T0 | None | 50% |
| T1 | 3 months | 55% |
| T2 | 6 months | 60% |
| T3 | 12 months | 65% |

> T3 lockup is 360 days exactly per the deployed contract (12 × 30 days, not a calendar year).

All percentages are current default protocol rules and governance-configurable.

## Exchange Rate

The exchange rate is the ratio of RISKUSD backing to atRISKUSD shares outstanding. It moves based on trading performance:

- **Profitable period** — Exchange rate increases (more RISKUSD per share)
- **Losing period** — Exchange rate decreases (less RISKUSD per share)
- **Yield crystallization** — Attestation-driven exchange-rate updates can occur frequently

## Withdrawal

1. Request withdrawal — RISKUSD amount is calculated and locked at current exchange rate
2. Wait for 7-day cooldown — position no longer participates in exchange-rate changes
3. Execute withdrawal — receive locked RISKUSD amount

If in a locked tier (T1–T3), you must wait for lock-up expiry before requesting withdrawal.

## Auto-Renewal

Auto-renewal is enabled by default. When your lock-up expires:

- **Auto-renewal ON** — Lock-up resets; you stay in the same tier
- **Auto-renewal OFF** — Shares revert to Tier 0; you earn Tier 0 yield

Toggle auto-renewal at any time through the staking interface.
