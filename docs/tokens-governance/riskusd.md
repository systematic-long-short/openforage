# RISKUSD

## What Is RISKUSD?

RISKUSD is the protocol's non-yielding dollar token. It is minted 1:1 against deposited USDC and backed by on-vault USDC plus conservatively valued deployed capital/custodian value.

## Key Properties

| Property | Value |
|----------|-------|
| Type | ERC-20 on Arbitrum |
| Peg | 1:1 with USDC |
| Yield | None |
| Trading risk | None |
| Supply | Elastic — grows with deposits, shrinks with redemptions |

## Minting

Deposit USDC into the RISKUSDVault. Receive RISKUSD at a fixed 1:1 ratio. No fees, no slippage, no rounds.

- Requires USDC approval first
- Available continuously — no deposit windows
- Immediate — RISKUSD appears in your wallet after transaction confirmation

## Redeeming

Convert RISKUSD back to USDC:

1. Call `RISKUSDVault.redeem(uint256)` with the RISKUSD amount
2. If checks pass, the transaction burns RISKUSD and returns USDC at 1:1

A governance-configurable weekly redemption cap may apply. If a redemption would exceed the cap, it reverts; it does not queue. Redemption may also revert or be blocked by vault liquidity, reserve-ratio checks, unresolved loss or loss-pending conditions, blocklist status, or pause conditions.

## Peg Behavior

The RISKUSD:USDC peg is maintained by the mint/redeem mechanism and vault solvency checks. When checks pass, `RISKUSDVault.redeem(uint256)` returns USDC at the 1:1 rate. The weekly redemption cap enforces bounded outflows by reverting transactions that would exceed the cap.

## Role in the Protocol

RISKUSD serves as the intermediate layer between external USDC and the yield vault:

```
USDC → RISKUSD → atRISKUSD (yield)
USDC ← RISKUSD ← atRISKUSD (exit)
```

Holding RISKUSD without staking keeps your capital in the protocol without trading exposure. This is useful for depositors who want to participate in FORAGE airdrops without taking trading risk.
