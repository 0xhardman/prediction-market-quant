"""Predict.fun client with REST API support."""

import time
from typing import Dict, Optional

import httpx
from aiolimiter import AsyncLimiter

from ..config import Config
from ..models import Orderbook, OrderResult, OrderStatus, Platform, Side
from ..utils.logger import get_logger
from .base import BaseClient


class PredictFunClient(BaseClient):
    """Predict.fun client with REST API for orderbook and trading."""

    BASE_URL = "https://api.predict.fun/v1"

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()

        # Rate limiter: 240 req/min = 4 req/s
        self._limiter = AsyncLimiter(4, 1)

        # Orderbook cache
        self._orderbooks: Dict[str, Orderbook] = {}

        # HTTP client
        self._http_client: Optional[httpx.AsyncClient] = None

        # API key
        self._api_key = config.credentials.predict_fun.api_key

    async def connect(self) -> None:
        """Initialize HTTP client."""
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key

        self._http_client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30,
        )
        self.logger.info("Predict.fun client connected")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self.logger.info("Predict.fun client disconnected")

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Optional[dict]:
        """Make a rate-limited HTTP request."""
        if not self._http_client:
            await self.connect()

        async with self._limiter:
            try:
                resp = await self._http_client.request(method, endpoint, **kwargs)

                if resp.status_code == 200:
                    data = resp.json()
                    # Predict.fun wraps data in { success, cursor, data }
                    if isinstance(data, dict) and "success" in data:
                        if data.get("success"):
                            return data
                        else:
                            self.logger.error(f"API error: {data}")
                            return None
                    return data
                else:
                    self.logger.error(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    return None

            except httpx.ReadTimeout:
                self.logger.error("Request timeout")
                return None
            except Exception as e:
                self.logger.error(f"Request error: {e}")
                return None

    async def get_markets(self, limit: int = 100, cursor: str = "") -> list[dict]:
        """Get list of markets."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        result = await self._request("GET", "/markets", params=params)
        if result and "data" in result:
            return result["data"]
        return []

    async def get_market(self, market_id: int) -> Optional[dict]:
        """Get market details by ID."""
        result = await self._request("GET", f"/markets/{market_id}")
        if result and "data" in result:
            return result["data"]
        return None

    async def fetch_orderbook(self, token_id: str, market_id: int = 0) -> Optional[Orderbook]:
        """Fetch orderbook from REST API.

        Note: Predict.fun uses market_id for orderbook, not token_id.
        """
        if market_id <= 0:
            self.logger.warning("market_id required for Predict.fun orderbook")
            return None

        result = await self._request("GET", f"/markets/{market_id}/orderbook")

        if not result:
            return None

        try:
            data = result.get("data", result)

            # Parse bids and asks - format: [[price, size], ...]
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            best_bid = 0.0
            bid_size = 0.0
            best_ask = 1.0
            ask_size = 0.0

            if bids:
                # Bids: highest price first (or we take first entry)
                # Sort by price descending to get best bid
                sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)
                best_bid = float(sorted_bids[0][0])
                bid_size = float(sorted_bids[0][1])

            if asks:
                # Asks: lowest price first
                # Sort by price ascending to get best ask
                sorted_asks = sorted(asks, key=lambda x: x[0])
                best_ask = float(sorted_asks[0][0])
                ask_size = float(sorted_asks[0][1])

            orderbook = Orderbook(
                platform=Platform.PREDICT_FUN,
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size=bid_size,
                ask_size=ask_size,
                timestamp=time.time(),
            )

            # Cache orderbook by token_id
            self._orderbooks[token_id] = orderbook
            return orderbook

        except Exception as e:
            self.logger.error(f"Failed to parse orderbook: {e}")
            return None

    async def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get cached orderbook or fetch if not available."""
        if token_id in self._orderbooks:
            return self._orderbooks[token_id]
        return await self.fetch_orderbook(token_id)

    async def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a limit order."""
        order_data = {
            "tokenId": token_id,
            "side": side.value.upper(),
            "price": str(price),
            "size": str(size),
            "type": "LIMIT",
        }

        result = await self._request("POST", "/order", json=order_data)

        if not result or not result.get("success"):
            return OrderResult(
                order_id="",
                platform=Platform.PREDICT_FUN,
                token_id=token_id,
                side=side,
                price=price,
                requested_size=size,
                filled_size=0.0,
                status=OrderStatus.FAILED,
            )

        data = result.get("data", {})
        order_id = str(data.get("orderId", data.get("id", "")))

        return OrderResult(
            order_id=order_id,
            platform=Platform.PREDICT_FUN,
            token_id=token_id,
            side=side,
            price=price,
            requested_size=size,
            filled_size=0.0,
            status=OrderStatus.PENDING,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        result = await self._request("DELETE", f"/order/{order_id}")
        return result is not None and result.get("success", False)

    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Get order status."""
        result = await self._request("GET", f"/order/{order_id}")

        if not result or not result.get("success"):
            return None

        data = result.get("data", {})

        # Parse status
        status_str = data.get("status", "").upper()
        status_map = {
            "PENDING": OrderStatus.PENDING,
            "OPEN": OrderStatus.PENDING,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELLED": OrderStatus.CANCELLED,
            "CANCELED": OrderStatus.CANCELLED,
            "FAILED": OrderStatus.FAILED,
        }
        status = status_map.get(status_str, OrderStatus.PENDING)

        # Parse side
        side_str = data.get("side", "BUY").upper()
        side = Side.BUY if side_str == "BUY" else Side.SELL

        return OrderResult(
            order_id=order_id,
            platform=Platform.PREDICT_FUN,
            token_id=data.get("tokenId", ""),
            side=side,
            price=float(data.get("price", 0)),
            requested_size=float(data.get("size", 0)),
            filled_size=float(data.get("filledSize", 0)),
            status=status,
        )
