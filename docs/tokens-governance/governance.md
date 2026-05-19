# Governance Process

## Governance Flow

All protocol changes follow a structured governance process with mandatory delays.

1. **Proposal.** Any FORAGE holder with at least 1% of total supply (1M FORAGE) can submit a proposal. Proposals specify the exact on-chain actions to execute.

2. **Voting Period.** Approximately 5-day voting period. Minimum 4% quorum (4M FORAGE) required for the vote to be valid. FORAGE holders vote for or against.

3. **Timelock.** If the proposal passes, it enters an 8-day mandatory timelock. The queued action is publicly visible on-chain with a countdown timer.

4. **Execution.** After the timelock expires, the proposal can be executed by anyone. The on-chain action runs exactly as specified in the proposal.

**Total time from proposal to execution: approximately 13 days.**

## Timelock

The TimelockController enforces an 8-day minimum delay (691,200 seconds) on all governance actions. This includes:

- Contract upgrades
- Parameter changes (revenue splits, cooldowns, caps)
- Treasury spending
- Any other on-chain governance action

The timelock gives depositors time to exit before any change takes effect. The full exit path (~8 days) is shorter than the governance cycle (~13 days).

## Delegation

FORAGE holders can delegate their voting power to another address. This allows:

- Passive holders to have their votes counted through a delegate
- Team tokens in the DelegatingVestingWallet to participate in governance during vesting

## Guardian Powers

Guardians have three narrowly scoped emergency powers:

1. **Pause/unpause** individual contracts immediately
2. **Cancel** active governance proposals before execution
3. **Batch pause/unpause** across multiple contracts

Guardians **cannot**:

- Move funds
- Change economic parameters
- Execute upgrades
- Take any other action

Guardian permissions are set at deployment and can only be changed through governance. The protocol has published a sunset timeline where guardian powers are progressively reduced as governance matures.

## What Can Governance Change?

| Parameter | Changeable | Process |
|-----------|-----------|---------|
| Revenue split (50/20/30) | Yes | Governance vote + 8-day timelock |
| Cooldown periods | Yes | Governance vote + 8-day timelock |
| Redemption caps | Yes | Governance vote + 8-day timelock |
| Tier yield allocations | Yes | Governance vote + 8-day timelock |
| Treasury spending | Yes | Governance vote + 8-day timelock |
| Contract upgrades | Yes | Governance vote + 8-day timelock |
| Guardian pause/unpause | Immediate | Guardian only |
