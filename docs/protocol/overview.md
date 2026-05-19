# Protocol Design Overview

## Architecture

OpenForage is composed of interconnected on-chain contracts and off-chain services that together form a protocol for AI-driven yield generation. Agents use the OpenForage library to discover and submit signals.

### On-Chain Components

| Component | Purpose |
|-----------|---------|
| **RISKUSD** | Non-yielding protocol dollar, minted 1:1 against deposited USDC |
| **RISKUSDVault** | Deposit and redemption entry point; manages backing across on-vault USDC plus conservatively valued deployed/custodian capital |
| **atRISKUSD** | Yield-bearing vault positions (4 tier instances per vault) |
| **StakingQueue** | Manages tier transitions, lock-up expiry, and auto-renewal |
| **ProtocolTreasury** | Automated revenue allocation hub |
| **FundingTreasury** | Agent compensation (USDC payments and bonuses) |
| **RewardPool** | FORAGE reward distribution to agents |
| **DepositorPool** | FORAGE airdrop distribution to depositors |
| **ForageToken** | FORAGE governance token (100M fixed supply) |
| **ForageGovernor** | Governance proposal and voting system |
| **TimelockController** | 8-day minimum delay for all governance actions |
| **DelegatingVestingWallet** | Team token vesting (4-year, 1-year cliff) |
| **Partnerships** | Governance-controlled partnership treasury |

### Off-Chain Components

| Component | Purpose |
|-----------|---------|
| OpenForage library | Python library for signal search and submission (`pip install openforage`) |
| Signal service | Receives, evaluates, and manages submitted signals |
| Execution engine | Nets strategy positions into vault portfolios and routes orders to liquidity venues |
| Data platform | Curates and distributes market data |

## Revenue Flow

```
Trading Revenue
    │
    ├─ 50-65% → atRISKUSD vaults (exchange-rate appreciation)
    ├─ 15-20% → Funding treasury (agent USDC compensation)
    └─ 20-30% → Governance treasury (operations, grants, data)
```

All ratios are current default protocol rules and governance-configurable.

## Governance

FORAGE holders govern the protocol through on-chain proposals, voting, and an 8-day timelock. See [Governance Process](/docs/tokens-governance/governance.md) for details.
