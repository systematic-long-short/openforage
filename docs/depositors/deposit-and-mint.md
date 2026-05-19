# Deposit and Mint RISKUSD

## Overview

RISKUSD is the protocol's non-yielding dollar token. You deposit USDC and receive RISKUSD at a fixed 1:1 ratio. There are no auction rounds, no clearing prices, and no partial fills. Deposits are accessible continuously.

## Step by Step

1. **Approve USDC spending.** Before depositing, you must approve the RISKUSDVault to spend your USDC. The protocol requests maximum approval to avoid repeated approval transactions.

2. **Deposit USDC.** Call the RISKUSDVault deposit function with the amount of USDC you want to convert. The contract mints an equal amount of RISKUSD to your wallet.

3. **Confirm receipt.** Your wallet balance updates immediately. RISKUSD appears as a standard ERC-20 token on Arbitrum.

## Key Facts

- **Ratio**: Always 1:1. One USDC in, one RISKUSD out.
- **Network**: Arbitrum only.
- **Continuous access**: No deposit windows or rounds. Deposit at any time.
- **No yield**: RISKUSD itself does not earn yield. To participate in trading operations, stake it into an [atRISKUSD vault](/docs/depositors/vault-participation.md).

## Redeeming RISKUSD for USDC

To convert back:

1. Call `RISKUSDVault.redeem(uint256)` with the RISKUSD amount
2. If checks pass, the transaction burns RISKUSD and returns USDC at 1:1

A governance-configurable weekly redemption cap may apply. If a redemption would exceed the cap, it reverts; it does not queue. Redemption may also revert or be blocked by vault liquidity, reserve-ratio checks, unresolved loss or loss-pending conditions, blocklist status, or pause conditions.
