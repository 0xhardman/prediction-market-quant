"""Polymarket client with WebSocket and REST support."""

import asyncio
import json
import time
from typing import Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from ..config import Config
from ..models import Orderbook, OrderResult, OrderStatus, Platform, Side
from ..utils.logger import get_logger
from .base import BaseClient

# Try to import py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.order_builder.constants import BUY, SELL
    from py_clob_client.clob_types import OrderType, OrderArgs
    HAS_CLOB_CLIENT = True
except ImportError:
    HAS_CLOB_CLIENT = False
    ClobClient = None


class PolymarketClient(BaseClient):
    """Polymarket client with WebSocket orderbook subscription and REST trading."""

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    REST_URL = "https://clob.polymarket.com"

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()

        # Orderbook cache
        self._orderbooks: Dict[str, Orderbook] = {}

        # WebSocket connection
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._subscribed_tokens: set[str] = set()
        self._running = False

        # CLOB client for trading
        self._clob_client: Optional[ClobClient] = None

        # Initialize CLOB client if credentials available
        if HAS_CLOB_CLIENT and config.credentials.polymarket.private_key:
            self._init_clob_client()

    def _init_clob_client(self) -> None:
        """Initialize the CLOB client for trading."""
        try:
            creds = self.config.credentials.polymarket
            self._clob_client = ClobClient(
                self.REST_URL,
                key=creds.private_key,
                chain_id=137,  # Polygon
                signature_type=2,  # POLY_GNOSIS_SAFE
                funder=creds.proxy_address if creds.proxy_address else None,
            )

            # Set API credentials - 优先使用已配置的凭据
            if creds.api_key and creds.api_secret and creds.api_passphrase:
                from py_clob_client.clob_types import ApiCreds
                api_creds = ApiCreds(
                    api_key=creds.api_key,
                    api_secret=creds.api_secret,
                    api_passphrase=creds.api_passphrase,
                )
                self.logger.info(f"Using configured API credentials (key: {creds.api_key[:20]}...)")
            else:
                api_creds = self._clob_client.create_or_derive_api_creds()
                self.logger.info("Derived new API credentials")
            self._clob_client.set_api_creds(api_creds)

            self.logger.info("Polymarket CLOB client initialized")
        except Exception as e:
            self.logger.error(f"Failed to initialize CLOB client: {e}")
            self._clob_client = None

    async def connect(self) -> None:
        """Connect to WebSocket and start receiving orderbook updates."""
        if self._running:
            return

        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        self.logger.info("Polymarket WebSocket connection started")

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        self.logger.info("Polymarket WebSocket disconnected")

    async def _ws_loop(self) -> None:
        """WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                async with websockets.connect(self.WS_URL) as ws:
                    self._ws = ws
                    self.logger.info("WebSocket connected")

                    # Resubscribe to tokens
                    for token_id in self._subscribed_tokens:
                        await self._send_subscribe(token_id)

                    # Process messages
                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as e:
                self.logger.warning(f"WebSocket closed: {e}")
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")

            if self._running:
                self.logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    async def _send_subscribe(self, token_id: str) -> None:
        """Send subscription message for a token."""
        if not self._ws:
            return

        message = {
            "type": "subscribe",
            "assets_ids": [token_id],
        }
        await self._ws.send(json.dumps(message))
        self.logger.debug(f"Subscribed to token: {token_id}")

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Handle list (batch of orderbook snapshots)
            if isinstance(data, list):
                for item in data:
                    await self._update_orderbook(item)
                return

            # Handle dict messages
            if "price_changes" in data:
                # Price change notification - update from price_changes array
                for change in data.get("price_changes", []):
                    await self._update_price_change(data.get("market"), change)
            elif "asset_id" in data:
                # Single orderbook update
                await self._update_orderbook(data)
            elif data.get("type") == "error":
                self.logger.error(f"WS error: {data.get('message', 'Unknown error')}")

        except json.JSONDecodeError:
            self.logger.warning(f"Invalid JSON message: {message[:100]}")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    async def _update_orderbook(self, data: dict) -> None:
        """Update orderbook cache from WebSocket snapshot."""
        token_id = data.get("asset_id")
        if not token_id:
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        bid_size = float(bids[0]["size"]) if bids else 0.0
        ask_size = float(asks[0]["size"]) if asks else 0.0

        self._orderbooks[token_id] = Orderbook(
            platform=Platform.POLYMARKET,
            token_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            timestamp=time.time(),
        )

    async def _update_price_change(self, market: str, change: dict) -> None:
        """Update orderbook from price change message."""
        token_id = change.get("asset_id")
        if not token_id:
            return

        best_bid = float(change.get("best_bid", 0))
        best_ask = float(change.get("best_ask", 1))

        # Update existing orderbook or create new one
        if token_id in self._orderbooks:
            ob = self._orderbooks[token_id]
            self._orderbooks[token_id] = Orderbook(
                platform=Platform.POLYMARKET,
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size=ob.bid_size,  # Keep previous size
                ask_size=ob.ask_size,
                timestamp=time.time(),
            )
        else:
            self._orderbooks[token_id] = Orderbook(
                platform=Platform.POLYMARKET,
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size=0.0,
                ask_size=0.0,
                timestamp=time.time(),
            )

    async def subscribe_orderbook(self, token_id: str) -> None:
        """Subscribe to orderbook updates for a token."""
        self._subscribed_tokens.add(token_id)

        if self._ws:
            await self._send_subscribe(token_id)

        # Also fetch initial orderbook via REST
        await self._fetch_orderbook_rest(token_id)

    async def _fetch_orderbook_rest(self, token_id: str) -> None:
        """Fetch orderbook via REST API."""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.REST_URL}/book",
                    params={"token_id": token_id},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()

                bids = data.get("bids", [])
                asks = data.get("asks", [])

                best_bid = float(bids[0]["price"]) if bids else 0.0
                best_ask = float(asks[0]["price"]) if asks else 1.0
                bid_size = float(bids[0]["size"]) if bids else 0.0
                ask_size = float(asks[0]["size"]) if asks else 0.0

                self._orderbooks[token_id] = Orderbook(
                    platform=Platform.POLYMARKET,
                    token_id=token_id,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    timestamp=time.time(),
                )
        except Exception as e:
            self.logger.error(f"Failed to fetch orderbook for {token_id}: {e}")

    async def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get cached orderbook for a token."""
        return self._orderbooks.get(token_id)

    async def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a limit order (uses FOK type)."""
        return await self.place_fok_order(token_id, side, price, size)

    async def place_fok_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a Fill-Or-Kill order."""
        if not self._clob_client:
            self.logger.error("CLOB client not initialized")
            return OrderResult(
                order_id="",
                platform=Platform.POLYMARKET,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

        try:
            # Convert USD size to shares
            # size is in USD, shares = usd / price
            shares = size / price if price > 0 else size

            # Create order args
            clob_side = BUY if side == Side.BUY else SELL
            order_args = OrderArgs(
                price=price,
                size=shares,
                side=clob_side,
                token_id=token_id,
            )

            # Create and post order
            signed_order = self._clob_client.create_order(order_args)
            resp = self._clob_client.post_order(signed_order, OrderType.FOK)

            # Parse response
            order_id = resp.get("orderID", "")
            status_str = resp.get("status", "")

            if status_str == "matched":
                status = OrderStatus.FILLED
                filled_size = size  # Return USD size
            elif status_str == "live":
                status = OrderStatus.PENDING
                filled_size = 0.0
            else:
                status = OrderStatus.CANCELLED
                filled_size = 0.0

            self.logger.info(
                f"PM Order {order_id}: {side.value} ${size:.2f} ({shares:.1f} shares) @ {price} -> {status.value}"
            )

            return OrderResult(
                order_id=order_id,
                platform=Platform.POLYMARKET,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=filled_size,
                status=status,
            )

        except Exception as e:
            self.logger.error(f"Failed to place order: {e}")
            return OrderResult(
                order_id="",
                platform=Platform.POLYMARKET,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if not self._clob_client:
            return False

        try:
            self._clob_client.cancel(order_id)
            self.logger.info(f"Cancelled order: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Get order status."""
        if not self._clob_client:
            return None

        try:
            order = self._clob_client.get_order(order_id)
            if not order:
                return None

            status_str = order.get("status", "")
            if status_str == "matched":
                status = OrderStatus.FILLED
            elif status_str == "live":
                status = OrderStatus.PENDING
            elif status_str == "cancelled":
                status = OrderStatus.CANCELLED
            else:
                status = OrderStatus.FAILED

            return OrderResult(
                order_id=order_id,
                platform=Platform.POLYMARKET,
                token_id=order.get("asset_id", ""),
                side=Side.BUY if order.get("side") == "BUY" else Side.SELL,
                price=float(order.get("price", 0)),
                requested_size=float(order.get("original_size", 0)),
                filled_size=float(order.get("size_matched", 0)),
                status=status,
            )
        except Exception as e:
            self.logger.error(f"Failed to get order status: {e}")
            return None
