"""Opinion client with REST API support."""

import asyncio
import time
from typing import Dict, Optional

import httpx
from aiolimiter import AsyncLimiter

from ..config import Config
from ..models import Orderbook, OrderResult, OrderStatus, Platform, Side
from ..utils.logger import get_logger
from .base import BaseClient

# Try to import opinion-clob-sdk
try:
    from opinion_clob_sdk import ClobClient as OpinionClobClient
    from opinion_clob_sdk import PlaceOrderDataInput, OrderSide
    HAS_OPINION_SDK = True
except ImportError:
    HAS_OPINION_SDK = False
    OpinionClobClient = None


class OpinionClient(BaseClient):
    """Opinion client with REST API for orderbook and trading."""

    BASE_URL = "https://proxy.opinion.trade:8443"
    OPENAPI_URL = "https://openapi.opinion.trade"

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()

        # Rate limiter: 15 req/s per API key
        self._limiter = AsyncLimiter(15, 1)

        # Orderbook cache
        self._orderbooks: Dict[str, Orderbook] = {}

        # HTTP client with proxy support
        self._http_client: Optional[httpx.AsyncClient] = None

        # Opinion SDK client
        self._sdk_client: Optional[OpinionClobClient] = None

        # API key
        self._api_key = config.credentials.opinion.api_key

    async def connect(self) -> None:
        """Initialize HTTP client."""
        # Setup proxy if enabled
        proxies = None
        if self.config.proxy.enabled and self.config.proxy.https:
            proxies = {
                "http://": self.config.proxy.http,
                "https://": self.config.proxy.https,
            }

        self._http_client = httpx.AsyncClient(
            timeout=30,
            proxies=proxies,
        )

        # Initialize SDK client if available
        if HAS_OPINION_SDK and self._api_key:
            try:
                self._sdk_client = OpinionClobClient(api_key=self._api_key)
                self.logger.info("Opinion SDK client initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize Opinion SDK: {e}")

        self.logger.info("Opinion client connected")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self.logger.info("Opinion client disconnected")

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Optional[dict]:
        """Make a rate-limited HTTP request."""
        if not self._http_client:
            await self.connect()

        async with self._limiter:
            try:
                # Add API key header
                headers = kwargs.pop("headers", {})
                if self._api_key:
                    headers["apikey"] = self._api_key

                resp = await self._http_client.request(
                    method,
                    url,
                    headers=headers,
                    **kwargs,
                )

                # Check for geo-restriction
                if resp.status_code == 403:
                    data = resp.json()
                    if data.get("errno") == 10403:
                        self.logger.error(
                            "Opinion API geo-restricted. Please use a proxy."
                        )
                        return None

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError as e:
                self.logger.error(f"HTTP error: {e}")
                return None
            except Exception as e:
                self.logger.error(f"Request error: {e}")
                return None

    async def fetch_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Fetch orderbook from REST API."""
        data = await self._request(
            "GET",
            f"{self.BASE_URL}/openapi/token/orderbook",
            params={"token_id": token_id},
        )

        if not data or data.get("code") != 0:
            return None

        result = data.get("result", {})
        bids = result.get("bids", [])
        asks = result.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        bid_size = float(bids[0]["size"]) if bids else 0.0
        ask_size = float(asks[0]["size"]) if asks else 0.0

        orderbook = Orderbook(
            platform=Platform.OPINION,
            token_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            timestamp=time.time(),
        )

        # Update cache
        self._orderbooks[token_id] = orderbook
        return orderbook

    async def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get orderbook (fetches fresh data)."""
        return await self.fetch_orderbook(token_id)

    async def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a limit order."""
        return await self.place_limit_order(token_id, side, price, size)

    async def place_limit_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
        market_id: Optional[str] = None,
    ) -> OrderResult:
        """Place a limit order using the SDK."""
        if not HAS_OPINION_SDK:
            self.logger.error("Opinion SDK not available")
            return OrderResult(
                order_id="",
                platform=Platform.OPINION,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

        if not self._sdk_client:
            self.logger.error("Opinion SDK client not initialized")
            return OrderResult(
                order_id="",
                platform=Platform.OPINION,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

        try:
            # Convert side
            sdk_side = OrderSide.BUY if side == Side.BUY else OrderSide.SELL

            # Calculate quote amount
            quote_amount = size * price

            # Create order input
            order_input = PlaceOrderDataInput(
                marketId=market_id or "",
                tokenId=token_id,
                side=sdk_side,
                orderType="LIMIT",
                price=price,
                makerAmountInQuoteToken=quote_amount,
            )

            # Place order (this is synchronous in the SDK)
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._sdk_client.place_order(order_input),
            )

            order_id = result.get("orderId", "") if result else ""

            self.logger.info(
                f"Opinion Order {order_id}: {side.value} {size} @ {price}"
            )

            return OrderResult(
                order_id=order_id,
                platform=Platform.OPINION,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,  # Will check later
                status=OrderStatus.PENDING,
            )

        except Exception as e:
            self.logger.error(f"Failed to place order: {e}")
            return OrderResult(
                order_id="",
                platform=Platform.OPINION,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if not self._sdk_client:
            return False

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._sdk_client.cancel_order(order_id),
            )
            self.logger.info(f"Cancelled order: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Get order status."""
        if not self._sdk_client:
            return None

        try:
            order = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._sdk_client.get_order(order_id),
            )

            if not order:
                return None

            status_str = order.get("status", "").upper()
            if status_str == "FILLED":
                status = OrderStatus.FILLED
            elif status_str == "PARTIALLY_FILLED":
                status = OrderStatus.PARTIALLY_FILLED
            elif status_str in ("PENDING", "OPEN"):
                status = OrderStatus.PENDING
            elif status_str == "CANCELLED":
                status = OrderStatus.CANCELLED
            else:
                status = OrderStatus.FAILED

            return OrderResult(
                order_id=order_id,
                platform=Platform.OPINION,
                token_id=order.get("tokenId", ""),
                side=Side.BUY if order.get("side") == "BUY" else Side.SELL,
                price=float(order.get("price", 0)),
                requested_size=float(order.get("originalSize", 0)),
                filled_size=float(order.get("filledSize", 0)),
                status=status,
            )
        except Exception as e:
            self.logger.error(f"Failed to get order status: {e}")
            return None

    async def get_positions(self) -> list:
        """Get current positions."""
        if not self._sdk_client:
            return []

        try:
            positions = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._sdk_client.get_my_positions(),
            )
            return positions or []
        except Exception as e:
            self.logger.error(f"Failed to get positions: {e}")
            return []
