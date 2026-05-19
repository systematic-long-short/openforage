# Withdrawals, Redemptions, and Exit Timing

## Exit Path Overview

Exiting from an atRISKUSD vault to holding USDC requires an atRISKUSD withdrawal followed by a RISKUSD redemption. Only the atRISKUSD withdrawal has a cooldown.

| Step | Action | Cooldown | Result |
|------|--------|----------|--------|
| 1 | Request withdrawal from atRISKUSD vault | 7 days | Receive RISKUSD |
| 2 | Redeem RISKUSD for USDC | None; immediate transaction when checks pass | Receive USDC |

**Total time from initiating withdrawal to holding USDC: approximately 7 days plus the RISKUSD redemption transaction.**

## Step 1: Unstake atRISKUSD

When you request withdrawal from the vault:

1. The RISKUSD amount owed is calculated and **locked** at the current exchange rate
2. From that point, your position no longer participates in exchange-rate changes (up or down)
3. After the 7-day cooldown, you execute the withdrawal and receive the locked RISKUSD amount

> **Info:** The exchange rate is locked at the moment you request withdrawal. Price changes during the cooldown do not affect your exit amount.

## Step 2: Redeem RISKUSD for USDC

After receiving RISKUSD:

1. Call `RISKUSDVault.redeem(uint256)` with the RISKUSD amount
2. If checks pass, the transaction burns RISKUSD and returns USDC at 1:1

RISKUSD redemption may revert or be blocked by the weekly cap, vault liquidity, reserve-ratio checks, unresolved loss or loss-pending conditions, blocklist status, or pause conditions. If a redemption would exceed the cap, it reverts; it does not queue for a later period.

## Lock-up Constraints

If you are in a locked tier (T1, T2, or T3), you must wait for your lock-up to expire before requesting withdrawal.

- If auto-renewal is disabled and your lock-up has expired, shares automatically revert to Tier 0
- You then withdraw from Tier 0 (which has no lock-up)
- If auto-renewal is enabled, your lock-up resets automatically — disable auto-renewal first, then wait for expiry

## Emergency Withdrawal

atRISKUSD withdrawal execution **cannot be blocked** even if the protocol is paused. RISKUSD redemption is a separate vault transaction and may still revert while the RISKUSDVault is paused or another redemption check fails. There is no special emergency-withdraw mechanism that bypasses cooldowns.

## Governance Exit Timing

Governance proposals take approximately 13 days from submission to execution (5-day voting plus 8-day timelock). The standard vault exit path takes approximately 7 days plus the immediate RISKUSD redemption transaction when checks pass. This gives depositors time to exit before a queued governance change takes effect under normal conditions.
