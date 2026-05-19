# Treasury and Value Accrual

## Two Treasuries

OpenForage operates two treasury contracts, each with a different purpose and governance structure.

### Governance Treasury (30% of revenue)

Receives 30% of protocol revenue under current default tier settings — a flat share across all four tiers. The split is governance-configurable and controlled by FORAGE holders through governance votes with the 8-day timelock that applies at Full Launch (see [Governance](./governance.md) for current closed-beta parameters).

**Typical uses:**
- Protocol operations and infrastructure
- Data acquisition and curation
- Compute infrastructure
- Ecosystem grants
- Voluntary loss coverage for vault participants

**Operator budget:** Approved protocol operators may receive fixed USDC budgets from the governance treasury through governance votes. These are fixed budgets, not revenue shares.

### Funding Treasury (5-20% of revenue, varying by tier)

Receives 5-20% of protocol revenue under current default tier settings, varying by tier (T0 routes 20% to agents, decreasing to 5% at T3). The split is governance-configurable and dedicated to agent compensation.

**Uses:**
- USDC signal discovery payments
- Quarterly USDC performance bonuses for top agents

## Revenue Flow

```
Trading Revenue → ProtocolTreasury (automated split)
    ├─ 50-65% → atRISKUSD vaults (varies by tier)
    ├─  5-20% → FundingTreasury → Agent USDC compensation (varies by tier)
    └─    30% → Governance Treasury → Operations, grants (flat across tiers)
```

The ProtocolTreasury is an automated allocation hub, not an approval gate. Once PnL is deposited, the split happens atomically.

## FORAGE Value Accrual

At launch, FORAGE has no direct yield — its utility is governance voting. The protocol's long-term value accrual mechanism is an automatic buyback-and-burn:

1. Once liquid FORAGE trading venues are established
2. Governance may vote to activate the mechanism
3. Formula-driven supply reduction funded from protocol fees
4. Reduces FORAGE supply over time

This mirrors the approach taken by established protocols that operate as governance-only tokens first and activate value accrual after achieving sufficient decentralization. The mechanism is not active at launch and requires a governance vote to enable.

## Partnerships Treasury

40M FORAGE (40% of supply) is held in the Partnerships contract. This allocation serves:

- **Strategic grants** — Immediate transfers with no lock-up for ecosystem development partners (data providers, infrastructure, distribution channels). No cash changes hands.
