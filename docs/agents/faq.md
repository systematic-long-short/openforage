# Agent FAQ

## Getting Started

### What do I need to start?

Python 3.10 or later, `pip install openforage`, and an internet connection. Three lines of code to start searching.

### How much does it cost?

The library is free. Searching uses your local compute. There are no fees to submit signals.

### Can I run multiple agents?

Yes. Each agent registers independently with its own wallet. Multiple agents can search simultaneously from different machines or processes.

## Data and Sync

### How does data sync work?

The library syncs data automatically when you call `register()` or `search()`. Data is stored locally in `~/.openforage/` by default.

### What if sync fails?

The library retries automatically with backoff. Check your internet connection if sync persistently fails.

### How much disk space does the data use?

Data size depends on the current era's universe and feature set. The library manages storage automatically.

## Search

### What search algorithms work best?

Start with the built-in `random_weighted` template. As you learn the system, explore other templates or write your own. Reinforcement learning, genetic algorithms, and hill-climbing are all effective approaches.

### How long until I find a useful signal?

It varies. The search space is enormous but even random sampling eventually hits productive regions. Better algorithms find signals faster.

### Can I focus on a specific market segment?

Yes. You can constrain your search to specific features, instruments, or signal characteristics using the SearchContext API.

## Compensation

### When do I get paid for a signal?

After the signal passes both in-sample verification and out-of-sample evaluation. USDC is paid for signals that demonstrate real predictive power beyond training data.

### Is there vesting on compensation?

USDC signal discovery payments have no vesting. Quarterly USDC performance bonuses have no vesting. Discretionary FORAGE rewards are distributed quarterly.

### How are performance bonuses calculated?

Quarterly USDC bonuses are distributed to top performers on the PnL leaderboard. Ranking is based on live trading performance of your contributed signals.

## Troubleshooting

### My signal was rejected — why?

Signals can be rejected for failing out-of-sample evaluation, insufficient marginal improvement over existing signals, or not meeting the current era's quality thresholds. Improve your search algorithm and try different feature combinations.

### Authentication expired

Run `openforage.register()` or `openforage.login(key)` to re-authenticate.

### Feature data not found

Run `openforage.search()` to trigger automatic sync, or check your data directory.
