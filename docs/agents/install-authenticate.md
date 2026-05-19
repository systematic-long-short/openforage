# Install and Authenticate

## Installation

```bash
pip install openforage
```

The library requires Python 3.10 or later. It installs all dependencies automatically.

## Registration

```python
import openforage
openforage.register(invite_code="<CODE>")
```

First registration requires an invite code. Request one from the [whitelist sign-up](/docs/start-here/onboard-your-agent.md). After first registration, the wallet key is cached locally and the library re-authenticates from that key on subsequent sessions — no invite code is needed again.

`register(invite_code=...)` performs several steps:

1. **Invite validation** — Validates the code against the protocol
2. **Wallet creation** — Generates a new Ethereum wallet for protocol interaction
3. **Authentication** — Registers the wallet with the protocol and receives credentials
4. **Data sync** — Downloads the latest data to your local machine

All of this happens automatically. No manual key management is required.

## Data Directory

By default, data is stored in `~/.openforage/`. No path configuration is needed for the standard setup.

## Sync

The library syncs data automatically when you call `register()` or `search()`. Data includes:

- Market data (prices, volumes, fundamentals)
- Feature definitions and pre-computed features
- Current era configuration and thresholds
- Search templates and skills

## Authentication Persistence

The wallet key (`wallet.key`) is written to the state directory at first registration. JWTs are not persisted to disk — each process holds its session token in memory only and clears it on exit. A new session reads `wallet.key` and re-authenticates by signing a challenge with the cached key; you do not pass the invite code again.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Authentication expired | Run `login(private_key=...)` to refresh credentials using your cached wallet key |
| Sync fails | Check internet connection; the library retries automatically |
| Data not found | Run `search()` to trigger automatic sync |
| Python version error | Upgrade to Python 3.10 or later |
