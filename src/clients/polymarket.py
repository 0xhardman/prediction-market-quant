"""Polymarket client implementation."""

from time import time

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from .base import BaseClient
from ..config import PolymarketConfig
from ..exceptions import (
    ConnectionError,
    NotConnectedError,
    OrderNotFoundError,
    OrderRejectedError,
)
from ..logging import pm_logger as logger
from ..models import Order, Orderbook, OrderStatus, Side


class PolymarketClient(BaseClient):
    """Polymarket trading client.

    Uses py_clob_client for authentication and order management.
    """

    CLOB_HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137  # Polygon

    def __init__(self, token_id: str, config: PolymarketConfig | None = None):
        """Initialize Polymarket client.

        Args:
            token_id: Market token ID for this client instance.
            config: Configuration object. If None, loads from environment.
        """
        self.token_id = token_id
        self._config = config or PolymarketConfig.from_env()
        self._client: ClobClient | None = None
        self._http: httpx.AsyncClient | None = None

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._client is not None and self._http is not None

    async def connect(self) -> None:
        """Connect and authenticate with Polymarket."""
        logger.info(f"Connecting to Polymarket (token={self.token_id[:20]}...)")

        try:
            self._config.validate()
        except ValueError as e:
            logger.error(f"Configuration validation failed: {e}")
            raise ConnectionError(str(e)) from e

        try:
            # Initialize CLOB client
            self._client = ClobClient(
                host=self.CLOB_HOST,
                key=self._config.private_key,
                chain_id=self.CHAIN_ID,
                signature_type=2,  # POLY_GNOSIS_SAFE
                funder=self._config.proxy_address if self._config.proxy_address else None,
            )

            # Set API credentials
            if (
                self._config.api_key
                and self._config.api_secret
                and self._config.api_passphrase
            ):
                creds = ApiCreds(
                    api_key=self._config.api_key,
                    api_secret=self._config.api_secret,
                    api_passphrase=self._config.api_passphrase,
                )
                logger.debug("Using provided API credentials")
            else:
                creds = self._client.create_or_derive_api_creds()
                logger.debug("Derived API credentials")
            self._client.set_api_creds(creds)

            # Initialize HTTP client for orderbook
            self._http = httpx.AsyncClient(timeout=10)

            logger.info("Connected to Polymarket successfully")

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            await self.close()
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self._client = None
        logger.info("Disconnected from Polymarket")

    def _ensure_connected(self) -> None:
        """Raise if not connected."""
        if not self.is_connected:
            raise NotConnectedError("Client not connected. Call connect() first.")

    async def get_orderbook(self) -> Orderbook:
        """Get orderbook for the bound market."""
        self._ensure_connected()

        resp = await self._http.get(
            f"{self.CLOB_HOST}/book",
            params={"token_id": self.token_id},
        )
        resp.raise_for_status()
        book = resp.json()

        bids = [(float(b["price"]), float(b["size"])) for b in book.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks", [])]

        return Orderbook(bids=bids, asks=asks, timestamp=time())

    async def place_order(self, side: Side, price: float, size: float) -> Order:
        """Place an order in the bound market.

        Args:
            side: BUY or SELL
            price: Price per share (0-1)
            size: Order size in shares (minimum 5 for Polymarket)
        """
        self._ensure_connected()

        logger.info(f"Placing order: {side.value} {size} @ {price}")

        order_side = BUY if side == Side.BUY else SELL
        order_args = OrderArgs(
            price=price,
            size=size,
            side=order_side,
            token_id=self.token_id,
        )

        try:
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Order placement failed: {e}")
            if "insufficient" in error_msg or "balance" in error_msg:
                from ..exceptions import InsufficientBalanceError

                raise InsufficientBalanceError() from e
            raise OrderRejectedError(str(e)) from e

        order_id = resp.get("orderID", "")
        status_str = resp.get("status", "PENDING")

        logger.info(f"Order placed: id={order_id[:20]}... status={status_str}")

        return Order(
            id=order_id,
            token_id=self.token_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus(status_str)
            if status_str in OrderStatus.__members__
            else OrderStatus.PENDING,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        self._ensure_connected()

        logger.info(f"Cancelling order: {order_id[:20]}...")

        try:
            self._client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id[:20]}...")
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "does not exist" in error_msg:
                logger.warning(f"Order not found: {order_id[:20]}...")
                raise OrderNotFoundError(order_id) from e
            logger.warning(f"Cancel failed (may be already cancelled/filled): {e}")
            return False

    async def get_balance(self) -> float:
        """Get USDC balance."""
        self._ensure_connected()

        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self._client.get_balance_allowance(params)
            # Result contains 'balance' field in wei (6 decimals for USDC)
            balance_str = result.get("balance", "0")
            balance = float(balance_str) / 1e6  # USDC has 6 decimals
            logger.debug(f"Balance: {balance:.2f} USDC")
            return balance
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    async def get_orders(self) -> list[Order]:
        """Get list of open orders."""
        self._ensure_connected()

        try:
            result = self._client.get_orders()
            orders = []

            for o in result:
                order_id = o.get("id", "")
                token_id = o.get("asset_id", "")
                side_str = o.get("side", "BUY")
                price = float(o.get("price", 0))
                size = float(o.get("original_size", 0))
                size_matched = float(o.get("size_matched", 0))
                status_str = o.get("status", "OPEN")

                orders.append(
                    Order(
                        id=order_id,
                        token_id=token_id,
                        side=Side.BUY if side_str == "BUY" else Side.SELL,
                        price=price,
                        size=size,
                        status=OrderStatus(status_str)
                        if status_str in OrderStatus.__members__
                        else OrderStatus.OPEN,
                        filled_size=size_matched,
                    )
                )

            logger.debug(f"Retrieved {len(orders)} orders")
            return orders

        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            raise
