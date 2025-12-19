"""Base client interface."""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Orderbook, OrderResult, Side


class BaseClient(ABC):
    """Abstract base class for platform clients."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the platform."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the platform."""
        pass

    @abstractmethod
    async def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get orderbook for a token."""
        pass

    @abstractmethod
    async def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place an order."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Get order status."""
        pass
