"""Predict.fun client with REST API support."""

import time
from typing import Dict, Optional

import httpx
from aiolimiter import AsyncLimiter
from predict_sdk import (
    OrderBuilder, ChainId, OrderBuilderOptions,
    BuildOrderInput, LimitHelperInput, Side as SDKSide
)

from ..config import Config
from ..models import Orderbook, OrderResult, OrderStatus, Platform, Side
from ..utils.logger import get_logger
from .base import BaseClient


class PredictFunClient(BaseClient):
    """Predict.fun client with REST API for orderbook and trading."""

    BASE_URL = "https://api.predict.fun/v1"

    # Contract addresses (BNB Mainnet)
    CTF_EXCHANGE = "0x8BC070BEdAB741406F4B1Eb65A72bee27894B689"
    NEG_RISK_CTF_EXCHANGE = "0x365fb81bd4A24D6303cd2F19c349dE6894D8d58A"

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()

        # Rate limiter: 240 req/min = 4 req/s
        self._limiter = AsyncLimiter(4, 1)

        # Orderbook cache
        self._orderbooks: Dict[str, Orderbook] = {}

        # HTTP client
        self._http_client: Optional[httpx.AsyncClient] = None

        # Credentials
        self._api_key = config.credentials.predict_fun.api_key
        self._private_key = config.credentials.predict_fun.private_key
        self._smart_wallet = config.credentials.predict_fun.smart_wallet
        self._jwt_token: Optional[str] = None

        # SDK builder (cached after authentication)
        self._builder: Optional[OrderBuilder] = None

    async def connect(self) -> None:
        """Initialize HTTP client and authenticate."""
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key

        self._http_client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30,
        )

        # Authenticate if private key is available
        if self._private_key:
            await self._authenticate()

        self.logger.info("Predict.fun client connected")

    async def _authenticate(self) -> bool:
        """Authenticate and get JWT token using Predict Account mode."""
        if not self._private_key or not self._smart_wallet or not self._http_client:
            self.logger.error("Missing private_key or smart_wallet for authentication")
            return False

        try:
            # 1. Get auth message
            resp = await self._http_client.get("/auth/message")
            if resp.status_code != 200:
                self.logger.error(f"Failed to get auth message: {resp.status_code}")
                return False

            data = resp.json()
            if not data.get("success"):
                self.logger.error(f"Auth message error: {data}")
                return False

            message = data["data"]["message"]

            # 2. Sign message using SDK (Predict Account mode)
            builder = OrderBuilder.make(
                ChainId.BNB_MAINNET,
                self._private_key,
                OrderBuilderOptions(predict_account=self._smart_wallet),
            )
            signature = builder.sign_predict_account_message(message)

            # 3. Get JWT - signer is Smart Wallet address in Predict Account mode
            auth_resp = await self._http_client.post(
                "/auth",
                json={
                    "message": message,
                    "signature": signature,
                    "signer": self._smart_wallet,
                },
            )

            if auth_resp.status_code != 200:
                self.logger.error(f"Auth failed: {auth_resp.status_code}")
                return False

            auth_data = auth_resp.json()
            if not auth_data.get("success"):
                self.logger.error(f"Auth error: {auth_data}")
                return False

            self._jwt_token = auth_data["data"]["token"]
            self._http_client.headers["Authorization"] = f"Bearer {self._jwt_token}"
            self._builder = builder  # Cache the builder for order signing
            self.logger.info(f"Authenticated as Smart Wallet {self._smart_wallet}")
            return True

        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            return False

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

    def _create_signed_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> tuple:
        """Create and sign order using predict_sdk.

        Returns: (order_payload, order_hash, price_wei)
        """
        if not self._builder:
            self._builder = OrderBuilder.make(
                ChainId.BNB_MAINNET,
                self._private_key,
                OrderBuilderOptions(predict_account=self._smart_wallet),
            )

        price_wei = int(price * 1e18)
        size_wei = int(size * 1e18)
        sdk_side = SDKSide.BUY if side == Side.BUY else SDKSide.SELL

        amounts = self._builder.get_limit_order_amounts(LimitHelperInput(
            side=sdk_side,
            price_per_share_wei=price_wei,
            quantity_wei=size_wei,
        ))

        order = self._builder.build_order('LIMIT', BuildOrderInput(
            token_id=token_id,
            side=sdk_side,
            maker_amount=amounts.maker_amount,
            taker_amount=amounts.taker_amount,
            fee_rate_bps=200,
        ))

        typed_data = self._builder.build_typed_data(
            order, is_neg_risk=False, is_yield_bearing=False
        )
        order_hash = self._builder.build_typed_data_hash(typed_data)
        signed = self._builder.sign_typed_data_order(typed_data)

        order_payload = {
            'hash': order_hash,
            'salt': str(order.salt),
            'maker': order.maker,
            'signer': order.signer,
            'taker': order.taker,
            'tokenId': str(order.token_id),
            'makerAmount': str(order.maker_amount),
            'takerAmount': str(order.taker_amount),
            'expiration': str(order.expiration),
            'nonce': str(order.nonce),
            'feeRateBps': str(order.fee_rate_bps),
            'side': order.side,
            'signatureType': order.signature_type,
            'signature': '0x' + signed.signature if not signed.signature.startswith('0x') else signed.signature,
        }

        return order_payload, order_hash, price_wei

    async def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a signed limit order."""
        try:
            # Create signed order using SDK
            order_payload, order_hash, price_wei = self._create_signed_order(
                token_id, side, price, size
            )

            # Submit order
            order_data = {
                "data": {
                    "pricePerShare": str(price_wei),
                    "strategy": "LIMIT",
                    "slippageBps": "0",
                    "order": order_payload,
                }
            }

            result = await self._request("POST", "/orders", json=order_data)

            if not result or not result.get("success"):
                self.logger.error(f"Order failed: {result}")
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
            # Prefer numeric id, fallback to orderHash
            order_id = str(data.get("id", data.get("orderId", order_hash)))

            self.logger.info(f"PF Order submitted: {order_id}")

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

        except Exception as e:
            self.logger.error(f"Failed to place order: {e}")
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

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID.

        Note: order_id should be the numeric ID from place_order response.
        If it's a hash, we need to query orders first to find the ID.
        """
        # If order_id is a hash, query orders to find numeric ID
        if order_id.startswith("0x"):
            orders_result = await self._request("GET", "/orders")
            if not orders_result:
                self.logger.error("Failed to query orders")
                return False

            found_id = None
            for o in orders_result.get("data", []):
                if o.get("order", {}).get("hash") == order_id:
                    found_id = str(o.get("id"))
                    break

            if not found_id:
                self.logger.error(f"Order not found by hash: {order_id}")
                return False

            order_id = found_id

        # Use correct API: POST /orders/remove
        result = await self._request("POST", "/orders/remove", json={
            "data": {"ids": [order_id]}
        })

        if not result:
            return False

        removed = result.get("removed", [])
        noop = result.get("noop", [])

        if removed:
            self.logger.info(f"Order cancelled: {removed}")
            return True
        elif noop:
            self.logger.info(f"Order already cancelled/filled: {noop}")
            return True

        return False

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
