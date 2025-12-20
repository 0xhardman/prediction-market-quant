#!/usr/bin/env python3
"""
Market order testing script for Polymarket and Predict.fun.

PM: Uses FOK (Fill Or Kill) order type
PF: Uses MARKET strategy with orderbook-based price calculation

Usage:
    # Interactive mode
    uv run python scripts/test_market_order.py

    # Command line mode
    uv run python scripts/test_market_order.py --platform pm --side buy --size 5
    uv run python scripts/test_market_order.py --platform pf --side buy --value 1.0
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from src.clients import PolymarketClient, PredictFunClient
from src.exceptions import InsufficientBalanceError, OrderRejectedError
from src.models import Side


# Default test markets
PF_MARKET_ID = int(os.getenv("PF_TEST_MARKET_ID", "415"))
PF_TOKEN_ID = os.getenv(
    "PF_TEST_TOKEN_ID",
    "14862668150972542930258837689755111839426102234146323070055218172124000064169",
)


async def get_active_pm_token() -> str | None:
    """Get an active Polymarket token_id for testing."""
    import httpx

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            "https://clob.polymarket.com/sampling-markets", timeout=10
        )
        markets = resp.json().get("data", [])
        for m in markets[:20]:
            if m.get("closed"):
                continue
            tokens = m.get("tokens", [])
            if tokens:
                token_id = tokens[0].get("token_id", "")
                if token_id:
                    return token_id
    return None


def print_orderbook(ob, platform: str):
    """Print orderbook summary."""
    print(f"\n  {platform} Orderbook:")
    print(f"    Best Bid: {ob.best_bid:.4f}" if ob.best_bid else "    Best Bid: None")
    print(f"    Best Ask: {ob.best_ask:.4f}" if ob.best_ask else "    Best Ask: None")
    if ob.spread is not None:
        print(f"    Spread:   {ob.spread:.4f} ({ob.spread*100:.2f}%)")


async def test_pm_market_order(side: Side, size: float) -> bool:
    """Test Polymarket FOK market order."""
    print("\n" + "=" * 60)
    print("POLYMARKET MARKET ORDER (FOK)")
    print("=" * 60)

    try:
        print("\n1. Finding active market...")
        token_id = await get_active_pm_token()
        if not token_id:
            print("   ERROR: No active market found")
            return False
        print(f"   Token: {token_id[:40]}...")

        async with PolymarketClient(token_id=token_id) as client:
            print("\n2. Connected!")

            # Get orderbook
            print("\n3. Getting orderbook...")
            ob = await client.get_orderbook()
            print_orderbook(ob, "PM")

            # Determine price based on side
            if side == Side.BUY:
                if not ob.best_ask:
                    print("   ERROR: No asks in orderbook")
                    return False
                price = ob.best_ask
                print(f"\n4. Placing FOK BUY order: {size} shares @ {price:.4f}")
            else:
                if not ob.best_bid:
                    print("   ERROR: No bids in orderbook")
                    return False
                price = ob.best_bid
                print(f"\n4. Placing FOK SELL order: {size} shares @ {price:.4f}")

            # Get balance before
            balance_before = await client.get_balance()
            print(f"   Balance before: {balance_before:.4f} USDC")

            # Place FOK order
            order = await client.place_order(
                side=side,
                price=price,
                size=size,
                order_type="FOK",
            )

            print(f"\n5. Order result:")
            print(f"   Order ID: {order.id[:40]}...")
            print(f"   Status:   {order.status.value}")

            # Get balance after
            balance_after = await client.get_balance()
            print(f"   Balance after: {balance_after:.4f} USDC")
            print(f"   Delta: {balance_after - balance_before:.4f} USDC")

            return True

    except InsufficientBalanceError:
        print("   ERROR: Insufficient balance")
        return False
    except OrderRejectedError as e:
        print(f"   ERROR: Order rejected - {e.reason}")
        return False
    except Exception as e:
        print(f"   ERROR: {e}")
        return False


async def test_pf_market_order(
    side: Side, size: float | None = None, value: float | None = None
) -> bool:
    """Test Predict.fun MARKET order."""
    print("\n" + "=" * 60)
    print("PREDICT.FUN MARKET ORDER")
    print("=" * 60)

    try:
        print(f"\n1. Using market {PF_MARKET_ID}")
        print(f"   Token: {PF_TOKEN_ID[:40]}...")

        async with PredictFunClient(
            market_id=PF_MARKET_ID, token_id=PF_TOKEN_ID
        ) as client:
            print("\n2. Connected!")

            # Get orderbook
            print("\n3. Getting orderbook...")
            ob = await client.get_orderbook()
            print_orderbook(ob, "PF")

            # Get balance before
            balance_before = await client.get_balance()
            print(f"   Balance before: {balance_before:.4f} USDT")

            # Place market order
            if side == Side.BUY:
                if value is not None:
                    print(f"\n4. Placing MARKET BUY order: ${value:.2f}")
                    order = await client.place_market_order(side=side, value=value)
                else:
                    print(f"\n4. Placing MARKET BUY order: {size} shares")
                    order = await client.place_market_order(side=side, size=size)
            else:
                print(f"\n4. Placing MARKET SELL order: {size} shares")
                order = await client.place_market_order(side=side, size=size)

            print(f"\n5. Order result:")
            print(f"   Order ID: {order.id[:40]}...")
            print(f"   Status:   {order.status.value}")
            print(f"   Avg Price: {order.price:.4f}")

            # Get balance after
            balance_after = await client.get_balance()
            print(f"   Balance after: {balance_after:.4f} USDT")
            print(f"   Delta: {balance_after - balance_before:.4f} USDT")

            return True

    except InsufficientBalanceError:
        print("   ERROR: Insufficient balance")
        return False
    except OrderRejectedError as e:
        print(f"   ERROR: Order rejected - {e.reason}")
        return False
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


async def interactive_mode():
    """Run in interactive mode."""
    print("\n" + "=" * 60)
    print("MARKET ORDER TESTING - Interactive Mode")
    print("=" * 60)

    # Select platform
    print("\nSelect platform:")
    print("  1. Polymarket (PM)")
    print("  2. Predict.fun (PF)")
    print("  3. Both")

    choice = input("\nChoice [1/2/3]: ").strip()
    platforms = []
    if choice == "1":
        platforms = ["pm"]
    elif choice == "2":
        platforms = ["pf"]
    elif choice == "3":
        platforms = ["pm", "pf"]
    else:
        print("Invalid choice")
        return

    # Select side
    print("\nSelect side:")
    print("  1. BUY")
    print("  2. SELL")

    side_choice = input("\nChoice [1/2]: ").strip()
    if side_choice == "1":
        side = Side.BUY
    elif side_choice == "2":
        side = Side.SELL
    else:
        print("Invalid choice")
        return

    # Get amount
    if "pm" in platforms:
        pm_size = float(input("\nPM order size (shares, min 5): ").strip() or "5")
    else:
        pm_size = 5.0

    if "pf" in platforms:
        if side == Side.BUY:
            pf_input = input("\nPF order value (USD) or size (shares): ").strip()
            if pf_input.startswith("$"):
                pf_value = float(pf_input[1:])
                pf_size = None
            else:
                try:
                    pf_value = float(pf_input)
                    pf_size = None
                except ValueError:
                    pf_value = None
                    pf_size = float(pf_input)
        else:
            pf_size = float(input("\nPF order size (shares): ").strip())
            pf_value = None
    else:
        pf_size = None
        pf_value = None

    # Confirm
    print("\n" + "-" * 40)
    print("Order Summary:")
    if "pm" in platforms:
        print(f"  PM: {side.value} {pm_size} shares (FOK)")
    if "pf" in platforms:
        if pf_value is not None:
            print(f"  PF: {side.value} ${pf_value} (MARKET)")
        else:
            print(f"  PF: {side.value} {pf_size} shares (MARKET)")
    print("-" * 40)

    confirm = input("\nProceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled")
        return

    # Execute orders
    results = {}
    if "pm" in platforms:
        results["PM"] = await test_pm_market_order(side, pm_size)
    if "pf" in platforms:
        results["PF"] = await test_pf_market_order(side, pf_size, pf_value)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for platform, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        print(f"  {platform}: {status}")


async def main():
    parser = argparse.ArgumentParser(description="Market order testing script")
    parser.add_argument(
        "--platform",
        choices=["pm", "pf", "both"],
        help="Platform to test (pm/pf/both)",
    )
    parser.add_argument(
        "--side",
        choices=["buy", "sell"],
        help="Order side",
    )
    parser.add_argument(
        "--size",
        type=float,
        help="Order size in shares",
    )
    parser.add_argument(
        "--value",
        type=float,
        help="Order value in USD (PF BUY only)",
    )

    args = parser.parse_args()

    # If no arguments, run interactive mode
    if not args.platform:
        await interactive_mode()
        return

    # Command line mode
    side = Side.BUY if args.side == "buy" else Side.SELL
    platforms = ["pm", "pf"] if args.platform == "both" else [args.platform]

    results = {}
    if "pm" in platforms:
        if not args.size:
            print("ERROR: --size required for PM")
            return
        results["PM"] = await test_pm_market_order(side, args.size)

    if "pf" in platforms:
        if side == Side.BUY and not args.size and not args.value:
            print("ERROR: --size or --value required for PF BUY")
            return
        if side == Side.SELL and not args.size:
            print("ERROR: --size required for PF SELL")
            return
        results["PF"] = await test_pf_market_order(side, args.size, args.value)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for platform, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        print(f"  {platform}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
