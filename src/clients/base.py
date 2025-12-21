"""Base client abstract class for prediction market platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Order, Orderbook, Side, Trade


class BaseClient(ABC):
    """Abstract base class for prediction market clients.

    Each client instance is bound to a specific market token.
    Supports async context manager for automatic resource cleanup.

    Usage:
        async with SomeClient(token_id, config) as client:
            ob = await client.get_orderbook()
            order = await client.place_order(Side.BUY, 0.5, 10)
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect and authenticate with the platform.

        Should be called before any other operations.
        Called automatically when using context manager.

        Raises:
            ConnectionError: If connection or authentication fails.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close connections and release resources.

        Called automatically when using context manager.
        """
        pass

    @abstractmethod
    async def get_orderbook(self) -> Orderbook:
        """Get the current orderbook for the bound market.

        Returns:
            Orderbook snapshot with bids and asks.

        Raises:
            NotConnectedError: If not connected.
            ClientError: If request fails.
        """
        pass

    @abstractmethod
    async def place_order(self, side: Side, price: float, size: float) -> Order:
        """Place an order in the bound market.

        Args:
            side: BUY or SELL
            price: Price per share (0-1)
            size: Order size in shares

        Returns:
            Order object with order details.

        Raises:
            NotConnectedError: If not connected.
            InsufficientBalanceError: If balance too low.
            OrderRejectedError: If order rejected by exchange.
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: Order ID to cancel.

        Returns:
            True if cancelled successfully.

        Raises:
            NotConnectedError: If not connected.
            OrderNotFoundError: If order does not exist.
        """
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        """Get account balance (USDC/USDT).

        Returns:
            Balance in USD.

        Raises:
            NotConnectedError: If not connected.
        """
        pass

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Get list of open orders.

        Returns:
            List of open orders.

        Raises:
            NotConnectedError: If not connected.
        """
        pass

    # Optional methods with default implementations
    # Subclasses can override these if the platform supports them

    async def place_market_order(
        self,
        side: Side,
        size: float | None = None,
        value: float | None = None,
    ) -> Order:
        """Place a market order.

        For BUY orders, specify value (USD amount to spend) or size (shares to buy).
        For SELL orders, specify size (shares to sell).

        Args:
            side: BUY or SELL
            size: Number of shares
            value: USD value to spend (BUY only)

        Returns:
            Order object with execution details.

        Raises:
            NotImplementedError: If platform doesn't support market orders.
        """
        raise NotImplementedError("Market orders not supported")

    async def place_orders(
        self,
        orders: list[tuple[Side, float, float]],
    ) -> list[Order]:
        """Place multiple orders in batch.

        Args:
            orders: List of (side, price, size) tuples.

        Returns:
            List of Order objects.

        Raises:
            NotImplementedError: If platform doesn't support batch orders.
        """
        raise NotImplementedError("Batch orders not supported")

    async def cancel_orders(self, order_ids: list[str]) -> list[bool]:
        """Cancel multiple orders in batch.

        Args:
            order_ids: List of order IDs to cancel.

        Returns:
            List of success booleans for each order.

        Raises:
            NotImplementedError: If platform doesn't support batch cancel.
        """
        raise NotImplementedError("Batch cancel not supported")

    async def cancel_all(self) -> int:
        """Cancel all open orders.

        Returns:
            Number of orders cancelled.

        Raises:
            NotImplementedError: If platform doesn't support cancel all.
        """
        raise NotImplementedError("Cancel all not supported")

    async def get_order(self, order_id: str) -> Order | None:
        """Get a specific order by ID.

        Args:
            order_id: The order ID.

        Returns:
            Order object or None if not found.

        Raises:
            NotImplementedError: If platform doesn't support get order.
        """
        raise NotImplementedError("Get order not supported")

    async def get_trades(self) -> list[Trade]:
        """Get trade history.

        Returns:
            List of Trade objects.

        Raises:
            NotImplementedError: If platform doesn't support trade history.
        """
        raise NotImplementedError("Trade history not supported")

    async def get_midpoint(self) -> float | None:
        """Get midpoint price for the bound market.

        Returns:
            Midpoint price or None if not available.

        Raises:
            NotImplementedError: If platform doesn't support midpoint.
        """
        raise NotImplementedError("Midpoint not supported")

    async def get_spread(self) -> float | None:
        """Get bid-ask spread for the bound market.

        Returns:
            Spread or None if not available.

        Raises:
            NotImplementedError: If platform doesn't support spread.
        """
        raise NotImplementedError("Spread not supported")

    async def __aenter__(self) -> "BaseClient":
        """Enter async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager."""
        await self.close()
