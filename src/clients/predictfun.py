"""Predict.fun client implementation."""

from time import time

import httpx
from predict_sdk import (
    OrderBuilder,
    ChainId,
    OrderBuilderOptions,
    BuildOrderInput,
    LimitHelperInput,
    Side as SdkSide,
)

from .base import BaseClient
from ..config import PredictFunConfig
from ..exceptions import (
    ConnectionError,
    NotConnectedError,
    OrderNotFoundError,
    OrderRejectedError,
    InsufficientBalanceError,
)
from ..logging import pf_logger as logger
from ..models import Order, Orderbook, OrderStatus, Side


class PredictFunClient(BaseClient):
    """Predict.fun trading client.

    Uses predict_sdk for order signing and httpx for REST API.
    Supports Predict Account mode (Smart Wallet).
    Automatically refreshes JWT token when expired.
    """

    BASE_URL = "https://api.predict.fun/v1"
    CHAIN_ID = ChainId.BNB_MAINNET
    JWT_REFRESH_THRESHOLD = 300  # Refresh if < 5 minutes remaining

    def __init__(
        self,
        market_id: int,
        token_id: str,
        config: PredictFunConfig | None = None,
    ):
        """Initialize Predict.fun client.

        Args:
            market_id: Market ID for orderbook queries.
            token_id: Token ID for order placement.
            config: Configuration object. If None, loads from environment.
        """
        self.market_id = market_id
        self.token_id = token_id
        self._config = config or PredictFunConfig.from_env()
        self._http: httpx.AsyncClient | None = None
        self._builder: OrderBuilder | None = None
        self._jwt_expires_at: float = 0  # Unix timestamp when JWT expires

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._http is not None and self._builder is not None

    async def _authenticate(self) -> None:
        """Authenticate and get JWT token."""
        logger.debug("Authenticating with Predict.fun...")

        # Get auth message
        resp = await self._http.get("/auth/message")
        resp.raise_for_status()
        message = resp.json()["data"]["message"]

        # Sign message using Predict Account mode
        signature = self._builder.sign_predict_account_message(message)

        # Authenticate
        auth_resp = await self._http.post(
            "/auth",
            json={
                "message": message,
                "signature": signature,
                "signer": self._config.smart_wallet,
            },
        )
        auth_resp.raise_for_status()

        result = auth_resp.json()
        if not result.get("success"):
            raise ConnectionError(f"Authentication failed: {result}")

        jwt = result["data"]["token"]
        self._http.headers["Authorization"] = f"Bearer {jwt}"

        # JWT typically expires in 24 hours, set refresh time conservatively
        self._jwt_expires_at = time() + 86400 - self.JWT_REFRESH_THRESHOLD

        logger.info("JWT token obtained successfully")

    async def _ensure_valid_token(self) -> None:
        """Refresh JWT if expired or about to expire."""
        if time() >= self._jwt_expires_at:
            logger.info("JWT token expired or expiring soon, refreshing...")
            await self._authenticate()

    async def connect(self) -> None:
        """Connect and authenticate with Predict.fun via JWT."""
        logger.info(f"Connecting to Predict.fun (market={self.market_id})")

        try:
            self._config.validate()
        except ValueError as e:
            logger.error(f"Configuration validation failed: {e}")
            raise ConnectionError(str(e)) from e

        try:
            # Initialize HTTP client
            self._http = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={"X-API-Key": self._config.api_key},
                timeout=30,
            )

            # Create builder for signing (Predict Account mode)
            self._builder = OrderBuilder.make(
                self.CHAIN_ID,
                self._config.private_key,
                OrderBuilderOptions(predict_account=self._config.smart_wallet),
            )

            # Authenticate
            await self._authenticate()

            logger.info("Connected to Predict.fun successfully")

        except ConnectionError:
            await self.close()
            raise
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            await self.close()
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self._builder = None
        self._jwt_expires_at = 0
        logger.info("Disconnected from Predict.fun")

    def _ensure_connected(self) -> None:
        """Raise if not connected."""
        if not self.is_connected:
            raise NotConnectedError("Client not connected. Call connect() first.")

    async def get_orderbook(self) -> Orderbook:
        """Get orderbook for the bound market."""
        self._ensure_connected()
        await self._ensure_valid_token()

        resp = await self._http.get(f"/markets/{self.market_id}/orderbook")
        resp.raise_for_status()

        book = resp.json().get("data", {})
        # PF format: [[price, size], ...]
        bids = [(float(b[0]), float(b[1])) for b in book.get("bids", [])]
        asks = [(float(a[0]), float(a[1])) for a in book.get("asks", [])]

        return Orderbook(bids=bids, asks=asks, timestamp=time())

    async def place_order(self, side: Side, price: float, size: float) -> Order:
        """Place an order in the bound market.

        Args:
            side: BUY or SELL
            price: Price per share (0-1)
            size: Order size in shares
        """
        self._ensure_connected()
        await self._ensure_valid_token()

        logger.info(f"Placing order: {side.value} {size} @ {price}")

        sdk_side = SdkSide.BUY if side == Side.BUY else SdkSide.SELL
        price_wei = int(price * 1e18)
        size_wei = int(size * 1e18)

        # Calculate order amounts
        amounts = self._builder.get_limit_order_amounts(
            LimitHelperInput(
                side=sdk_side,
                price_per_share_wei=price_wei,
                quantity_wei=size_wei,
            )
        )

        # Build order
        order = self._builder.build_order(
            "LIMIT",
            BuildOrderInput(
                token_id=self.token_id,
                side=sdk_side,
                maker_amount=amounts.maker_amount,
                taker_amount=amounts.taker_amount,
                fee_rate_bps=200,  # 2% fee
            ),
        )

        # Sign order
        typed_data = self._builder.build_typed_data(
            order, is_neg_risk=False, is_yield_bearing=False
        )
        order_hash = self._builder.build_typed_data_hash(typed_data)
        signed = self._builder.sign_typed_data_order(typed_data)

        # Build payload
        signature = signed.signature
        if not signature.startswith("0x"):
            signature = "0x" + signature

        order_payload = {
            "hash": order_hash,
            "salt": str(order.salt),
            "maker": order.maker,
            "signer": order.signer,
            "taker": order.taker,
            "tokenId": str(order.token_id),
            "makerAmount": str(order.maker_amount),
            "takerAmount": str(order.taker_amount),
            "expiration": str(order.expiration),
            "nonce": str(order.nonce),
            "feeRateBps": str(order.fee_rate_bps),
            "side": order.side,
            "signatureType": order.signature_type,
            "signature": signature,
        }

        # Submit order
        resp = await self._http.post(
            "/orders",
            json={
                "data": {
                    "pricePerShare": str(price_wei),
                    "strategy": "LIMIT",
                    "slippageBps": "0",
                    "order": order_payload,
                }
            },
        )

        result = resp.json()
        if resp.status_code in (200, 201) and result.get("success"):
            returned_hash = result.get("data", {}).get("orderHash", order_hash)
            logger.info(f"Order placed: hash={returned_hash[:30]}...")
            return Order(
                id=returned_hash,
                token_id=self.token_id,
                side=side,
                price=price,
                size=size,
                status=OrderStatus.OPEN,
            )
        else:
            error_msg = result.get("message", "") or result.get("error", {}).get(
                "description", ""
            )
            logger.error(f"Order placement failed: {error_msg}")
            error_lower = error_msg.lower()
            if "insufficient" in error_lower or "collateral" in error_lower:
                raise InsufficientBalanceError()
            raise OrderRejectedError(error_msg)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by hash."""
        self._ensure_connected()
        await self._ensure_valid_token()

        logger.info(f"Cancelling order: {order_id[:30]}...")

        # First get order ID from hash
        orders_resp = await self._http.get("/orders")
        orders_resp.raise_for_status()

        orders = orders_resp.json().get("data", [])
        internal_id = None
        for o in orders:
            if o.get("order", {}).get("hash") == order_id:
                internal_id = o.get("id")
                break

        if not internal_id:
            logger.warning(f"Order not found: {order_id[:30]}...")
            raise OrderNotFoundError(order_id)

        # Cancel by internal ID
        cancel_resp = await self._http.post(
            "/orders/remove", json={"data": {"ids": [internal_id]}}
        )

        result = cancel_resp.json()
        if result.get("success"):
            removed = result.get("removed", [])
            noop = result.get("noop", [])
            if removed or noop:
                logger.info(f"Order cancelled: {order_id[:30]}...")
                return True
        logger.warning(f"Cancel returned false: {result}")
        return False

    async def get_balance(self) -> float:
        """Get Smart Wallet USDT balance."""
        self._ensure_connected()

        balance_wei = await self._builder.balance_of_async(
            "USDT", self._config.smart_wallet
        )
        balance = balance_wei / 1e18
        logger.debug(f"Balance: {balance:.4f} USDT")
        return balance

    async def get_orders(self) -> list[Order]:
        """Get list of open orders."""
        self._ensure_connected()
        await self._ensure_valid_token()

        resp = await self._http.get("/orders")
        resp.raise_for_status()

        orders = []
        for o in resp.json().get("data", []):
            order_data = o.get("order", {})
            orders.append(
                Order(
                    id=order_data.get("hash", ""),
                    token_id=order_data.get("tokenId", ""),
                    side=Side.BUY if order_data.get("side") == "BUY" else Side.SELL,
                    price=float(order_data.get("price", 0)),
                    size=float(order_data.get("size", 0)),
                    status=OrderStatus.OPEN,
                )
            )

        logger.debug(f"Retrieved {len(orders)} orders")
        return orders
