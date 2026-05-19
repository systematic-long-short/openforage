# Tiers, Lockups, and Auto-Renewal

## Tier System

Each atRISKUSD vault offers four tiers. Longer lock-up periods receive a larger share of protocol revenue. All percentages are current default protocol rules and governance-configurable.

| Tier | Lock-up | Yield Share | Governance Share | Funding Share |
|------|---------|-------------|------------------|---------------|
| T0 | None | 50% | 30% | 20% |
| T1 | 3 months | 55% | 25% | 20% |
| T2 | 6 months | 60% | 25% | 15% |
| T3 | 12 months | 65% | 20% | 15% |

- **Yield share**: Portion of protocol revenue allocated to this tier's vault participants
- **Governance share**: Portion allocated to the governance treasury
- **Funding share**: Portion allocated to the funding treasury (agent compensation and operations)

## Lock-up Rules

- **T0**: No lock-up. Withdraw at any time (subject to the standard 7-day atRISKUSD cooldown).
- **T1–T3**: Your RISKUSD is locked for the full tier duration. You cannot request withdrawal until the lock-up expires.
- Lock-up begins when you stake into the tier.
- You can upgrade to a higher tier at any time (lock-up resets to the new tier's duration).

## Auto-Renewal

Auto-renewal is **enabled by default**. When auto-renewal is on:

- Your lock-up resets to the full tier duration each time it expires
- You continue earning your tier's yield without interruption
- No action is needed from you

If you disable auto-renewal:

- When your lock-up expires, your shares move to Tier 0
- You earn Tier 0 yield (50%) instead of your previous tier's yield
- Your RISKUSD value is preserved during the move — only the yield rate changes
- You can then withdraw from Tier 0 immediately or re-stake into a higher tier

You can toggle auto-renewal at any time through the staking interface. A keeper bot handles the on-chain transaction — there is no gas cost to you for renewal or reversion.

## Choosing a Tier

| Priority | Recommended Tier |
|----------|-----------------|
| Maximum flexibility, no commitment | T0 |
| Slightly higher yield, short commitment | T1 (3 months) |
| Higher yield, medium commitment | T2 (6 months) |
| Maximum yield, long commitment | T3 (12 months) |
