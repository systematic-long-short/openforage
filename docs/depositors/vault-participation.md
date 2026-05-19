# atRISKUSD Vault Participation

## How It Works

Each atRISKUSD vault is tied to a market and a specific alpha-generating process. When you stake RISKUSD into a vault, you receive atRISKUSD shares at the current exchange rate. As trading generates revenue for that vault, the vault's RISKUSD balance grows while the share count stays fixed. Your yield is the rising exchange rate.

**Example**: If the exchange rate is 1.00 when you stake and rises to 1.10, your atRISKUSD shares are now worth 10% more RISKUSD than when you entered.

## Multiple Vaults

The protocol supports multiple atRISKUSD vaults, each tied to a market and alpha-generating process. Current vault categories include crypto systematic market neutral and prediction-markets systematic market neutral exposure. Additional vaults targeting different markets and processes may be added over time.

## Exchange-Rate Mechanics

- **Revenue increases the rate**: When trading operations are profitable, the exchange rate goes up.
- **Losses decrease the rate**: When trading operations lose money, the exchange rate goes down. There is no automatic loss buffer.
- **Tier allocation**: Different tiers receive different shares of revenue. See [Tiers, Lockups, and Auto-Renewal](/docs/depositors/tiers-lockups.md).

## What You Are Exposed To

> **Danger:** Vault participation carries real risk. The atRISKUSD exchange rate can decrease if trading operations incur losses. You can lose money, including all of it. Holding RISKUSD without staking does NOT expose you to trading losses.

| State | Trading Risk | Yield |
|-------|-------------|-------|
| Holding USDC | None | None |
| Holding RISKUSD | None | None |
| Holding atRISKUSD | Yes — exchange-rate exposure | Yes — exchange-rate appreciation |

## Exchange-Rate Updates

Yield crystallization is attestation-driven rather than monthly. Realized PnL can be attested and allocated frequently. Once realized PnL is deposited, the split across recipients happens automatically in a single atomic transaction.
