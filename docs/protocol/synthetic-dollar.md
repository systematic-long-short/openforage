# Synthetic Dollar Model

## Two-Token Design

OpenForage separates the deposit function from the yield function using two dollar-denominated tokens.

### RISKUSD — The Protocol Dollar

- Minted 1:1 against USDC
- Non-yielding — holding RISKUSD does not participate in trading operations
- Redeemable for USDC through an immediate `RISKUSDVault.redeem(uint256)` transaction when checks pass
- Represents your deposit in the protocol

### atRISKUSD — The Yield Position

- Received when staking RISKUSD into a vault
- Yield-bearing — exchange rate appreciates as trading generates revenue
- Subject to trading losses — exchange rate can decrease
- Subject to the atRISKUSD withdrawal cooldown and tier lock-ups

## Why Separate?

Separation serves three purposes:

1. **Risk isolation** — Depositors choose whether to take trading risk. Holding RISKUSD keeps capital in the protocol without trading exposure. Staking into atRISKUSD opts into risk and reward.

2. **Tier flexibility** — Different tiers receive different revenue allocations. Each atRISKUSD vault supports four tiers with independent exchange rates, each backed by the same underlying RISKUSD.

3. **Clean accounting** — RISKUSD is minted 1:1 against deposited USDC and backed by on-vault USDC plus conservatively valued deployed capital/custodian value. atRISKUSD tracks a floating exchange rate. Separating these simplifies on-chain accounting and makes the yield mechanism transparent.

## Exchange-Rate Mechanics

When trading operations generate profit:

1. Realized PnL is deposited into the ProtocolTreasury
2. The treasury splits revenue per governance-configured ratios
3. The vault's share increases the RISKUSD balance backing atRISKUSD shares
4. The exchange rate (RISKUSD per atRISKUSD share) increases
5. Each atRISKUSD share is now worth more RISKUSD

When trading operations lose money, the same mechanism works in reverse — the exchange rate decreases.

## Worked Example

| Event | Exchange Rate | 100 atRISKUSD Worth |
|-------|--------------|---------------------|
| Initial stake | 1.000 | 100 RISKUSD |
| After profitable month | 1.015 | 101.5 RISKUSD |
| After losing month | 1.005 | 100.5 RISKUSD |
| After another profitable month | 1.025 | 102.5 RISKUSD |

The exchange rate reflects cumulative net performance over time.
