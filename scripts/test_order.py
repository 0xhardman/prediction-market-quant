#!/usr/bin/env python3
"""Test PF and PM order placement with minimal shares.

Usage:
    uv run python scripts/test_order.py --platform pf --market-id 655 --token-id <token> --is-yes
    uv run python scripts/test_order.py --platform pm --token-id <token>
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.clients.predictfun import PredictFunClient
from src.clients.polymarket import PolymarketClient
from src.config import PredictFunConfig, PolymarketConfig
from src.models import Side
from src.logging import get_logger

logger = get_logger("test_order")


async def test_pf_order(market_id: int, token_id: str, is_yes: bool, shares: float, use_limit: bool = False):
    """Test PF order placement."""
    config = PredictFunConfig.from_env()
    client = PredictFunClient(
        market_id=market_id,
        token_id=token_id,
        is_yes=is_yes,
        config=config,
    )

    try:
        logger.info(f"Connecting to PF (market={market_id})...")
        await client.connect()

        # Get balance
        balance = await client.get_balance()
        logger.info(f"Balance: {balance:.2f} USDT")

        # Get orderbook
        ob = await client.get_orderbook()
        logger.info(f"Orderbook: best_bid={ob.best_bid}, best_ask={ob.best_ask}")

        if not ob.best_ask:
            logger.error("No asks available")
            return

        if use_limit:
            # Place limit order at best_ask price (should fill immediately)
            price = ob.best_ask
            logger.info(f"Placing LIMIT BUY order: {shares} shares @ {price}")
            order = await client.place_order(Side.BUY, price=price, size=shares)
        else:
            # Place market order
            logger.info(f"Placing MARKET BUY order: {shares} shares, is_yes={is_yes}")
            order = await client.place_market_order(
                Side.BUY, size=shares, token_id=token_id, is_yes=is_yes
            )
        logger.info(f"Order placed: {order.id}")
        logger.info(f"Price: {order.price:.4f}, Size: {order.size}")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await client.close()


async def test_pm_order(token_id: str, shares: float):
    """Test PM order placement."""
    config = PolymarketConfig.from_env()
    client = PolymarketClient(token_id=token_id, config=config)

    try:
        logger.info(f"Connecting to PM (token={token_id[:20]}...)...")
        await client.connect()

        # Get balance
        balance = await client.get_balance()
        logger.info(f"Balance: {balance:.2f} USDC")

        # Get orderbook
        ob = await client.get_orderbook()
        logger.info(f"Orderbook: best_bid={ob.best_bid}, best_ask={ob.best_ask}")

        if not ob.best_ask:
            logger.error("No asks available")
            return

        # Place order
        logger.info(f"Placing BUY order: {shares} shares")
        order = await client.place_market_order(Side.BUY, size=shares)
        logger.info(f"Order placed: {order.id}")
        logger.info(f"Price: {order.price:.4f}, Size: {order.size}")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="Test order placement")
    parser.add_argument("--platform", type=str, required=True, choices=["pf", "pm"])
    parser.add_argument("--market-id", type=int, help="PF market ID")
    parser.add_argument("--token-id", type=str, required=True, help="Token ID")
    parser.add_argument("--is-yes", action="store_true", help="PF: is YES token")
    parser.add_argument("--shares", type=float, default=1.0, help="Shares to buy (default: 1)")
    parser.add_argument("--limit", action="store_true", help="Use limit order instead of market order")
    args = parser.parse_args()

    if args.platform == "pf":
        if not args.market_id:
            print("Error: --market-id required for PF")
            return
        asyncio.run(test_pf_order(args.market_id, args.token_id, args.is_yes, args.shares, args.limit))
    else:
        asyncio.run(test_pm_order(args.token_id, args.shares))


if __name__ == "__main__":
    main()
