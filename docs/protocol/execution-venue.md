# Execution Venues and Public Trading Wallets

## Execution Venues

OpenForage routes orders to a registry of approved execution venues. Hyperliquid is the only venue active at launch. Additional venues (including Lighter, which is registered as a fixture in the custodian registry) can be enabled through governance as integrations complete. The protocol uses algorithmic execution to achieve target vault portfolios with minimal slippage.

## Capital Deployment

USDC deposited through RISKUSD can be deployed as trading capital across supported venues. Strategies generate target positions from accepted signals; positions from many strategies are netted into final vault portfolios before orders are routed.

Key characteristics:

- **Algorithmic execution** — No manual trading; all orders are placed programmatically
- **Market-neutral targeting** — Strategies aim for market-neutral positioning (both long and short positions)
- **Risk constraints** — Position sizing operates under risk and cost constraints defined by the current era

## Public Trading Wallets

The protocol's trading wallets are intended to be publicly visible where venue infrastructure supports it. Anyone can observe:

- Current open positions
- Historical trades
- Account balance and margin usage

This provides transparency into how depositor capital is being deployed.

## Venue Risk

> **Warning:** Venue concentration is a risk factor. Exchange downtime, liquidity issues, or counterparty risk at the venue level can impact protocol operations. See [Venue, Liquidity, and Operational Risk](https://openforage.ai/docs/risks/venue-liquidity) for details.

## Future Expansion

The protocol's design supports expansion to additional venues, vaults, and asset classes over time. Additional vaults targeting different markets and alpha-generating processes may be added as the protocol matures.
