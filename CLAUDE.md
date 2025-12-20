# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-platform prediction market arbitrage system supporting Polymarket, Opinion, and Predict.fun. Detects and executes cross-platform arbitrage opportunities when Yes + No token prices sum to less than 1.0 (after fees).

## Commands

```bash
# Install dependencies
uv sync

# Run arbitrage system
uv run python -m src.main
# Or with custom config
uv run arbitrage [config.yaml]

# Run tests
uv run pytest tests/

# Run single test file
uv run pytest tests/test_arbitrage.py -v

# Connection tests for each platform
uv run python scripts/test_pm_connection.py    # Polymarket
uv run python scripts/test_opinion_connection.py
uv run python scripts/test_pf_connection.py    # Predict.fun

# Generate Polymarket API credentials from private key
uv run python scripts/generate_pm_creds.py

# Market lookup (accepts URL, slug, condition_id, or token_id)
uv run python scripts/pm_market_lookup.py "https://polymarket.com/event/..."
```

## Key Implementation Details

### Arbitrage Detection
Profit calculation: `total_cost = pm_price + other_price + fees + gas`. Opportunity exists when `total_cost < 1.0` and `profit_pct > min_threshold`.

### Platform-Specific Notes
- Polymarket: Chain ID 137 (Polygon), proxy_address needed for GNOSIS_SAFE signing
- Opinion: Requires proxy for some regions, 1% taker fee
- Predict.fun: Chain ID 56 (BNB), 2% taker fee, orderbook endpoint uses market_id

### Environment Variables
```
PM_PRIVATE_KEY, PM_API_KEY, PM_API_SECRET, PM_API_PASSPHRASE, PM_PROXY_ADDRESS
OPINION_API_KEY, OPINION_PRIVATE_KEY
PREDICT_FUN_API_KEY (uses PM_PRIVATE_KEY for signing)
```
