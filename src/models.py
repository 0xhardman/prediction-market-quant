"""Data models for arbitrage system."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    """Supported platforms."""
    POLYMARKET = "polymarket"
    OPINION = "opinion"
    PREDICT_FUN = "predict_fun"


class Side(str, Enum):
    """Order side."""
    BUY = "buy"
    SELL = "sell"


class Direction(str, Enum):
    """Arbitrage direction."""
    # Polymarket ⟷ Opinion
    PM_YES_OP_NO = "PM_YES_OP_NO"   # Buy Yes on Polymarket, Buy No on Opinion
    PM_NO_OP_YES = "PM_NO_OP_YES"   # Buy No on Polymarket, Buy Yes on Opinion
    # Polymarket ⟷ Predict.fun
    PM_YES_PF_NO = "PM_YES_PF_NO"   # Buy Yes on Polymarket, Buy No on Predict.fun
    PM_NO_PF_YES = "PM_NO_PF_YES"   # Buy No on Polymarket, Buy Yes on Predict.fun
    # Opinion ⟷ Predict.fun
    OP_YES_PF_NO = "OP_YES_PF_NO"   # Buy Yes on Opinion, Buy No on Predict.fun
    OP_NO_PF_YES = "OP_NO_PF_YES"   # Buy No on Opinion, Buy Yes on Predict.fun


class OrderStatus(str, Enum):
    """Order status."""
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Orderbook:
    """Orderbook snapshot for a token."""
    platform: Platform
    token_id: str
    best_bid: float          # Highest buy price
    best_ask: float          # Lowest sell price
    bid_size: float          # Size available at best bid
    ask_size: float          # Size available at best ask
    timestamp: float = field(default_factory=time.time)

    def is_fresh(self, max_age_ms: int = 500) -> bool:
        """Check if orderbook data is still fresh."""
        age_ms = (time.time() - self.timestamp) * 1000
        return age_ms < max_age_ms

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.best_ask - self.best_bid

    @property
    def mid_price(self) -> float:
        """Mid price."""
        return (self.best_bid + self.best_ask) / 2


@dataclass
class ArbitrageOpportunity:
    """An identified arbitrage opportunity."""
    market_name: str
    direction: Direction
    pm_token: str            # Polymarket token to buy
    op_token: str = ""       # Opinion token to buy (optional for PM-PF arb)
    pf_token: str = ""       # Predict.fun token to buy (optional for PM-OP arb)
    pm_price: float = 0.0    # Polymarket price
    op_price: float = 0.0    # Opinion price (optional)
    pf_price: float = 0.0    # Predict.fun price (optional)
    total_cost: float = 0.0  # Total cost per unit
    profit_pct: float = 0.0  # Profit percentage
    max_size: float = 0.0    # Maximum size based on orderbook depth
    timestamp: float = field(default_factory=time.time)

    @property
    def expected_profit(self) -> float:
        """Expected profit in absolute terms."""
        return (1.0 - self.total_cost) * self.max_size


@dataclass
class OrderResult:
    """Result of an order execution."""
    order_id: str
    platform: Platform
    token_id: str
    side: Side
    price: float
    requested_size: float
    filled_size: float
    status: OrderStatus
    timestamp: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        """Check if order was fully filled."""
        return self.status == OrderStatus.FILLED

    @property
    def fill_rate(self) -> float:
        """Percentage of order filled."""
        if self.requested_size == 0:
            return 0.0
        return self.filled_size / self.requested_size


@dataclass
class ExecutionResult:
    """Result of an arbitrage execution attempt."""
    success: bool
    reason: str = ""
    pm_order: Optional[OrderResult] = None
    op_order: Optional[OrderResult] = None
    unhedged: float = 0.0    # Amount of unhedged exposure
    timestamp: float = field(default_factory=time.time)

    @property
    def is_unhedged(self) -> bool:
        """Check if there's unhedged exposure."""
        return self.unhedged > 0


@dataclass
class Position:
    """A position in a market."""
    platform: Platform
    market_id: str
    token_id: str
    is_yes: bool             # True for Yes token, False for No token
    size: float
    avg_price: float
    timestamp: float = field(default_factory=time.time)

    @property
    def value(self) -> float:
        """Current value based on avg_price."""
        return self.size * self.avg_price


@dataclass
class UnhedgedPosition:
    """Record of an unhedged position requiring attention."""
    filled_order: OrderResult
    missing_platform: Platform
    expected_size: float
    reason: str
    retry_count: int = 0
    resolved: bool = False
    timestamp: float = field(default_factory=time.time)
