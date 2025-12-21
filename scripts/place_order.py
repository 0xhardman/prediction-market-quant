#!/usr/bin/env python3
"""
Order placement tool for Polymarket and Predict.fun.

Supports both interactive mode and command-line mode.

Usage:
    # Interactive mode
    uv run python scripts/place_order.py

    # Command-line mode (PM)
    uv run python scripts/place_order.py --platform pm --market <url/slug/condition_id> \\
        --token yes --side buy --size 5 --type market

    # Command-line mode (PF)
    uv run python scripts/place_order.py --platform pf --market 415 \\
        --token yes --side buy --value 1.0 --type market

Examples:
    # PM market order
    uv run python scripts/place_order.py -p pm -s buy -z 5 -t market

    # PF limit order
    uv run python scripts/place_order.py -p pf -m 415 --token no -s sell -z 100 -t limit --price 0.6

    # Quick buy with default market
    uv run python scripts/place_order.py -p pm -s buy -z 5
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import httpx

from src.clients import PolymarketClient, PredictFunClient
from src.exceptions import InsufficientBalanceError, OrderRejectedError
from src.models import Side

# API endpoints
PM_CLOB_API = "https://clob.polymarket.com"
PM_GAMMA_API = "https://gamma-api.polymarket.com"
PF_API = "https://api.predict.fun/v1"

# Default markets
DEFAULT_PF_MARKET_ID = int(os.getenv("PF_TEST_MARKET_ID", "415"))


class OrderWizard:
    """Step-by-step order placement wizard."""

    def __init__(self):
        self.platform: str = ""
        self.market: dict = {}
        self.token_id: str = ""
        self.token_name: str = ""
        self.order_type: str = ""
        self.side: Side = Side.BUY
        self.size: float = 0
        self.price: float = 0
        self.value: float | None = None

    def clear_screen(self):
        """Clear terminal screen."""
        os.system("clear" if os.name == "posix" else "cls")

    def print_header(self, step: int, title: str):
        """Print step header."""
        print("\n" + "=" * 60)
        print(f"  Step {step}: {title}")
        print("=" * 60)
        print("  (q: quit, b: back)\n")

    def get_input(self, prompt: str, default: str = "") -> str:
        """Get user input with quit/back support."""
        suffix = f" [{default}]" if default else ""
        try:
            value = input(f"  {prompt}{suffix}: ").strip()
        except (KeyboardInterrupt, EOFError):
            return "q"

        if value.lower() == "q":
            return "q"
        if value.lower() == "b":
            return "b"
        return value or default

    # ========== Step 1: Platform ==========
    async def step_platform(self) -> str:
        """Select platform."""
        self.print_header(1, "Select Platform")
        print("  1. Polymarket (PM)")
        print("  2. Predict.fun (PF)")
        print()

        choice = self.get_input("Choice", "1")
        if choice in ("q", "b"):
            return choice

        if choice == "1":
            self.platform = "pm"
        elif choice == "2":
            self.platform = "pf"
        else:
            print("  Invalid choice")
            return await self.step_platform()

        return "next"

    # ========== Step 2: Market ==========
    async def step_market(self) -> str:
        """Select market."""
        self.print_header(2, "Select Market")

        if self.platform == "pm":
            return await self._step_market_pm()
        else:
            return await self._step_market_pf()

    async def _step_market_pm(self) -> str:
        """Select PM market."""
        print("  Enter market identifier:")
        print("    - URL: https://polymarket.com/event/.../market-slug")
        print("    - Slug: market-slug")
        print("    - Condition ID: 0x...")
        print("    - (empty): Use random active market")
        print()

        identifier = self.get_input("Market")
        if identifier in ("q", "b"):
            return identifier

        async with httpx.AsyncClient(timeout=15) as http:
            if not identifier:
                # Get random active market
                print("\n  Fetching active markets...")
                resp = await http.get(f"{PM_CLOB_API}/sampling-markets")
                markets = resp.json().get("data", [])
                for m in markets[:20]:
                    if not m.get("closed") and m.get("tokens"):
                        self.market = {
                            "question": m.get("question", "Unknown"),
                            "condition_id": m.get("condition_id", ""),
                            "tokens": m.get("tokens", []),
                        }
                        break
            elif "polymarket.com" in identifier:
                # Extract slug from URL
                import re

                match = re.search(r"polymarket\.com/event/[^/]+/([^?]+)", identifier)
                if match:
                    slug = match.group(1).rstrip("\\")
                    resp = await http.get(
                        f"{PM_GAMMA_API}/markets", params={"slug": slug}
                    )
                    if resp.status_code == 200 and resp.json():
                        data = resp.json()[0]
                        self.market = {
                            "question": data.get("question", "Unknown"),
                            "condition_id": data.get("conditionId", ""),
                            "tokens": data.get("tokens", []),
                        }
            elif identifier.startswith("0x"):
                # Condition ID
                resp = await http.get(f"{PM_CLOB_API}/markets/{identifier}")
                if resp.status_code == 200:
                    data = resp.json()
                    self.market = {
                        "question": data.get("question", "Unknown"),
                        "condition_id": data.get("condition_id", identifier),
                        "tokens": data.get("tokens", []),
                    }
            else:
                # Slug
                resp = await http.get(
                    f"{PM_GAMMA_API}/markets", params={"slug": identifier}
                )
                if resp.status_code == 200 and resp.json():
                    data = resp.json()[0]
                    self.market = {
                        "question": data.get("question", "Unknown"),
                        "condition_id": data.get("conditionId", ""),
                        "tokens": data.get("tokens", []),
                    }

        if not self.market.get("tokens"):
            print("  ERROR: Market not found or has no tokens")
            return await self.step_market()

        print(f"\n  Market: {self.market['question'][:60]}...")
        return "next"

    async def _step_market_pf(self) -> str:
        """Select PF market."""
        print(f"  Enter market ID (default: {DEFAULT_PF_MARKET_ID}):")
        print()

        market_id_str = self.get_input("Market ID", str(DEFAULT_PF_MARKET_ID))
        if market_id_str in ("q", "b"):
            return market_id_str

        try:
            market_id = int(market_id_str)
        except ValueError:
            print("  Invalid market ID")
            return await self.step_market()

        async with httpx.AsyncClient(timeout=15) as http:
            print("\n  Fetching market info...")
            resp = await http.get(f"{PF_API}/markets/{market_id}")
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                outcomes = data.get("outcomes", [])
                self.market = {
                    "market_id": market_id,
                    "question": data.get("question", "Unknown"),
                    "tokens": [
                        {
                            "token_id": o.get("tokenId", ""),
                            "outcome": o.get("name", f"Outcome {i}"),
                        }
                        for i, o in enumerate(outcomes)
                    ],
                }
            else:
                print("  ERROR: Market not found")
                return await self.step_market()

        if not self.market.get("tokens"):
            print("  ERROR: Market has no tokens")
            return await self.step_market()

        print(f"\n  Market: {self.market['question'][:60]}...")
        return "next"

    # ========== Step 3: Token ==========
    async def step_token(self) -> str:
        """Select Yes/No token."""
        self.print_header(3, "Select Token")

        tokens = self.market.get("tokens", [])
        if not tokens:
            print("  ERROR: No tokens available")
            return "b"

        # Get prices for each token
        print("  Fetching prices...\n")

        for i, token in enumerate(tokens):
            if self.platform == "pm":
                token_id = token.get("token_id", "")
                outcome = token.get("outcome", f"Token {i+1}")
            else:
                token_id = token.get("token_id", "")
                outcome = token.get("outcome", f"Token {i+1}")

            # Get orderbook to show price
            try:
                if self.platform == "pm":
                    async with httpx.AsyncClient(timeout=10) as http:
                        resp = await http.get(
                            f"{PM_CLOB_API}/book", params={"token_id": token_id}
                        )
                        if resp.status_code == 200:
                            book = resp.json()
                            best_bid = float(book["bids"][0]["price"]) if book.get("bids") else 0
                            best_ask = float(book["asks"][0]["price"]) if book.get("asks") else 0
                            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
                            print(f"  {i+1}. {outcome}")
                            print(f"     Bid: {best_bid:.3f}  Ask: {best_ask:.3f}  Mid: {mid:.3f}")
                else:
                    # PF uses market_id for orderbook
                    async with httpx.AsyncClient(timeout=10) as http:
                        market_id = self.market.get("market_id")
                        resp = await http.get(f"{PF_API}/markets/{market_id}/orderbook")
                        if resp.status_code == 200:
                            book = resp.json().get("data", {})
                            bids = book.get("bids", [])
                            asks = book.get("asks", [])
                            best_bid = float(bids[0][0]) if bids else 0
                            best_ask = float(asks[0][0]) if asks else 0
                            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
                            print(f"  {i+1}. {outcome}")
                            print(f"     Bid: {best_bid:.3f}  Ask: {best_ask:.3f}  Mid: {mid:.3f}")
            except Exception as e:
                print(f"  {i+1}. {outcome} (price unavailable: {e})")

        print()
        choice = self.get_input("Choice", "1")
        if choice in ("q", "b"):
            return choice

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(tokens):
                selected = tokens[idx]
                self.token_id = selected.get("token_id", "")
                self.token_name = selected.get("outcome", f"Token {idx+1}")
                print(f"\n  Selected: {self.token_name}")
                return "next"
        except ValueError:
            pass

        print("  Invalid choice")
        return await self.step_token()

    # ========== Step 4: Order Type ==========
    async def step_order_type(self) -> str:
        """Select order type."""
        self.print_header(4, "Select Order Type")
        print("  1. Market Order (immediate execution)")
        print("  2. Limit Order (specify price)")
        print()

        choice = self.get_input("Choice", "1")
        if choice in ("q", "b"):
            return choice

        if choice == "1":
            self.order_type = "market"
        elif choice == "2":
            self.order_type = "limit"
        else:
            print("  Invalid choice")
            return await self.step_order_type()

        return "next"

    # ========== Step 5: Order Config ==========
    async def step_config(self) -> str:
        """Configure order parameters."""
        self.print_header(5, "Configure Order")

        # Side
        print("  Side:")
        print("    1. BUY")
        print("    2. SELL")
        print()

        side_choice = self.get_input("Side", "1")
        if side_choice in ("q", "b"):
            return side_choice

        self.side = Side.BUY if side_choice == "1" else Side.SELL

        # Size
        print()
        if self.platform == "pm":
            size_str = self.get_input("Size (shares, min 5)", "5")
        else:
            if self.side == Side.BUY:
                print("  For BUY: enter $ value (e.g., $10) or shares")
                size_str = self.get_input("Amount", "$1")
            else:
                size_str = self.get_input("Size (shares)", "100")

        if size_str in ("q", "b"):
            return size_str

        # Parse size
        if size_str.startswith("$"):
            self.value = float(size_str[1:])
            self.size = 0
        else:
            self.size = float(size_str)
            self.value = None

        # Price (for limit orders)
        if self.order_type == "limit":
            print()
            price_str = self.get_input("Price (0-1)", "0.50")
            if price_str in ("q", "b"):
                return price_str
            self.price = float(price_str)

        return "next"

    # ========== Step 6: Confirm ==========
    async def step_confirm(self) -> str:
        """Confirm and execute order."""
        self.print_header(6, "Confirm Order")

        print(f"  Platform:    {self.platform.upper()}")
        print(f"  Market:      {self.market.get('question', 'Unknown')[:50]}...")
        print(f"  Token:       {self.token_name}")
        print(f"  Order Type:  {self.order_type.upper()}")
        print(f"  Side:        {self.side.value}")
        if self.value:
            print(f"  Value:       ${self.value:.2f}")
        else:
            print(f"  Size:        {self.size} shares")
        if self.order_type == "limit":
            print(f"  Price:       {self.price:.4f}")
        print()

        confirm = self.get_input("Execute order? (y/n)", "n")
        if confirm in ("q", "b"):
            return confirm

        if confirm.lower() != "y":
            print("  Cancelled")
            return "q"

        return await self.execute_order()

    async def execute_order(self) -> str:
        """Execute the order."""
        print("\n" + "-" * 40)
        print("  Executing order...")
        print("-" * 40)

        try:
            if self.platform == "pm":
                return await self._execute_pm_order()
            else:
                return await self._execute_pf_order()
        except InsufficientBalanceError:
            print("\n  ERROR: Insufficient balance")
            return "q"
        except OrderRejectedError as e:
            print(f"\n  ERROR: Order rejected - {e.reason}")
            return "q"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            import traceback
            traceback.print_exc()
            return "q"

    async def _execute_pm_order(self) -> str:
        """Execute PM order."""
        async with PolymarketClient(token_id=self.token_id) as client:
            print("\n  Connected to Polymarket")

            # Get balance
            balance_before = await client.get_balance()
            print(f"  Balance: {balance_before:.4f} USDC")

            # Get orderbook for display
            ob = await client.get_orderbook()
            print(f"  Best Bid: {ob.best_bid:.4f}" if ob.best_bid else "  Best Bid: None")
            print(f"  Best Ask: {ob.best_ask:.4f}" if ob.best_ask else "  Best Ask: None")

            if self.order_type == "market":
                # Market order: client auto-fetches price
                order_type_str = "FOK"
                print(f"\n  Placing {order_type_str} {self.side.value} order...")
                print(f"    {self.size} shares (market price)")

                order = await client.place_order(
                    side=self.side,
                    size=self.size,
                    order_type=order_type_str,
                )
            else:
                # Limit order: use specified price
                order_type_str = "GTC"
                print(f"\n  Placing {order_type_str} {self.side.value} order...")
                print(f"    {self.size} shares @ {self.price:.4f}")

                order = await client.place_order(
                    side=self.side,
                    price=self.price,
                    size=self.size,
                    order_type=order_type_str,
                )

            print(f"\n  Order placed!")
            print(f"    ID: {order.id[:40]}...")
            print(f"    Status: {order.status.value}")

            balance_after = await client.get_balance()
            print(f"\n  Balance after: {balance_after:.4f} USDC")
            print(f"  Delta: {balance_after - balance_before:.4f} USDC")

        return "q"

    async def _execute_pf_order(self) -> str:
        """Execute PF order."""
        market_id = self.market.get("market_id")

        async with PredictFunClient(
            market_id=market_id, token_id=self.token_id
        ) as client:
            print("\n  Connected to Predict.fun")

            # Get balance
            balance_before = await client.get_balance()
            print(f"  Balance: {balance_before:.4f} USDT")

            # Get orderbook
            ob = await client.get_orderbook()
            print(f"  Best Bid: {ob.best_bid:.4f}" if ob.best_bid else "  Best Bid: None")
            print(f"  Best Ask: {ob.best_ask:.4f}" if ob.best_ask else "  Best Ask: None")

            if self.order_type == "market":
                print(f"\n  Placing MARKET {self.side.value} order...")
                if self.value:
                    print(f"    Value: ${self.value:.2f}")
                    order = await client.place_market_order(
                        side=self.side, value=self.value
                    )
                else:
                    print(f"    Size: {self.size} shares")
                    order = await client.place_market_order(
                        side=self.side, size=self.size
                    )
            else:
                print(f"\n  Placing LIMIT {self.side.value} order...")
                print(f"    {self.size} shares @ {self.price:.4f}")
                order = await client.place_order(
                    side=self.side,
                    price=self.price,
                    size=self.size,
                )

            print(f"\n  Order placed!")
            print(f"    ID: {order.id[:40]}...")
            print(f"    Status: {order.status.value}")
            print(f"    Price: {order.price:.4f}")

            balance_after = await client.get_balance()
            print(f"\n  Balance after: {balance_after:.4f} USDT")
            print(f"  Delta: {balance_after - balance_before:.4f} USDT")

        return "q"

    async def run(self):
        """Run the wizard."""
        steps = [
            self.step_platform,
            self.step_market,
            self.step_token,
            self.step_order_type,
            self.step_config,
            self.step_confirm,
        ]

        current_step = 0

        while current_step < len(steps):
            result = await steps[current_step]()

            if result == "q":
                print("\n  Goodbye!")
                break
            elif result == "b":
                current_step = max(0, current_step - 1)
            else:
                current_step += 1


async def run_cli(args):
    """Run in command-line mode."""
    wizard = OrderWizard()
    wizard.platform = args.platform

    print("\n" + "=" * 60)
    print(f"  {args.platform.upper()} Order - CLI Mode")
    print("=" * 60)

    # Step 1: Set platform (already done)

    # Step 2: Get market
    if args.platform == "pm":
        async with httpx.AsyncClient(timeout=15) as http:
            if not args.market:
                # Get random active market
                print("\n  Fetching active market...")
                resp = await http.get(f"{PM_CLOB_API}/sampling-markets")
                markets = resp.json().get("data", [])
                for m in markets[:20]:
                    if not m.get("closed") and m.get("tokens"):
                        wizard.market = {
                            "question": m.get("question", "Unknown"),
                            "condition_id": m.get("condition_id", ""),
                            "tokens": m.get("tokens", []),
                        }
                        break
            elif args.market.startswith("0x"):
                resp = await http.get(f"{PM_CLOB_API}/markets/{args.market}")
                if resp.status_code == 200:
                    data = resp.json()
                    wizard.market = {
                        "question": data.get("question", "Unknown"),
                        "condition_id": data.get("condition_id", args.market),
                        "tokens": data.get("tokens", []),
                    }
            else:
                # Try as slug
                resp = await http.get(f"{PM_GAMMA_API}/markets", params={"slug": args.market})
                if resp.status_code == 200 and resp.json():
                    data = resp.json()[0]
                    wizard.market = {
                        "question": data.get("question", "Unknown"),
                        "condition_id": data.get("conditionId", ""),
                        "tokens": data.get("tokens", []),
                    }
    else:
        # PF
        market_id = int(args.market) if args.market else DEFAULT_PF_MARKET_ID
        async with httpx.AsyncClient(timeout=15) as http:
            print(f"\n  Fetching market {market_id}...")
            resp = await http.get(f"{PF_API}/markets/{market_id}")
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                outcomes = data.get("outcomes", [])
                wizard.market = {
                    "market_id": market_id,
                    "question": data.get("question", "Unknown"),
                    "tokens": [
                        {"token_id": o.get("tokenId", ""), "outcome": o.get("name", f"Outcome {i}")}
                        for i, o in enumerate(outcomes)
                    ],
                }

    if not wizard.market.get("tokens"):
        print("  ERROR: Market not found or has no tokens")
        return

    print(f"  Market: {wizard.market['question'][:50]}...")

    # Step 3: Select token
    tokens = wizard.market.get("tokens", [])
    token_idx = 0 if args.token == "yes" else 1 if args.token == "no" else 0
    if token_idx < len(tokens):
        wizard.token_id = tokens[token_idx].get("token_id", "")
        wizard.token_name = tokens[token_idx].get("outcome", f"Token {token_idx}")
    else:
        wizard.token_id = tokens[0].get("token_id", "")
        wizard.token_name = tokens[0].get("outcome", "Token 0")

    print(f"  Token: {wizard.token_name}")

    # Step 4: Order type
    wizard.order_type = args.type or "market"

    # Step 5: Configure order
    wizard.side = Side.BUY if args.side == "buy" else Side.SELL
    wizard.size = args.size or 0
    wizard.value = args.value
    wizard.price = args.price or 0.5

    # Print summary
    print(f"\n  Order Type: {wizard.order_type.upper()}")
    print(f"  Side: {wizard.side.value}")
    if wizard.value:
        print(f"  Value: ${wizard.value:.2f}")
    elif wizard.size:
        print(f"  Size: {wizard.size} shares")
    if wizard.order_type == "limit":
        print(f"  Price: {wizard.price:.4f}")

    # Confirm
    if not args.yes:
        confirm = input("\n  Execute? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled")
            return

    # Execute
    await wizard.execute_order()


async def main():
    parser = argparse.ArgumentParser(
        description="Order placement tool for PM/PF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  %(prog)s

  # PM market buy
  %(prog)s -p pm -s buy -z 5 -t market

  # PF limit sell
  %(prog)s -p pf -m 415 -s sell -z 100 -t limit --price 0.6

  # Quick order with auto-confirm
  %(prog)s -p pm -s buy -z 5 -y
        """,
    )
    parser.add_argument("-p", "--platform", choices=["pm", "pf"], help="Platform (pm/pf)")
    parser.add_argument("-m", "--market", help="Market ID/slug/URL")
    parser.add_argument("--token", choices=["yes", "no"], default="yes", help="Token (yes/no)")
    parser.add_argument("-s", "--side", choices=["buy", "sell"], help="Order side")
    parser.add_argument("-t", "--type", choices=["market", "limit"], help="Order type")
    parser.add_argument("-z", "--size", type=float, help="Order size (shares)")
    parser.add_argument("-v", "--value", type=float, help="Order value (USD, PF only)")
    parser.add_argument("--price", type=float, help="Limit price (0-1)")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    # If platform specified, run CLI mode
    if args.platform:
        if not args.side:
            parser.error("--side is required in CLI mode")
        if not args.size and not args.value:
            parser.error("--size or --value is required")
        await run_cli(args)
    else:
        # Interactive mode
        print("\n" + "=" * 60)
        print("  Order Placement Tool")
        print("  PM (Polymarket) / PF (Predict.fun)")
        print("=" * 60)

        wizard = OrderWizard()
        await wizard.run()


if __name__ == "__main__":
    asyncio.run(main())
