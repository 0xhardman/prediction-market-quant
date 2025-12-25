#!/usr/bin/env python3
"""NBA game arbitrage monitor between Predict.fun and Polymarket.

Strategy:
- PF: Binary markets (e.g., "Oklahoma City wins?") - YES/NO conversion applies
- PM: Multi-outcome markets - Thunder NO = buy Spurs YES token directly

Arbitrage conditions for each team:
1. PF_YES * 1.02 + PM_NO < 1 -> Buy PF YES + PM opponent YES
2. PF_NO * 1.02 + PM_YES < 1 -> Buy PF NO + PM team YES

Usage:
    uv run python case/nba/arb.py --game "Thunder vs Spurs"
"""

import argparse
import asyncio
import json
import math
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
MIN_PROFIT_THRESHOLD = 0.015  # 1.5% minimum profit
CHECK_INTERVAL = 5  # seconds between checks
PF_FEE_RATE = 1.02  # 2% fee on Predict.fun

logger = get_logger("nba_arb")


@dataclass
class GameConfig:
    """Game configuration loaded from JSON."""
    game_name: str
    team_a: str  # First team name (e.g., "Thunder")
    team_b: str  # Second team name (e.g., "Spurs")
    pm_team_a_token: str  # PM Team A YES token
    pm_team_b_token: str  # PM Team B YES token
    pf_team_a_market_id: int
    pf_team_a_yes_token: str
    pf_team_a_no_token: str
    pf_team_b_market_id: int
    pf_team_b_yes_token: str
    pf_team_b_no_token: str


@dataclass
class ArbOpportunity:
    """Arbitrage opportunity details."""
    game: str
    team: str
    direction: str  # "pf_yes_pm_no" or "pf_no_pm_yes"
    pf_ask: float
    pm_ask: float
    total_cost: float
    profit_rate: float
    min_shares: float
    max_shares: float
    timestamp: float


def load_game_config(game_name: str) -> GameConfig:
    """Load game configuration from JSON file."""
    config_path = Path(__file__).parent / "nba.json"
    with open(config_path) as f:
        data = json.load(f)

    # Find matching game
    for game in data["games"]:
        if game["game"] == game_name:
            pm = game["polymarket"]
            pf = game["predict_fun"]

            # Extract team names from outcomes
            pm_teams = list(pm["outcomes"].keys())
            pf_teams = list(pf["markets"].keys())

            # Match PM teams to PF teams (names may differ slightly)
            # Assume first team in game name is team_a
            game_parts = game_name.split(" vs ")
            team_a_name = game_parts[0]  # e.g., "Thunder"
            team_b_name = game_parts[1]  # e.g., "Spurs"

            # Find PM tokens by team name
            pm_team_a_token = pm["outcomes"].get(team_a_name)
            pm_team_b_token = pm["outcomes"].get(team_b_name)

            # Find PF markets - need to match city names to team names
            # Thunder -> Oklahoma City, Spurs -> San Antonio, etc.
            pf_team_a = None
            pf_team_b = None
            for city, market in pf["markets"].items():
                # Check if this city matches team_a or team_b
                # Use the order in the markets dict
                if pf_team_a is None:
                    pf_team_a = (city, market)
                else:
                    pf_team_b = (city, market)

            return GameConfig(
                game_name=game_name,
                team_a=team_a_name,
                team_b=team_b_name,
                pm_team_a_token=pm_team_a_token,
                pm_team_b_token=pm_team_b_token,
                pf_team_a_market_id=pf_team_a[1]["market_id"],
                pf_team_a_yes_token=pf_team_a[1]["yes_token"],
                pf_team_a_no_token=pf_team_a[1]["no_token"],
                pf_team_b_market_id=pf_team_b[1]["market_id"],
                pf_team_b_yes_token=pf_team_b[1]["yes_token"],
                pf_team_b_no_token=pf_team_b[1]["no_token"],
            )

    raise ValueError(f"Game '{game_name}' not found in config")


def list_available_games() -> list[str]:
    """List all available games from config."""
    config_path = Path(__file__).parent / "nba.json"
    with open(config_path) as f:
        data = json.load(f)
    return [game["game"] for game in data["games"]]


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
    pm_book: Orderbook,
    fee_rate: float = PF_FEE_RATE,
) -> float:
    """Calculate max shares where arbitrage is still profitable.

    Binary search for max N where: PF_cost * fee + PM_cost < N
    """
    max_liquidity = min(
        sum(size for _, size in pf_book.asks),
        sum(size for _, size in pm_book.asks),
    )

    if max_liquidity <= 0:
        return 0

    lo, hi = 0.0, max_liquidity
    result = 0.0

    for _ in range(50):
        mid = (lo + hi) / 2
        if mid <= 0:
            break

        pf_cost = calc_buy_cost(pf_book, mid)
        pm_cost = calc_buy_cost(pm_book, mid)

        if pf_cost is None or pm_cost is None:
            hi = mid
            continue

        total_cost = pf_cost * fee_rate + pm_cost

        if total_cost < mid:  # Profitable
            result = mid
            lo = mid
        else:
            hi = mid

    return result


async def check_arbitrage(
    game_config: GameConfig,
    clients: dict,
) -> ArbOpportunity | None:
    """Check for arbitrage opportunity across all directions."""
    try:
        # Fetch all 4 orderbooks concurrently
        tasks = [
            clients["pm_team_a"].get_orderbook(),
            clients["pm_team_b"].get_orderbook(),
            clients["pf_team_a_yes"].get_orderbook(),
            clients["pf_team_b_yes"].get_orderbook(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch orderbook {i}: {result}")
                return None

        pm_a_book, pm_b_book, pf_a_book, pf_b_book = results

        best_opp = None
        best_profit = 0.0

        # Check Team A arbitrage
        if pf_a_book.best_ask and pf_a_book.best_bid and pm_a_book.best_ask and pm_b_book.best_ask:
            pf_a_yes_ask = pf_a_book.best_ask
            pf_a_no_ask = 1 - pf_a_book.best_bid  # NO ask = 1 - YES bid
            pm_a_yes_ask = pm_a_book.best_ask
            pm_a_no_ask = pm_b_book.best_ask  # Team A NO = Team B YES

            # Direction 1: PF YES + PM NO
            cost1 = pf_a_yes_ask * PF_FEE_RATE + pm_a_no_ask
            profit1 = 1 - cost1
            if profit1 > best_profit:
                min_price = min(pf_a_yes_ask, pm_a_no_ask)
                min_shares = PM_MIN_ORDER_VALUE / min_price if min_price > 0 else float('inf')
                max_shares = calc_max_shares(pf_a_book, pm_b_book)
                best_opp = ArbOpportunity(
                    game=game_config.game_name,
                    team=game_config.team_a,
                    direction="pf_yes_pm_no",
                    pf_ask=pf_a_yes_ask,
                    pm_ask=pm_a_no_ask,
                    total_cost=cost1,
                    profit_rate=profit1,
                    min_shares=min_shares,
                    max_shares=max_shares,
                    timestamp=time(),
                )
                best_profit = profit1

            # Direction 2: PF NO + PM YES
            cost2 = pf_a_no_ask * PF_FEE_RATE + pm_a_yes_ask
            profit2 = 1 - cost2
            if profit2 > best_profit:
                min_price = min(pf_a_no_ask, pm_a_yes_ask)
                min_shares = PM_MIN_ORDER_VALUE / min_price if min_price > 0 else float('inf')
                # For PF NO, we need to use the NO orderbook
                pf_no_book = Orderbook(
                    bids=[(1 - p, s) for p, s in pf_a_book.asks],
                    asks=[(1 - p, s) for p, s in pf_a_book.bids],
                )
                max_shares = calc_max_shares(pf_no_book, pm_a_book)
                best_opp = ArbOpportunity(
                    game=game_config.game_name,
                    team=game_config.team_a,
                    direction="pf_no_pm_yes",
                    pf_ask=pf_a_no_ask,
                    pm_ask=pm_a_yes_ask,
                    total_cost=cost2,
                    profit_rate=profit2,
                    min_shares=min_shares,
                    max_shares=max_shares,
                    timestamp=time(),
                )
                best_profit = profit2

        # Check Team B arbitrage
        if pf_b_book.best_ask and pf_b_book.best_bid and pm_b_book.best_ask and pm_a_book.best_ask:
            pf_b_yes_ask = pf_b_book.best_ask
            pf_b_no_ask = 1 - pf_b_book.best_bid
            pm_b_yes_ask = pm_b_book.best_ask
            pm_b_no_ask = pm_a_book.best_ask  # Team B NO = Team A YES

            # Direction 1: PF YES + PM NO
            cost1 = pf_b_yes_ask * PF_FEE_RATE + pm_b_no_ask
            profit1 = 1 - cost1
            if profit1 > best_profit:
                min_price = min(pf_b_yes_ask, pm_b_no_ask)
                min_shares = PM_MIN_ORDER_VALUE / min_price if min_price > 0 else float('inf')
                max_shares = calc_max_shares(pf_b_book, pm_a_book)
                best_opp = ArbOpportunity(
                    game=game_config.game_name,
                    team=game_config.team_b,
                    direction="pf_yes_pm_no",
                    pf_ask=pf_b_yes_ask,
                    pm_ask=pm_b_no_ask,
                    total_cost=cost1,
                    profit_rate=profit1,
                    min_shares=min_shares,
                    max_shares=max_shares,
                    timestamp=time(),
                )
                best_profit = profit1

            # Direction 2: PF NO + PM YES
            cost2 = pf_b_no_ask * PF_FEE_RATE + pm_b_yes_ask
            profit2 = 1 - cost2
            if profit2 > best_profit:
                min_price = min(pf_b_no_ask, pm_b_yes_ask)
                min_shares = PM_MIN_ORDER_VALUE / min_price if min_price > 0 else float('inf')
                pf_no_book = Orderbook(
                    bids=[(1 - p, s) for p, s in pf_b_book.asks],
                    asks=[(1 - p, s) for p, s in pf_b_book.bids],
                )
                max_shares = calc_max_shares(pf_no_book, pm_b_book)
                best_opp = ArbOpportunity(
                    game=game_config.game_name,
                    team=game_config.team_b,
                    direction="pf_no_pm_yes",
                    pf_ask=pf_b_no_ask,
                    pm_ask=pm_b_yes_ask,
                    total_cost=cost2,
                    profit_rate=profit2,
                    min_shares=min_shares,
                    max_shares=max_shares,
                    timestamp=time(),
                )
                best_profit = profit2

        return best_opp

    except Exception as e:
        logger.error(f"Error checking arbitrage: {e}")
        return None


async def execute_arbitrage(
    opp: ArbOpportunity,
    game_config: GameConfig,
    clients: dict,
    shares: float,
) -> bool:
    """Execute arbitrage by placing orders on both platforms."""
    logger.info(f"Executing arbitrage: {opp.team} {opp.direction} - {shares:.2f} shares")

    success = True

    # Determine which clients to use
    is_team_a = opp.team == game_config.team_a

    if opp.direction == "pf_yes_pm_no":
        # Buy PF YES + PM opponent YES
        pf_client = clients["pf_team_a_yes"] if is_team_a else clients["pf_team_b_yes"]
        pm_client = clients["pm_team_b"] if is_team_a else clients["pm_team_a"]
    else:  # pf_no_pm_yes
        # Buy PF NO + PM team YES
        pf_client = clients["pf_team_a_no"] if is_team_a else clients["pf_team_b_no"]
        pm_client = clients["pm_team_a"] if is_team_a else clients["pm_team_b"]

    # Place PF order
    try:
        order = await pf_client.place_market_order(Side.BUY, size=shares)
        logger.info(f"PF order placed: {order.id[:30]}...")
    except Exception as e:
        logger.error(f"Failed to place PF order: {e}")
        success = False

    # Place PM order
    try:
        order = await pm_client.place_market_order(Side.BUY, size=shares)
        logger.info(f"PM order placed: {order.id[:30]}...")
    except Exception as e:
        logger.error(f"Failed to place PM order: {e}")
        success = False

    return success


async def monitor_loop(game_config: GameConfig):
    """Main monitoring loop with persistent client connections."""
    logger.info(f"Starting NBA arbitrage monitor for {game_config.game_name}")
    logger.info(f"Teams: {game_config.team_a} vs {game_config.team_b}")
    logger.info(f"Config: threshold={MIN_PROFIT_THRESHOLD:.1%}, interval={CHECK_INTERVAL}s")

    # Load configs
    pf_config = PredictFunConfig.from_env()
    pm_config = PolymarketConfig.from_env()

    # Create TG notifier
    tg = TelegramNotifier()
    if tg.is_configured:
        logger.info("Telegram notifications enabled")
    else:
        logger.info("Telegram not configured, notifications disabled")

    # Create clients (6 per game: 4 PF + 2 PM)
    clients = {
        "pm_team_a": PolymarketClient(token_id=game_config.pm_team_a_token, config=pm_config),
        "pm_team_b": PolymarketClient(token_id=game_config.pm_team_b_token, config=pm_config),
        "pf_team_a_yes": PredictFunClient(
            market_id=game_config.pf_team_a_market_id,
            token_id=game_config.pf_team_a_yes_token,
            is_yes=True,
            config=pf_config,
        ),
        "pf_team_a_no": PredictFunClient(
            market_id=game_config.pf_team_a_market_id,
            token_id=game_config.pf_team_a_no_token,
            is_yes=False,
            config=pf_config,
        ),
        "pf_team_b_yes": PredictFunClient(
            market_id=game_config.pf_team_b_market_id,
            token_id=game_config.pf_team_b_yes_token,
            is_yes=True,
            config=pf_config,
        ),
        "pf_team_b_no": PredictFunClient(
            market_id=game_config.pf_team_b_market_id,
            token_id=game_config.pf_team_b_no_token,
            is_yes=False,
            config=pf_config,
        ),
    }

    try:
        # Connect all clients
        logger.info("Connecting to PM (2 clients)...")
        await clients["pm_team_a"].connect()
        await clients["pm_team_b"].connect()

        logger.info("Connecting to PF (4 clients)...")
        for name, client in clients.items():
            if name.startswith("pf_"):
                await client.connect()
                logger.debug(f"{name} connected")

        logger.info("All clients connected")

        # Check initial balances
        pf_balance = await clients["pf_team_a_yes"].get_balance()
        pm_balance = await clients["pm_team_a"].get_balance()
        logger.info(f"Balances: PF={pf_balance:.2f} USDT, PM={pm_balance:.2f} USDC")

        logger.info("Starting monitor loop")

        # Monitor loop
        while True:
            opp = await check_arbitrage(game_config, clients)

            if opp:
                pf_cost = opp.pf_ask * PF_FEE_RATE
                min_cost = opp.min_shares * opp.total_cost
                max_cost = opp.max_shares * opp.total_cost
                logger.info(
                    f"{opp.team} {opp.direction}: "
                    f"PF={pf_cost:.4f} PM={opp.pm_ask:.4f} "
                    f"Total={opp.total_cost:.4f} Profit={opp.profit_rate:.2%} | "
                    f"Shares=[{opp.min_shares:.1f}, {opp.max_shares:.1f}] "
                    f"Cost=[${min_cost:.2f}, ${max_cost:.2f}]"
                )

                if opp.profit_rate >= MIN_PROFIT_THRESHOLD:
                    if opp.max_shares < opp.min_shares:
                        logger.warning(
                            f"Arb not feasible: max_shares({opp.max_shares:.1f}) < "
                            f"min_shares({opp.min_shares:.1f})"
                        )
                    else:
                        logger.info(f"ARBITRAGE FOUND! {opp.team} {opp.direction} Profit: {opp.profit_rate:.2%}")

                        shares = math.ceil(opp.min_shares)

                        if shares > opp.max_shares:
                            logger.warning(f"min_shares({shares}) > max_shares({opp.max_shares:.1f}), skipping")
                        else:
                            total_usd = shares * opp.total_cost
                            pf_needed = shares * opp.pf_ask * PF_FEE_RATE
                            pm_needed = shares * opp.pm_ask
                            expected_profit = shares * opp.profit_rate

                            # Check balances
                            pf_balance = await clients["pf_team_a_yes"].get_balance()
                            pm_balance = await clients["pm_team_a"].get_balance()

                            if pf_balance < pf_needed:
                                logger.warning(f"PF balance insufficient: {pf_balance:.2f} < {pf_needed:.2f}")
                                await tg.send(f"<b>NBA Arb Skipped</b>\nPF balance: ${pf_balance:.2f} < ${pf_needed:.2f}")
                                await asyncio.sleep(CHECK_INTERVAL)
                                continue

                            if pm_balance < pm_needed:
                                logger.warning(f"PM balance insufficient: {pm_balance:.2f} < {pm_needed:.2f}")
                                await tg.send(f"<b>NBA Arb Skipped</b>\nPM balance: ${pm_balance:.2f} < ${pm_needed:.2f}")
                                await asyncio.sleep(CHECK_INTERVAL)
                                continue

                            await tg.send(
                                f"<b>NBA Arb Found</b>\n"
                                f"Game: {opp.game}\n"
                                f"Team: {opp.team} ({opp.direction})\n"
                                f"Profit: {opp.profit_rate:.2%} (${expected_profit:.2f})\n"
                                f"Shares: {shares} @ ${total_usd:.2f}\n"
                                f"PF: ${pf_needed:.2f} | PM: ${pm_needed:.2f}"
                            )

                            logger.info(f"Buying {shares} shares for ${total_usd:.2f}, expected profit ${expected_profit:.2f}")

                            success = await execute_arbitrage(opp, game_config, clients, shares)
                            if success:
                                logger.info("Arbitrage executed successfully!")
                                await tg.send(f"<b>NBA Arb Executed</b>\n{shares} shares @ ${total_usd:.2f}")
                            else:
                                logger.warning("Arbitrage execution had some failures")
                                await tg.send(f"<b>NBA Arb Failed</b>\nSome orders failed")

            await asyncio.sleep(CHECK_INTERVAL)

    finally:
        logger.info("Closing connections...")
        await tg.close()
        for client in clients.values():
            await client.close()


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(description="NBA arbitrage monitor")
    parser.add_argument("--game", type=str, help="Game to monitor (e.g., 'Thunder vs Spurs')")
    parser.add_argument("--list", action="store_true", help="List available games")
    args = parser.parse_args()

    if args.list:
        games = list_available_games()
        print("Available games:")
        for game in games:
            print(f"  - {game}")
        return

    if not args.game:
        games = list_available_games()
        print("Please specify a game with --game")
        print("Available games:")
        for game in games:
            print(f"  - {game}")
        return

    try:
        config = load_game_config(args.game)
    except ValueError as e:
        print(f"Error: {e}")
        games = list_available_games()
        print("Available games:")
        for game in games:
            print(f"  - {game}")
        return

    logger.info(f"Loaded config for {config.game_name}")

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
