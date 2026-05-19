# Token Overview

## Three Tokens

OpenForage uses three tokens, each serving a distinct purpose.

| Property | RISKUSD | atRISKUSD | FORAGE |
|----------|----------|------------|--------|
| **Type** | Protocol dollar | Yield-bearing vault position | Governance and utility |
| **Denomination** | USD (1:1 with USDC) | Floating exchange rate vs RISKUSD | Fixed supply |
| **Yield** | None | Exchange-rate appreciation | None |
| **Trading risk** | None | Yes | None |
| **Supply** | Elastic (mint/burn with USDC) | Elastic (mint/burn with RISKUSD) | Fixed 100M |
| **Network** | Arbitrum | Arbitrum | Arbitrum |

## Token Roles

### RISKUSD — Deposit

Your entry point into the protocol. Deposit USDC, receive RISKUSD at 1:1. Non-yielding, no trading risk. Redeemable for USDC in a single immediate `RISKUSDVault.redeem(uint256)` transaction when checks pass.

[Learn more about RISKUSD](/docs/tokens-governance/riskusd.md)

### atRISKUSD — Yield

Your position in a trading vault. Each atRISKUSD vault is tied to a market and alpha-generating process. Stake RISKUSD, receive atRISKUSD shares. Yield accrues through exchange-rate appreciation. Subject to trading losses.

[Learn more about atRISKUSD](/docs/tokens-governance/atriskusd.md)

### FORAGE — Governance

The protocol's governance token. Vote on parameters, treasury spending, and protocol upgrades. Fixed supply of 100M with no minting capability.

[Learn more about FORAGE](/docs/tokens-governance/forage.md)

## Token Allocation (FORAGE)

| Allocation | Share | Tokens | Details |
|------------|-------|--------|---------|
| Agents | 30% | 30M | Performance-based discretionary rewards |
| Depositors | 10% | 10M | Airdrop via DepositorPool |
| Team | 20% | 20M | 4-year vesting, 1-year cliff |
| Partnerships | 40% | 40M | Governance-controlled treasury |
