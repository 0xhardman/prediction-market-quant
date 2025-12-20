"""Arbitrage detection engine."""

from typing import Optional

from ..config import Config, MarketPair
from ..models import ArbitrageOpportunity, Direction, Orderbook
from ..utils.logger import get_logger


class ArbitrageEngine:
    """Engine for detecting arbitrage opportunities between platforms."""

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()

        # Extract parameters
        self.min_profit = config.arbitrage.min_profit_threshold
        self.max_size = config.arbitrage.max_position_size
        self.min_size = config.arbitrage.min_position_size
        self.freshness_ms = config.arbitrage.price_freshness_ms

        # Calculate total fees
        self.pm_fee = config.fees.polymarket.taker_fee
        self.pm_gas = config.fees.polymarket.gas_estimate
        self.op_fee = config.fees.opinion.taker_fee
        self.op_gas = config.fees.opinion.gas_estimate
        self.pf_fee = config.fees.predict_fun.taker_fee
        self.pf_gas = config.fees.predict_fun.gas_estimate

    def check_arbitrage(
        self,
        market: MarketPair,
        pm_yes: Optional[Orderbook],
        pm_no: Optional[Orderbook],
        op_yes: Optional[Orderbook],
        op_no: Optional[Orderbook],
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check for arbitrage opportunities in both directions.

        Direction 1: PM_YES + OP_NO (Buy Yes on PM, Buy No on Opinion)
        Direction 2: PM_NO + OP_YES (Buy No on PM, Buy Yes on Opinion)

        Returns the most profitable opportunity if above threshold.
        """
        opportunities = []

        # Check data freshness
        orderbooks = [pm_yes, pm_no, op_yes, op_no]
        if not all(orderbooks):
            self.logger.debug(f"Missing orderbook data for {market.name}")
            return None

        for ob in orderbooks:
            if not ob.is_fresh(self.freshness_ms):
                self.logger.debug(f"Stale orderbook for {ob.token_id}")
                return None

        # Direction 1: PM Yes + Opinion No
        opp1 = self._check_direction(
            market=market,
            direction=Direction.PM_YES_OP_NO,
            pm_ob=pm_yes,
            op_ob=op_no,
        )
        if opp1:
            opportunities.append(opp1)

        # Direction 2: PM No + Opinion Yes
        opp2 = self._check_direction(
            market=market,
            direction=Direction.PM_NO_OP_YES,
            pm_ob=pm_no,
            op_ob=op_yes,
        )
        if opp2:
            opportunities.append(opp2)

        # Return the most profitable opportunity
        if opportunities:
            best = max(opportunities, key=lambda x: x.profit_pct)
            self.logger.info(
                f"Arbitrage found: {market.name} | {best.direction.value} | "
                f"PM={best.pm_price:.4f} OP={best.op_price:.4f} | "
                f"Profit={best.profit_pct:.2%}"
            )
            return best

        return None

    def _check_direction(
        self,
        market: MarketPair,
        direction: Direction,
        pm_ob: Orderbook,
        op_ob: Orderbook,
    ) -> Optional[ArbitrageOpportunity]:
        """Check a single arbitrage direction."""
        # We're buying at ask prices
        pm_price = pm_ob.best_ask
        op_price = op_ob.best_ask

        # Calculate total cost
        # Token cost + fees + gas
        token_cost = pm_price + op_price
        fee_cost = (pm_price * self.pm_fee) + (op_price * self.op_fee)
        gas_cost = self.pm_gas + self.op_gas

        # Total cost per unit (normalized to 1.0 payout)
        total_cost = token_cost + fee_cost + gas_cost

        # Check if profitable
        if total_cost >= 1.0:
            return None

        # Calculate profit percentage
        profit_pct = (1.0 - total_cost) / total_cost

        # Check minimum profit threshold
        if profit_pct < self.min_profit:
            return None

        # Calculate max size based on orderbook depth
        max_size = min(
            pm_ob.ask_size,
            op_ob.ask_size,
            self.max_size,
        )

        # Check minimum size
        if max_size < self.min_size:
            self.logger.debug(
                f"Size too small: {max_size:.2f} < {self.min_size:.2f}"
            )
            return None

        return ArbitrageOpportunity(
            market_name=market.name,
            direction=direction,
            pm_token=pm_ob.token_id,
            op_token=op_ob.token_id,
            pm_price=pm_price,
            op_price=op_price,
            total_cost=total_cost,
            profit_pct=profit_pct,
            max_size=max_size,
        )

    def calculate_optimal_size(
        self,
        opportunity: ArbitrageOpportunity,
    ) -> float:
        """
        Calculate optimal position size considering:
        - Available liquidity (max_size)
        - Position size limits
        - Expected profit vs fixed costs
        """
        # Start with max available
        size = opportunity.max_size

        # Apply position limits
        size = min(size, self.max_size)
        size = max(size, self.min_size)

        # Calculate expected profit
        expected_profit = (1.0 - opportunity.total_cost) * size
        gas_cost = self.pm_gas + self.op_gas

        # Ensure profit exceeds gas costs with margin
        if expected_profit < gas_cost * 2:
            # Reduce size or return minimum
            self.logger.debug(
                f"Profit {expected_profit:.4f} too close to gas {gas_cost:.4f}"
            )

        return size

    def check_arbitrage_pm_pf(
        self,
        market: MarketPair,
        pm_yes: Optional[Orderbook],
        pm_no: Optional[Orderbook],
        pf_yes: Optional[Orderbook],
        pf_no: Optional[Orderbook],
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check for arbitrage opportunities between Polymarket and Predict.fun.

        Direction 1: PM_YES + PF_NO (Buy Yes on PM, Buy No on Predict.fun)
        Direction 2: PM_NO + PF_YES (Buy No on PM, Buy Yes on Predict.fun)

        Returns the most profitable opportunity if above threshold.
        """
        opportunities = []

        # Check data freshness
        orderbooks = [pm_yes, pm_no, pf_yes, pf_no]
        if not all(orderbooks):
            self.logger.debug(f"Missing orderbook data for {market.name}")
            return None

        for ob in orderbooks:
            if not ob.is_fresh(self.freshness_ms):
                self.logger.debug(f"Stale orderbook for {ob.token_id}")
                return None

        # Direction 1: PM Yes + PF No
        opp1 = self._check_direction_pm_pf(
            market=market,
            direction=Direction.PM_YES_PF_NO,
            pm_ob=pm_yes,
            pf_ob=pf_no,
        )
        if opp1:
            opportunities.append(opp1)

        # Direction 2: PM No + PF Yes
        opp2 = self._check_direction_pm_pf(
            market=market,
            direction=Direction.PM_NO_PF_YES,
            pm_ob=pm_no,
            pf_ob=pf_yes,
        )
        if opp2:
            opportunities.append(opp2)

        # Return the most profitable opportunity
        if opportunities:
            best = max(opportunities, key=lambda x: x.profit_pct)
            self.logger.info(
                f"Arbitrage found: {market.name} | {best.direction.value} | "
                f"PM={best.pm_price:.4f} PF={best.pf_price:.4f} | "
                f"Profit={best.profit_pct:.2%}"
            )
            return best

        return None

    def _check_direction_pm_pf(
        self,
        market: MarketPair,
        direction: Direction,
        pm_ob: Orderbook,
        pf_ob: Orderbook,
    ) -> Optional[ArbitrageOpportunity]:
        """Check a single arbitrage direction for PM ⟷ PF."""
        # We're buying at ask prices
        pm_price = pm_ob.best_ask
        pf_price = pf_ob.best_ask

        # Calculate total cost
        # Token cost + fees + gas
        token_cost = pm_price + pf_price
        fee_cost = (pm_price * self.pm_fee) + (pf_price * self.pf_fee)
        gas_cost = self.pm_gas + self.pf_gas

        # Total cost per unit (normalized to 1.0 payout)
        total_cost = token_cost + fee_cost + gas_cost

        # Check if profitable
        if total_cost >= 1.0:
            return None

        # Calculate profit percentage
        profit_pct = (1.0 - total_cost) / total_cost

        # Check minimum profit threshold
        if profit_pct < self.min_profit:
            return None

        # Calculate max size based on orderbook depth
        max_size = min(
            pm_ob.ask_size,
            pf_ob.ask_size,
            self.max_size,
        )

        # Check minimum size
        if max_size < self.min_size:
            self.logger.debug(
                f"Size too small: {max_size:.2f} < {self.min_size:.2f}"
            )
            return None

        return ArbitrageOpportunity(
            market_name=market.name,
            direction=direction,
            pm_token=pm_ob.token_id,
            pf_token=pf_ob.token_id,
            pm_price=pm_price,
            pf_price=pf_price,
            total_cost=total_cost,
            profit_pct=profit_pct,
            max_size=max_size,
        )
