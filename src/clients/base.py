"""Base client abstract class for prediction market platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Order, Orderbook, Side


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

    async def __aenter__(self) -> "BaseClient":
        """Enter async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager."""
        await self.close()
