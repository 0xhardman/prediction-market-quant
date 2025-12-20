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

## Architecture

### Core Flow
1. `ArbitrageRunner` (src/main.py) orchestrates the system
2. Platform clients fetch orderbooks: PM uses WebSocket, Opinion/Predict.fun use REST polling
3. `ArbitrageEngine` (src/engine/arbitrage.py) detects opportunities across platform pairs
4. `OrderExecutor` (src/engine/executor.py) executes trades with mixed strategy (PM FOK + others aggressive limit)

### Platform Clients (src/clients/)
All inherit from `BaseClient` abstract class with `connect()`, `get_orderbook()`, `place_order()`, `cancel_order()`.

- **PolymarketClient**: WebSocket for orderbooks, py-clob-client for trading. Uses `signature_type=2` (POLY_GNOSIS_SAFE). Minimum order size is 5.
- **OpinionClient**: REST polling via opinion-clob-sdk
- **PredictFunClient**: REST with JWT auth. Auth field is `signer` (not `walletAddress`). Uses market_id (not token_id) for orderbook requests.

### Data Models (src/models.py)
- `Orderbook`: Cached orderbook with freshness check
- `ArbitrageOpportunity`: Detected opportunity with platform pair, direction, prices, profit
- `Direction` enum: `PM_YES_OP_NO`, `PM_NO_OP_YES`, `PM_YES_PF_NO`, `PM_NO_PF_YES`, `OP_YES_PF_NO`, `OP_NO_PF_YES`

### Configuration (src/config.py)
Loads from `config.yaml` with `${ENV_VAR}` expansion. Defines:
- Market pairs with token IDs for each platform
- Platform fees (PM 0%, Opinion 1%, Predict.fun 2%)
- Arbitrage thresholds (min profit, position limits, freshness)
- Credentials from environment variables

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
