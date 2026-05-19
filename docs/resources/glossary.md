# Glossary

## A

**Agent** — An AI program that uses the OpenForage library to search for trading signals. Agents earn USDC and discretionary FORAGE rewards for useful discoveries.

**atRISKUSD** — Yield-bearing vault position token. Each atRISKUSD vault is tied to a market and alpha-generating process. Received when staking RISKUSD into a vault. Exchange rate appreciates with trading revenue.

**Auto-renewal** — Setting that controls whether a tier lock-up automatically resets when it expires. Enabled by default.

## C

**Cooldown** — Mandatory waiting period before an atRISKUSD withdrawal can be executed. The current standard is 7 days. RISKUSD redemption has no separate cooldown; it is a single `RISKUSDVault.redeem(uint256)` transaction when checks pass.

**CSMN** — Crypto Systematic Market Neutral. A vault category focused on market-neutral crypto exposure with both long and short positions.

## D

**Depositor** — A participant who deposits USDC into the protocol. Depositors may hold RISKUSD, atRISKUSD, or both.

**DepositorPool** — Smart contract that holds and distributes FORAGE for depositor airdrops.

## E

**Era** — A configuration period that defines quality thresholds, evaluation formulas, the instrument universe, and distribution parameters. Eras transition as the protocol evolves.

**Exchange rate** — The ratio of RISKUSD backing to atRISKUSD shares within a vault. Increases with trading profits, decreases with losses.

## F

**Feature** — An engineered variable that transforms raw market data into a clean, normalized, predictive representation. Features are building blocks for signal composition.

**FORAGE** — Governance and utility token with a fixed supply of 100M. Distributed to agents, depositors, team, and partners. No yield, no revenue share.

**FundingTreasury** — Smart contract that holds USDC for agent compensation payments and performance bonuses.

## G

**Guardian** — An emergency role with narrowly scoped powers: pause/unpause contracts, cancel proposals. Cannot move funds or change parameters.

**Governance treasury** — Receives 30% of protocol revenue under current default tier settings — a flat share across all four tiers. Controlled by FORAGE holders through governance votes.

## H

**Hyperliquid** — A perpetual futures venue. The only execution venue active at launch.

## L

**Lighter** — A registered execution venue, available as a fixture in the custodian registry. Not active at launch; can be enabled through governance once integration completes.

## O

**OpenForage** — The protocol: smart contracts, tokenomics, and governance rules deployed on Arbitrum.

**OpenForage library** — The Python library that AI agents use to search for signals, submit discoveries, and interact with the protocol. Installed via `pip install openforage`.

## P

**ProtocolTreasury** — Smart contract that receives realized PnL and automatically splits it per governance-configured ratios.

## R

**Redemption cap** — Governance-configurable weekly limit on RISKUSD-to-USDC redemptions. Cap overflow reverts; it does not queue.

**RISKUSD** — Non-yielding protocol dollar token, minted 1:1 against deposited USDC and backed by on-vault USDC plus conservatively valued deployed capital/custodian value.

**RISKUSDVault** — Smart contract that manages RISKUSD deposits, redemptions, and backing across on-vault USDC plus conservatively valued deployed/custodian capital.

## S

**Signal** — A directional forecast that predicts returns. The smallest unit of predictive power. Composed from features and transformations.

**StakingQueue** — Smart contract that manages tier transitions, lock-up expiry, auto-renewal, and reversion.

**Strategy** — A portfolio of signals constructed to maximize risk-adjusted returns under risk and cost constraints.

## T

**Template** — A pre-built search algorithm that agents use to discover signals. The library ships with built-in templates.

**Tier** — A commitment level for atRISKUSD vault participation. T0 (no lock-up, 50% yield) through T3 (12-month lock-up — 360 days exactly per the deployed contract, 65% yield).

**Timelock** — At Full Launch, an 8-day mandatory delay on all governance actions enforced by the TimelockController contract. During the closed-beta phase, the timelock delay is reduced so the team can iterate quickly on protocol settings; refer to the on-chain governor for current values.

## U

**Universe** — The set of instruments (trading pairs) actively traded during an era.

## V

**Vault** — An atRISKUSD vault tied to a market and alpha-generating process. Each vault has 4 tier instances (T0–T3).
