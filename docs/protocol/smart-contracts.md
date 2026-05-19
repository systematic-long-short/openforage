# Smart Contract Architecture

## Contract Overview

OpenForage deploys a suite of contracts on Arbitrum, organized into five layers: dollar, yield, treasury, governance, and execution-and-safety. The thirteen core contracts that depositors and agents typically interact with are summarized below; additional infrastructure contracts (custodian registry, blocklist, guardian module, vault registry, and the venue bridge/execution suite) are deployed but are not direct user touchpoints.

### Dollar Layer

| Contract | Purpose |
|----------|---------|
| **RISKUSD** | ERC-20 protocol dollar minted 1:1 against deposited USDC and burned on redemption. |
| **RISKUSDVault** | Deposit and redemption entry point; manages RISKUSD backing as on-vault USDC plus conservatively valued deployed/custodian capital. |

### Yield Layer

| Contract | Purpose |
|----------|---------|
| **atRISKUSD** (4 tier instances per vault) | Yield-bearing vault positions. Exchange rate reflects cumulative net performance per vault. |
| **StakingQueue** | Manages tier transitions, lock-up expiry, auto-renewal, and reversion to Tier 0. |

### Treasury Layer

| Contract | Purpose |
|----------|---------|
| **ProtocolTreasury** | Automated revenue allocation hub. Splits incoming PnL per governance ratios. |
| **FundingTreasury** | Holds and distributes USDC for agent compensation. |
| **RewardPool** | Holds and distributes FORAGE for agent performance rewards. |
| **DepositorPool** | Holds and distributes FORAGE for depositor airdrops. |
| **Partnerships** | Governance-controlled treasury for strategic partnership allocations. |

### Governance Layer

| Contract | Purpose |
|----------|---------|
| **ForageToken** | FORAGE ERC-20 governance token. Fixed supply of 100M, no minting. |
| **ForageGovernor** | Proposal, voting, and execution system for protocol governance. |
| **TimelockController** | Enforces an 8-day minimum delay on all governance actions at Full Launch. During the closed-beta phase the delay is reduced; refer to the on-chain governor for current values. |
| **DelegatingVestingWallet** | Team FORAGE vesting (4-year linear, 1-year cliff). Supports governance delegation during vesting. |

### Execution-and-Safety Layer (infrastructure)

Deployed as part of the protocol but not direct user touchpoints:

| Contract | Purpose |
|----------|---------|
| **Blocklist** | Address-level deny-list checked by RISKUSD redemption and other risk-gated entry points. |
| **CustodianRegistry** | Registers approved execution venues and their bridge adapters. Hyperliquid is the only active custodian at launch; Lighter is registered as a fixture. |
| **GuardianModule** | Holds and rotates the narrowly scoped guardian role used for emergency pause/cancel actions. |
| **VaultRegistry** | Registry of supported atRISKUSD vaults and their tier instances. |
| **Hyperliquid execution suite** | Bridge, vault submit/withdrawal modules, executor proxy, and CoreWriter action parser that route custodial capital between Arbitrum and the Hyperliquid execution venue. |

## Key Depositor Interactions

The primary depositor flow touches three contracts:

1. **RISKUSD** — Deposit USDC, receive RISKUSD. Redeem RISKUSD for USDC.
2. **atRISKUSD** — Stake RISKUSD, receive yield-bearing shares. Request withdrawal.
3. **StakingQueue** — Select tier, manage auto-renewal, process lock-up expiry.

## Security Properties

- At Full Launch, all governance actions pass through the TimelockController with an 8-day delay; during the closed-beta phase the delay is reduced so the team can iterate quickly on protocol settings
- Guardians can only pause and unpause contracts (no fund movement or parameter changes)
- atRISKUSD withdrawal execution cannot be blocked, even during protocol pause
- Revenue allocation is atomic and non-discretionary once PnL is deposited

See [Smart Contract Security](https://openforage.ai/docs/risks/smart-contract-security) for security review status and trust assumptions.
