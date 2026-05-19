# Depositor Quickstart

> **Warning — eligibility:** Access to the OpenForage protocol, including atRISKUSD vault participation, is not available to residents of the United States, any member state of the European Union, the Republic of Singapore, or any jurisdiction subject to comprehensive OFAC-administered sanctions. See the [Terms & Conditions](../../legal/terms.md) for the full Restricted Jurisdictions list. Connecting a wallet from a Restricted Jurisdiction is a breach of the Terms and may result in suspension and forfeiture.

> **Domains.** All OpenForage user-facing surfaces live under two registered domains: `openforage.ai` (documentation, whitelist, marketing) and `openforage.com` (the connected app and the public protocol dashboard). Any other domain is not OpenForage.

Get from zero to earning yield in four steps.

1. **Connect your wallet.** Visit [openforage.com/app](https://openforage.com/app) and connect an Arbitrum-compatible wallet. The protocol runs on Arbitrum — if your wallet is on a different chain, you will be prompted to switch.

2. **Deposit USDC and mint RISKUSD.** Send USDC to the RISKUSDVault. You receive RISKUSD at a 1:1 ratio. This is the protocol's non-yielding dollar — it represents your deposit but does not participate in trading operations.

3. **Stake RISKUSD into an atRISKUSD vault.** Choose a vault tied to the market and alpha-generating process you want exposure to, then select a tier. You receive atRISKUSD shares at the current exchange rate. From this point, your position participates in all exchange-rate changes (up and down) for that vault.

4. **Earn yield.** Trading revenue flows into the vault, increasing the atRISKUSD exchange rate. Your yield is the rising exchange rate. When you want to exit, request withdrawal (7-day cooldown), then redeem RISKUSD for USDC in a single immediate `RISKUSDVault.redeem(uint256)` transaction when checks pass.

> **Warning:** Vault participation exposes you to trading losses. If the protocol takes losses, the atRISKUSD exchange rate drops — you can lose money, including all of it. Read the [risk overview](https://openforage.ai/docs/risks/overview) before depositing.

## What You Need

- An Arbitrum-compatible wallet (MetaMask, Rabby, or similar)
- USDC on Arbitrum
- Basic familiarity with token approvals and on-chain transactions

## Next Steps

- [Deposit and Mint RISKUSD](/docs/depositors/deposit-and-mint.md) — detailed deposit flow
- [Tiers, Lockups, and Auto-Renewal](/docs/depositors/tiers-lockups.md) — choose your commitment level
- [Depositor FAQ](/docs/depositors/faq.md) — common questions
