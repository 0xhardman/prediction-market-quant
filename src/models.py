"""Data models for prediction market trading."""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Literal


class Side(str, Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Order status."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class Orderbook:
    """Orderbook snapshot."""
    bids: list[tuple[float, float]]  # [(price, size), ...] sorted by price desc
    asks: list[tuple[float, float]]  # [(price, size), ...] sorted by price asc
    timestamp: float = field(default_factory=time)

    @property
    def best_bid(self) -> float | None:
        """Best bid price."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        """Best ask price."""
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> float | None:
        """Bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


@dataclass
class Order:
    """Order information."""
    id: str
    token_id: str
    side: Side
    price: float
    size: float
    status: OrderStatus
    filled_size: float = 0.0
    created_at: float = field(default_factory=time)

    @property
    def remaining_size(self) -> float:
        """Remaining unfilled size."""
        return self.size - self.filled_size


@dataclass
class Trade:
    """Trade execution information."""
    id: str
    order_id: str
    token_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0
    timestamp: float = field(default_factory=time)
