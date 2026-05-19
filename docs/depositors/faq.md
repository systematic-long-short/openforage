# Depositor FAQ

## Deposits

### How do I participate?

Deposit USDC to mint RISKUSD (1:1), then optionally stake RISKUSD into an atRISKUSD vault to access exchange-rate appreciation from trading operations. Each vault is tied to a market and alpha-generating process. RISKUSD redemption is a single immediate transaction when checks pass; unstaking atRISKUSD has a 7-day cooldown.

### Does deposit size change terms?

The deposit mechanism is the same for all sizes — deposit USDC, receive RISKUSD at 1:1. Participants who commit larger deposits for extended periods may receive enhanced FORAGE airdrop allocations. Contact contact@openforage.ai to discuss.

## Withdrawals

### How long does it take to fully exit?

Approximately 7 days plus the RISKUSD redemption transaction: a 7-day cooldown to unstake atRISKUSD, then a single immediate `RISKUSDVault.redeem(uint256)` transaction when checks pass. If you are in a locked tier, you must also wait for your lock-up to expire first.

### Can I exit before a governance change takes effect?

Yes, under normal conditions. Governance proposals take approximately 13 days (5-day voting plus 8-day timelock). The standard exit takes approximately 7 days plus the RISKUSD redemption transaction when checks pass. The RISKUSD amount is locked at the exchange rate when you request withdrawal.

### Can my withdrawal be blocked?

atRISKUSD withdrawal execution cannot be blocked even if the protocol is paused. RISKUSD redemption is separate and may revert or be blocked by the weekly cap, vault liquidity, reserve-ratio checks, unresolved loss or loss-pending conditions, blocklist status, or pause conditions.

## Losses

### What happens if the protocol loses money?

The atRISKUSD exchange rate decreases. Your shares become worth less RISKUSD. There is no automatic loss buffer. However, the governance treasury may vote to voluntarily absorb losses on behalf of vault participants.

### Can I lose money holding RISKUSD (without staking)?

No. Holding RISKUSD without staking into atRISKUSD does not expose you to trading losses. Only atRISKUSD vault participants bear trading risk through the exchange rate.

## FORAGE Airdrops

### Do unclaimed airdrops expire?

Yes. The current default claim window is 180 days from round publication. After the claim window expires, unclaimed amounts may be swept by governance.

### Do I need FORAGE to earn yield?

No. FORAGE is a governance token with zero yield. To earn yield, you need atRISKUSD (not FORAGE).

## Governance

### Can payout rules change after launch?

Yes. All protocol parameters are governance-configurable. Changes require a ForageGovernor proposal (1% minimum to propose), approximately 5-day voting with 4% quorum, and an 8-day timelock delay. All queued changes are visible on-chain.

### Can distributions be delayed by the treasury?

Once realized PnL is deposited, the split happens automatically in a single atomic transaction. PnL deposit timing is operationally determined; after deposit, no party can selectively delay or withhold any portion.
