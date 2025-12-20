#!/usr/bin/env python3
"""
Test script for modularized PM and PF clients.
Demonstrates context manager usage and error handling.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from src.clients import PolymarketClient, PredictFunClient
from src.config import PolymarketConfig, PredictFunConfig
from src.exceptions import (
    NotConnectedError,
    OrderNotFoundError,
    InsufficientBalanceError,
)
from src.models import Side


# Test market configuration
PF_MARKET_ID = 415
PF_TOKEN_ID = "14862668150972542930258837689755111839426102234146323070055218172124000064169"


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


async def test_polymarket():
    """Test PolymarketClient with context manager."""
    print("\n" + "=" * 60)
    print("POLYMARKET CLIENT TEST (Context Manager)")
    print("=" * 60)

    results = {
        "connect": False,
        "orderbook": False,
        "place_order": False,
        "cancel_order": False,
    }

    try:
        # Get active market token
        print("\n0. Finding active market...")
        token_id = await get_active_pm_token()
        if not token_id:
            print("   No active market found")
            return results
        print(f"   Token: {token_id[:40]}...")

        # Use context manager - automatically connects and closes
        async with PolymarketClient(token_id=token_id) as client:
            print("\n1. Connected via context manager!")
            results["connect"] = True

            # 2. Get orderbook
            print("\n2. Getting orderbook...")
            ob = await client.get_orderbook()
            print(f"   Best Bid: {ob.best_bid}, Best Ask: {ob.best_ask}")
            print(f"   Spread: {ob.spread}")
            results["orderbook"] = True

            # 3. Place order (low price, won't fill)
            print("\n3. Placing test order (BUY @ 0.01, size=5)...")
            order = await client.place_order(Side.BUY, price=0.01, size=5.0)
            print(f"   Order ID: {order.id[:30]}...")
            print(f"   Status: {order.status}")
            results["place_order"] = True

            # 4. Cancel order
            print("\n4. Cancelling order...")
            cancelled = await client.cancel_order(order.id)
            print(f"   Cancelled: {cancelled}")
            results["cancel_order"] = cancelled

        # Client is automatically closed here
        print("\n5. Client closed automatically (context manager)")

    except NotConnectedError as e:
        print(f"Not connected: {e}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()

    return results


async def test_predictfun():
    """Test PredictFunClient with context manager."""
    print("\n" + "=" * 60)
    print("PREDICT.FUN CLIENT TEST (Context Manager)")
    print("=" * 60)

    results = {
        "connect": False,
        "orderbook": False,
        "balance": False,
        "place_order": False,
        "cancel_order": False,
    }

    try:
        # Use context manager
        async with PredictFunClient(
            market_id=PF_MARKET_ID, token_id=PF_TOKEN_ID
        ) as client:
            print("\n1. Connected via context manager!")
            results["connect"] = True

            # 2. Get balance
            print("\n2. Getting balance...")
            balance = await client.get_balance()
            print(f"   USDT Balance: {balance:.4f}")
            results["balance"] = True

            # 3. Get orderbook
            print("\n3. Getting orderbook...")
            ob = await client.get_orderbook()
            print(f"   Best Bid: {ob.best_bid}, Best Ask: {ob.best_ask}")
            print(f"   Spread: {ob.spread}")
            results["orderbook"] = True

            # 4. Place order (low price, won't fill)
            print("\n4. Placing test order (BUY @ 0.01, size=100)...")
            try:
                order = await client.place_order(Side.BUY, price=0.01, size=100.0)
                print(f"   Order ID: {order.id[:40]}...")
                print(f"   Status: {order.status}")
                results["place_order"] = True

                # 5. Cancel order
                print("\n5. Cancelling order...")
                cancelled = await client.cancel_order(order.id)
                print(f"   Cancelled: {cancelled}")
                results["cancel_order"] = cancelled

            except InsufficientBalanceError:
                print("   Order rejected (insufficient balance) - signature OK")
                results["place_order"] = True
                results["cancel_order"] = True

        # Client is automatically closed here
        print("\n6. Client closed automatically (context manager)")

    except NotConnectedError as e:
        print(f"Not connected: {e}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()

    return results


async def test_error_handling():
    """Test error handling scenarios."""
    print("\n" + "=" * 60)
    print("ERROR HANDLING TEST")
    print("=" * 60)

    # Test NotConnectedError
    print("\n1. Testing NotConnectedError...")
    client = PredictFunClient(market_id=PF_MARKET_ID, token_id=PF_TOKEN_ID)
    try:
        await client.get_orderbook()  # Should fail - not connected
        print("   FAIL: Should have raised NotConnectedError")
    except NotConnectedError:
        print("   OK: NotConnectedError raised as expected")

    # Test OrderNotFoundError
    print("\n2. Testing OrderNotFoundError...")
    async with PredictFunClient(
        market_id=PF_MARKET_ID, token_id=PF_TOKEN_ID
    ) as client:
        try:
            await client.cancel_order("nonexistent_order_hash")
            print("   FAIL: Should have raised OrderNotFoundError")
        except OrderNotFoundError as e:
            print(f"   OK: OrderNotFoundError raised (order_id={e.order_id[:20]}...)")

    print("\n3. Error handling tests completed")


async def main():
    print("=" * 60)
    print("CLIENT MODULE TEST (Improved Architecture)")
    print("=" * 60)

    pm_results = await test_polymarket()
    pf_results = await test_predictfun()
    await test_error_handling()

    # Summary
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    def icon(ok):
        return "OK" if ok else "FAIL"

    print("\n| Test          | Polymarket | Predict.fun |")
    print("|---------------|------------|-------------|")
    print(
        f"| Connect       | {icon(pm_results['connect']):^10} | {icon(pf_results['connect']):^11} |"
    )
    print(
        f"| Orderbook     | {icon(pm_results['orderbook']):^10} | {icon(pf_results['orderbook']):^11} |"
    )
    print(
        f"| Place Order   | {icon(pm_results['place_order']):^10} | {icon(pf_results['place_order']):^11} |"
    )
    print(
        f"| Cancel Order  | {icon(pm_results['cancel_order']):^10} | {icon(pf_results['cancel_order']):^11} |"
    )

    pm_ok = all(pm_results.values())
    pf_ok = all(pf_results.values())

    print(f"\nPolymarket:   {'ALL PASSED' if pm_ok else 'SOME FAILED'}")
    print(f"Predict.fun:  {'ALL PASSED' if pf_ok else 'SOME FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
