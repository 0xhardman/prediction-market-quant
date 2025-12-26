"""Polymarket client implementation."""

import asyncio
from time import time

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    PostOrdersArgs,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .base import BaseClient
from ..config import PolymarketConfig
from ..exceptions import (
    ConnectionError,
    InsufficientBalanceError,
    NotConnectedError,
    OrderNotFoundError,
    OrderRejectedError,
)
from ..logging import pm_logger as logger
from ..models import Order, Orderbook, OrderStatus, Side, Trade


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

            # Initialize HTTP client for orderbook with strict timeout
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0)  # 5s total, 3s connect
            )

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

        # Sort: bids descending (highest first), asks ascending (lowest first)
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        return Orderbook(bids=bids, asks=asks, timestamp=time())

    async def place_order(
        self,
        side: Side,
        price: float,
        size: float,
        order_type: OrderType = OrderType.GTC,
    ) -> Order:
        """Place an order in the bound market.

        Args:
            side: BUY or SELL
            price: Price per share (0-1)
            size: Order size in shares (minimum 5 for Polymarket)
            order_type: Order type - GTC (default), FOK, FAK, GTD
        """
        self._ensure_connected()

        logger.info(f"Placing order: {side.value} {size} @ {price} ({order_type})")

        order_side = BUY if side == Side.BUY else SELL
        order_args = OrderArgs(
            price=price,
            size=size,
            side=order_side,
            token_id=self.token_id,
        )

        try:
            signed_order = await asyncio.to_thread(self._client.create_order, order_args)
            resp = await asyncio.to_thread(self._client.post_order, signed_order, order_type)
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Limit order failed: {e}")
            logger.error(
                f"Order params: token_id={self.token_id[:20]}..., side={side.value}, "
                f"price={price}, size={size}, order_type={order_type}"
            )
            if "insufficient" in error_msg or "balance" in error_msg:
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
            await asyncio.to_thread(self._client.cancel, order_id)
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
            result = await asyncio.to_thread(self._client.get_balance_allowance, params)
            # Result contains 'balance' field in wei (6 decimals for USDC)
            balance_str = result.get("balance", "0")
            balance = float(balance_str) / 1e6  # USDC has 6 decimals
            logger.debug(f"Balance: {balance:.2f} USDC")
            return balance
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    async def get_position(self) -> float:
        """Get token position (shares held) for the bound market."""
        self._ensure_connected()

        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=self.token_id,
            )
            result = await asyncio.to_thread(self._client.get_balance_allowance, params)
            # Result contains 'balance' field in wei (6 decimals)
            balance_str = result.get("balance", "0")
            position = float(balance_str) / 1e6
            logger.debug(f"Position: {position:.2f} shares")
            return position
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
            raise

    async def get_orders(self) -> list[Order]:
        """Get list of open orders."""
        self._ensure_connected()

        try:
            result = await asyncio.to_thread(self._client.get_orders)
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

    async def place_market_order(
        self,
        side: Side,
        size: float | None = None,
        value: float | None = None,
    ) -> Order:
        """Place a market order.

        For BUY orders, specify value (USD amount to spend) or size (shares to buy).
        For SELL orders, specify size (shares to sell).

        Note: SDK's MarketOrderArgs.amount means:
          - BUY: USD amount to spend
          - SELL: shares to sell

        Args:
            side: BUY or SELL
            size: Number of shares (for SELL, or converted to value for BUY)
            value: USD value to spend (BUY only)
        """
        self._ensure_connected()

        # Validate parameters
        if side == Side.BUY and size is None and value is None:
            raise ValueError("BUY market order requires either size or value")
        if side == Side.SELL and size is None:
            raise ValueError("SELL market order requires size")

        # SDK amount semantics:
        #   BUY: amount = USD to spend
        #   SELL: amount = shares to sell
        if side == Side.BUY:
            if value is not None:
                # Direct USD value
                amount = value
                logger.info(f"Placing market BUY: ${value:.2f}")
            else:
                # Convert size (shares) to value (USD) using best ask
                ob = await self.get_orderbook()
                if not ob.best_ask:
                    raise OrderRejectedError("No asks available for market buy")
                amount = size * ob.best_ask
                logger.info(f"Placing market BUY: {size} shares @ ~{ob.best_ask:.4f} = ${amount:.2f}")
        else:
            # SELL: amount = shares
            amount = size
            logger.info(f"Placing market SELL: {size} shares")

        order_side = BUY if side == Side.BUY else SELL
        order_args = MarketOrderArgs(
            token_id=self.token_id,
            amount=amount,
            side=order_side,
            order_type=OrderType.FOK,
        )

        try:
            signed_order = await asyncio.to_thread(
                self._client.create_market_order, order_args
            )
            resp = await asyncio.to_thread(
                self._client.post_order, signed_order, OrderType.FOK
            )
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Market order failed: {e}")
            logger.error(
                f"Order params: token_id={self.token_id[:20]}..., side={side.value}, "
                f"amount={amount}, size={size}, value={value}"
            )
            if "insufficient" in error_msg or "balance" in error_msg:
                raise InsufficientBalanceError() from e
            raise OrderRejectedError(str(e)) from e

        order_id = resp.get("orderID", "")
        status_str = resp.get("status", "FILLED")

        logger.info(f"Market order placed: id={order_id[:20]}... status={status_str}")

        # For BUY with size, we converted to value, so record original size
        # For BUY with value, estimate size from value/price
        # For SELL, size is the amount
        if side == Side.BUY:
            if size is not None:
                order_size = size
                order_price = amount / size  # estimated avg price
            else:
                # value-based buy, estimate size
                order_size = amount  # will be updated after fill
                order_price = 0.0
        else:
            order_size = size
            order_price = 0.0

        return Order(
            id=order_id,
            token_id=self.token_id,
            side=side,
            price=order_price,
            size=order_size,
            status=OrderStatus.FILLED,
        )

    async def place_orders(
        self,
        orders: list[tuple[Side, float, float]],
    ) -> list[Order]:
        """Place multiple orders in batch.

        Args:
            orders: List of (side, price, size) tuples.
        """
        self._ensure_connected()

        logger.info(f"Placing {len(orders)} orders in batch")

        # Create and sign all orders
        signed_orders = []
        for side, price, size in orders:
            order_side = BUY if side == Side.BUY else SELL
            order_args = OrderArgs(
                price=price,
                size=size,
                side=order_side,
                token_id=self.token_id,
            )
            signed = await asyncio.to_thread(self._client.create_order, order_args)
            signed_orders.append(PostOrdersArgs(order=signed, orderType=OrderType.GTC))

        try:
            resp = await asyncio.to_thread(self._client.post_orders, signed_orders)
        except Exception as e:
            logger.error(f"Batch order placement failed: {e}")
            raise OrderRejectedError(str(e)) from e

        result = []
        for i, (side, price, size) in enumerate(orders):
            order_id = ""
            if isinstance(resp, list) and i < len(resp):
                order_id = resp[i].get("orderID", "")
            result.append(
                Order(
                    id=order_id,
                    token_id=self.token_id,
                    side=side,
                    price=price,
                    size=size,
                    status=OrderStatus.PENDING,
                )
            )

        logger.info(f"Batch placed {len(result)} orders")
        return result

    async def cancel_orders(self, order_ids: list[str]) -> list[bool]:
        """Cancel multiple orders in batch."""
        self._ensure_connected()

        logger.info(f"Cancelling {len(order_ids)} orders in batch")

        try:
            await asyncio.to_thread(self._client.cancel_orders, order_ids)
            logger.info(f"Batch cancelled {len(order_ids)} orders")
            return [True] * len(order_ids)
        except Exception as e:
            logger.warning(f"Batch cancel failed: {e}")
            return [False] * len(order_ids)

    async def cancel_all(self) -> int:
        """Cancel all open orders."""
        self._ensure_connected()

        logger.info("Cancelling all orders")

        try:
            result = await asyncio.to_thread(self._client.cancel_all)
            cancelled = result.get("canceled", []) if isinstance(result, dict) else []
            count = len(cancelled) if isinstance(cancelled, list) else 0
            logger.info(f"Cancelled {count} orders")
            return count
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            raise

    async def get_order(self, order_id: str) -> Order | None:
        """Get a specific order by ID."""
        self._ensure_connected()

        try:
            o = await asyncio.to_thread(self._client.get_order, order_id)
            if not o:
                return None

            side_str = o.get("side", "BUY")
            status_str = o.get("status", "OPEN")

            return Order(
                id=o.get("id", order_id),
                token_id=o.get("asset_id", self.token_id),
                side=Side.BUY if side_str == "BUY" else Side.SELL,
                price=float(o.get("price", 0)),
                size=float(o.get("original_size", 0)),
                status=OrderStatus(status_str)
                if status_str in OrderStatus.__members__
                else OrderStatus.OPEN,
                filled_size=float(o.get("size_matched", 0)),
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg:
                return None
            logger.error(f"Failed to get order: {e}")
            raise

    async def get_trades(self) -> list[Trade]:
        """Get trade history."""
        self._ensure_connected()

        try:
            result = await asyncio.to_thread(self._client.get_trades)
            trades = []

            for t in result:
                trades.append(
                    Trade(
                        id=t.get("id", ""),
                        order_id=t.get("order_id", ""),
                        token_id=t.get("asset_id", ""),
                        side=Side.BUY if t.get("side") == "BUY" else Side.SELL,
                        price=float(t.get("price", 0)),
                        size=float(t.get("size", 0)),
                        fee=float(t.get("fee_rate_bps", 0)) / 10000,
                        timestamp=float(t.get("created_at", 0)) / 1000
                        if t.get("created_at")
                        else time(),
                    )
                )

            logger.debug(f"Retrieved {len(trades)} trades")
            return trades
        except Exception as e:
            logger.error(f"Failed to get trades: {e}")
            raise

    async def get_midpoint(self) -> float | None:
        """Get midpoint price for the bound market."""
        self._ensure_connected()

        try:
            result = await asyncio.to_thread(self._client.get_midpoint, self.token_id)
            if result and "mid" in result:
                return float(result["mid"])
            return None
        except Exception as e:
            logger.error(f"Failed to get midpoint: {e}")
            return None

    async def get_spread(self) -> float | None:
        """Get bid-ask spread for the bound market."""
        self._ensure_connected()

        try:
            result = await asyncio.to_thread(self._client.get_spread, self.token_id)
            if result and "spread" in result:
                return float(result["spread"])
            return None
        except Exception as e:
            logger.error(f"Failed to get spread: {e}")
            return None
