"""Tests for arbitrage engine."""

import pytest
from src.config import ArbitrageConfig, FeesConfig, PlatformFees
from src.models import Direction, Orderbook, Platform
from src.engine.arbitrage import ArbitrageEngine


class MockConfig:
    """Mock configuration for testing."""

    def __init__(self):
        self.arbitrage = ArbitrageConfig(
            min_profit_threshold=0.02,
            max_position_size=100,
            min_position_size=10,
            price_freshness_ms=5000,  # 5s for tests
        )
        self.fees = FeesConfig(
            polymarket=PlatformFees(taker_fee=0.0, gas_estimate=0.05),
            opinion=PlatformFees(taker_fee=0.01, gas_estimate=0.10),
        )


class MockMarket:
    """Mock market pair for testing."""

    def __init__(self, name="Test Market"):
        self.name = name


def create_orderbook(
    platform: Platform,
    token_id: str,
    best_bid: float,
    best_ask: float,
    size: float = 100.0,
) -> Orderbook:
    """Create a test orderbook."""
    import time
    return Orderbook(
        platform=platform,
        token_id=token_id,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=size,
        ask_size=size,
        timestamp=time.time(),
    )


class TestArbitrageEngine:
    """Test cases for ArbitrageEngine."""

    def test_no_opportunity_when_sum_above_one(self):
        """No arbitrage when PM_Yes + OP_No >= 1.0."""
        config = MockConfig()
        engine = ArbitrageEngine(config)
        market = MockMarket()

        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.50, 0.51)
        pm_no = create_orderbook(Platform.POLYMARKET, "pm_no", 0.48, 0.49)
        op_yes = create_orderbook(Platform.OPINION, "op_yes", 0.49, 0.50)
        op_no = create_orderbook(Platform.OPINION, "op_no", 0.50, 0.51)

        # 0.51 + 0.51 = 1.02 > 1.0 (no opportunity)
        result = engine.check_arbitrage(market, pm_yes, pm_no, op_yes, op_no)
        assert result is None

    def test_opportunity_when_sum_below_one(self):
        """Arbitrage exists when PM_Yes + OP_No < 1.0 (minus fees)."""
        config = MockConfig()
        engine = ArbitrageEngine(config)
        market = MockMarket()

        # Create profitable scenario
        # PM Yes @ 0.40, OP No @ 0.45 = 0.85 total
        # With fees (~1%) and gas (~0.15) = ~0.87 < 1.0
        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.39, 0.40)
        pm_no = create_orderbook(Platform.POLYMARKET, "pm_no", 0.58, 0.60)
        op_yes = create_orderbook(Platform.OPINION, "op_yes", 0.54, 0.55)
        op_no = create_orderbook(Platform.OPINION, "op_no", 0.44, 0.45)

        result = engine.check_arbitrage(market, pm_yes, pm_no, op_yes, op_no)

        assert result is not None
        assert result.direction == Direction.PM_YES_OP_NO
        assert result.pm_price == 0.40
        assert result.op_price == 0.45
        assert result.profit_pct > 0.02  # Above threshold

    def test_selects_more_profitable_direction(self):
        """Engine selects the more profitable arbitrage direction."""
        config = MockConfig()
        engine = ArbitrageEngine(config)
        market = MockMarket()

        # Direction 1: PM Yes + OP No = 0.40 + 0.45 = 0.85
        # Direction 2: PM No + OP Yes = 0.35 + 0.40 = 0.75 (more profitable)
        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.39, 0.40)
        pm_no = create_orderbook(Platform.POLYMARKET, "pm_no", 0.34, 0.35)
        op_yes = create_orderbook(Platform.OPINION, "op_yes", 0.39, 0.40)
        op_no = create_orderbook(Platform.OPINION, "op_no", 0.44, 0.45)

        result = engine.check_arbitrage(market, pm_yes, pm_no, op_yes, op_no)

        assert result is not None
        assert result.direction == Direction.PM_NO_OP_YES
        assert result.pm_price == 0.35
        assert result.op_price == 0.40

    def test_respects_min_profit_threshold(self):
        """No opportunity if profit below threshold."""
        config = MockConfig()
        config.arbitrage.min_profit_threshold = 0.10  # 10% threshold
        engine = ArbitrageEngine(config)
        market = MockMarket()

        # Small profit scenario (~3%)
        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.44, 0.45)
        pm_no = create_orderbook(Platform.POLYMARKET, "pm_no", 0.54, 0.55)
        op_yes = create_orderbook(Platform.OPINION, "op_yes", 0.54, 0.55)
        op_no = create_orderbook(Platform.OPINION, "op_no", 0.34, 0.35)

        result = engine.check_arbitrage(market, pm_yes, pm_no, op_yes, op_no)
        assert result is None  # Below 10% threshold

    def test_respects_min_size(self):
        """No opportunity if available size below minimum."""
        config = MockConfig()
        config.arbitrage.min_position_size = 50
        engine = ArbitrageEngine(config)
        market = MockMarket()

        # Small liquidity
        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.39, 0.40, size=20)
        pm_no = create_orderbook(Platform.POLYMARKET, "pm_no", 0.58, 0.60)
        op_yes = create_orderbook(Platform.OPINION, "op_yes", 0.54, 0.55)
        op_no = create_orderbook(Platform.OPINION, "op_no", 0.44, 0.45, size=100)

        result = engine.check_arbitrage(market, pm_yes, pm_no, op_yes, op_no)
        assert result is None  # Only 20 available, need 50

    def test_handles_missing_orderbook(self):
        """Returns None if any orderbook is missing."""
        config = MockConfig()
        engine = ArbitrageEngine(config)
        market = MockMarket()

        pm_yes = create_orderbook(Platform.POLYMARKET, "pm_yes", 0.40, 0.41)

        result = engine.check_arbitrage(market, pm_yes, None, None, None)
        assert result is None


class TestOrderbook:
    """Test cases for Orderbook model."""

    def test_is_fresh(self):
        """Test orderbook freshness check."""
        import time

        ob = Orderbook(
            platform=Platform.POLYMARKET,
            token_id="test",
            best_bid=0.50,
            best_ask=0.51,
            bid_size=100,
            ask_size=100,
            timestamp=time.time(),
        )

        assert ob.is_fresh(1000)  # Fresh within 1 second

    def test_spread_calculation(self):
        """Test spread calculation."""
        ob = Orderbook(
            platform=Platform.POLYMARKET,
            token_id="test",
            best_bid=0.50,
            best_ask=0.52,
            bid_size=100,
            ask_size=100,
        )

        assert ob.spread == pytest.approx(0.02)
        assert ob.mid_price == pytest.approx(0.51)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
