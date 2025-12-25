#!/usr/bin/env python3
"""Gold price arbitrage monitor between Predict.fun and Polymarket.

Strategy:
- PF market: "Will gold close above $4,400 in 2025?" - Buy NO (bet gold <= $4400)
- PM markets: 7 price ranges ($4400-$4500, ..., >$5000) - Buy YES on all
- Arbitrage condition: PF_NO_ask * 1.02 + sum(PM_YES_asks) < 1

Usage:
    uv run python case/gold/arb.py
"""

import asyncio
import json
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from time import time

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.clients.predictfun import PredictFunClient
from src.clients.polymarket import PolymarketClient
from src.config import PredictFunConfig, PolymarketConfig
from src.models import Orderbook, Side
from src.logging import get_logger
from src.utils.telegram import TelegramNotifier

PM_MIN_ORDER_VALUE = 1.0  # PM minimum order value in USD

# Configuration
ARB_AMOUNT = 50  # USD per arbitrage
MIN_PROFIT_THRESHOLD = 0.01  # 1% minimum profit
CHECK_INTERVAL = 5  # seconds between checks
PF_FEE_RATE = 1.02  # 2% fee on Predict.fun

logger = get_logger("gold_arb")


@dataclass
class MarketConfig:
    """Market configuration loaded from JSON."""
    pf_market_id: int
    pf_no_token_id: str
    pm_markets: list[dict]  # [{title, yes_token_id}, ...]


@dataclass
class ArbOpportunity:
    """Arbitrage opportunity details."""
    pf_no_ask: float
    pm_yes_asks: list[float]
    total_cost: float
    profit_rate: float
    min_shares: float  # Minimum shares to meet $1 order requirement
    max_shares: float  # Maximum shares before slippage kills profit
    timestamp: float


def load_market_config() -> MarketConfig:
    """Load market configuration from JSON file."""
    config_path = Path(__file__).parent / "markets.json"
    with open(config_path) as f:
        data = json.load(f)

    return MarketConfig(
        pf_market_id=data["predictfun"]["market_id"],
        pf_no_token_id=data["predictfun"]["no_token_id"],
        pm_markets=data["polymarket"]["markets"],
    )


def calc_buy_cost(orderbook: Orderbook, shares: float) -> float | None:
    """Calculate cost to buy N shares from orderbook (eating asks).

    Returns None if not enough liquidity.
    """
    remaining = shares
    cost = 0.0
    for price, size in orderbook.asks:
        take = min(size, remaining)
        cost += price * take
        remaining -= take
        if remaining <= 0:
            return cost
    return None  # Not enough liquidity


def calc_max_shares(
    pf_book: Orderbook,
    pm_books: list[Orderbook],
    fee_rate: float = PF_FEE_RATE,
) -> float:
    """Calculate max shares where arbitrage is still profitable.

    Binary search for max N where: PF_cost * fee + sum(PM_costs) < N
    """
    # Find upper bound (min total liquidity across all books)
    max_liquidity = min(
        sum(size for _, size in pf_book.asks),
        *[sum(size for _, size in book.asks) for book in pm_books]
    )

    if max_liquidity <= 0:
        return 0

    # Binary search
    lo, hi = 0.0, max_liquidity
    result = 0.0

    for _ in range(50):  # Enough iterations for precision
        mid = (lo + hi) / 2
        if mid <= 0:
            break

        pf_cost = calc_buy_cost(pf_book, mid)
        if pf_cost is None:
            hi = mid
            continue

        total_cost = pf_cost * fee_rate
        valid = True
        for book in pm_books:
            pm_cost = calc_buy_cost(book, mid)
            if pm_cost is None:
                valid = False
                break
            total_cost += pm_cost

        if not valid:
            hi = mid
        elif total_cost < mid:  # Profitable: cost < payout
            result = mid
            lo = mid
        else:
            hi = mid

    return result


async def check_arbitrage(
    pf_client: PredictFunClient,
    pm_clients: list[PolymarketClient],
    market_config: MarketConfig,
) -> ArbOpportunity | None:
    """Check for arbitrage opportunity using connected clients."""
    try:
        # Fetch all orderbooks concurrently
        tasks = [pf_client.get_orderbook()]
        for client in pm_clients:
            tasks.append(client.get_orderbook())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check for errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch orderbook {i}: {result}")
                return None

        # Extract orderbooks
        pf_book: Orderbook = results[0]
        pm_books: list[Orderbook] = results[1:]

        pf_no_ask = pf_book.best_ask
        if pf_no_ask is None:
            logger.warning("No PF NO asks available")
            return None

        pm_yes_asks = []
        for i, book in enumerate(pm_books):
            if book.best_ask is None:
                logger.warning(f"No PM asks for {market_config.pm_markets[i]['title']}")
                return None
            pm_yes_asks.append(book.best_ask)

        # Calculate basic arbitrage (best prices only)
        total_cost = pf_no_ask * PF_FEE_RATE + sum(pm_yes_asks)
        profit_rate = 1 - total_cost

        # Calculate min shares (PM minimum order = $1)
        min_price = min(pm_yes_asks)
        min_shares = PM_MIN_ORDER_VALUE / min_price if min_price > 0 else float('inf')

        # Calculate max shares considering slippage
        max_shares = calc_max_shares(pf_book, pm_books)

        return ArbOpportunity(
            pf_no_ask=pf_no_ask,
            pm_yes_asks=pm_yes_asks,
            total_cost=total_cost,
            profit_rate=profit_rate,
            min_shares=min_shares,
            max_shares=max_shares,
            timestamp=time(),
        )

    except Exception as e:
        logger.error(f"Error checking arbitrage: {e}")
        return None


async def execute_arbitrage(
    pf_client: PredictFunClient,
    pm_clients: list[PolymarketClient],
    market_config: MarketConfig,
    shares: float,
) -> bool:
    """Execute arbitrage by placing market orders on all 8 markets."""
    logger.info(f"Executing arbitrage: {shares:.2f} shares")

    success = True

    # Place PF NO order
    try:
        order = await pf_client.place_market_order(Side.BUY, size=shares)
        logger.info(f"PF NO order placed: {order.id[:30]}...")
    except Exception as e:
        logger.error(f"Failed to place PF NO order: {e}")
        success = False

    # Place PM YES orders
    for i, client in enumerate(pm_clients):
        title = market_config.pm_markets[i]["title"]
        try:
            order = await client.place_market_order(Side.BUY, size=shares)
            logger.info(f"PM {title} YES order placed: {order.id[:30]}...")
        except Exception as e:
            logger.error(f"Failed to place PM {title} order: {e}")
            success = False

    return success


async def monitor_loop(market_config: MarketConfig):
    """Main monitoring loop with persistent client connections."""
    logger.info("Starting gold arbitrage monitor")
    logger.info(f"Config: amount=${ARB_AMOUNT}, threshold={MIN_PROFIT_THRESHOLD:.1%}, interval={CHECK_INTERVAL}s")

    # Load configs
    pf_config = PredictFunConfig.from_env()
    pm_config = PolymarketConfig.from_env()

    # Create TG notifier
    tg = TelegramNotifier()
    if tg.is_configured:
        logger.info("Telegram notifications enabled")
    else:
        logger.info("Telegram not configured, notifications disabled")

    # Create and connect all clients
    pf_client = PredictFunClient(
        market_id=market_config.pf_market_id,
        token_id=market_config.pf_no_token_id,
        is_yes=False,  # We're trading NO token
        config=pf_config,
    )

    pm_clients = [
        PolymarketClient(token_id=m["yes_token_id"], config=pm_config)
        for m in market_config.pm_markets
    ]

    try:
        # Connect all clients
        logger.info("Connecting to PF...")
        await pf_client.connect()

        logger.info("Connecting to PM (7 markets)...")
        for i, client in enumerate(pm_clients):
            await client.connect()
            logger.debug(f"PM client {i+1}/7 connected")

        logger.info("All clients connected, starting monitor loop")

        # Monitor loop
        while True:
            opp = await check_arbitrage(pf_client, pm_clients, market_config)

            if opp:
                pm_sum = sum(opp.pm_yes_asks)
                pf_cost = opp.pf_no_ask * PF_FEE_RATE
                min_cost = opp.min_shares * opp.total_cost
                max_cost = opp.max_shares * opp.total_cost
                logger.info(
                    f"PF={pf_cost:.4f} PM={pm_sum:.4f} "
                    f"Total={opp.total_cost:.4f} Profit={opp.profit_rate:.2%} | "
                    f"Shares=[{opp.min_shares:.1f}, {opp.max_shares:.1f}] "
                    f"Cost=[${min_cost:.2f}, ${max_cost:.2f}]"
                )

                if opp.profit_rate >= MIN_PROFIT_THRESHOLD:
                    # Check if arbitrage is feasible
                    if opp.max_shares < opp.min_shares:
                        logger.warning(
                            f"Arb not feasible: max_shares({opp.max_shares:.1f}) < "
                            f"min_shares({opp.min_shares:.1f})"
                        )
                    else:
                        logger.info(f"ARBITRAGE FOUND! Profit: {opp.profit_rate:.2%}")

                        # Use max_shares (maximize profit), capped by ARB_AMOUNT
                        shares = min(opp.max_shares, ARB_AMOUNT / opp.total_cost)
                        # Ensure above minimum
                        shares = max(shares, opp.min_shares)

                        if shares > opp.max_shares:
                            logger.warning(f"Cannot meet min_shares, skipping")
                        else:
                            total_usd = shares * opp.total_cost

                            # Notify: arbitrage found
                            await tg.send(
                                f"<b>Gold Arb Found</b>\n"
                                f"Profit: {opp.profit_rate:.2%}\n"
                                f"Shares: {shares:.1f} (${total_usd:.2f})\n"
                                f"PF NO: {opp.pf_no_ask:.4f}\n"
                                f"PM sum: {pm_sum:.4f}"
                            )

                            logger.info(f"Buying {shares:.2f} shares for ${total_usd:.2f}")

                            success = await execute_arbitrage(
                                pf_client, pm_clients, market_config, shares
                            )
                            if success:
                                logger.info("Arbitrage executed successfully!")
                                await tg.send(f"<b>Arb Executed</b>\n{shares:.1f} shares @ ${total_usd:.2f}")
                            else:
                                logger.warning("Arbitrage execution had some failures")
                                await tg.send(f"<b>Arb Failed</b>\nSome orders failed")

            await asyncio.sleep(CHECK_INTERVAL)

    finally:
        # Cleanup
        logger.info("Closing connections...")
        await tg.close()
        await pf_client.close()
        for client in pm_clients:
            await client.close()


def main():
    """Entry point."""
    config = load_market_config()
    logger.info(f"Loaded config: PF market={config.pf_market_id}, PM markets={len(config.pm_markets)}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown(_sig, _frame):
        logger.info("Shutting down...")
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(monitor_loop(config))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
