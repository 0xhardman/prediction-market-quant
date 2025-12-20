"""Order execution with risk management."""

import asyncio
from typing import List, Optional

from ..clients.polymarket import PolymarketClient
from ..clients.predictfun import PredictFunClient
from ..config import Config
from ..models import (
    ArbitrageOpportunity,
    ExecutionResult,
    OrderResult,
    OrderStatus,
    Platform,
    Side,
    UnhedgedPosition,
)
from ..utils.logger import get_logger


class OrderExecutor:
    """Executes arbitrage orders with risk management."""

    def __init__(
        self,
        config: Config,
        pm_client: PolymarketClient,
        pf_client: PredictFunClient,
    ):
        self.config = config
        self.pm_client = pm_client
        self.pf_client = pf_client
        self.logger = get_logger()

        # Track unhedged positions
        self._unhedged_positions: List[UnhedgedPosition] = []

        # Execution parameters
        self.order_timeout_ms = config.arbitrage.order_timeout_ms
        self.aggressive_markup = config.arbitrage.aggressive_price_markup
        self.max_unhedged = config.arbitrage.max_unhedged_exposure

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        size: float,
    ) -> ExecutionResult:
        """
        Execute arbitrage with mixed strategy:
        - Polymarket: FOK order (Fill-Or-Kill)
        - Predict.fun: Aggressive limit order + timeout cancel

        Execution order: PM first (FOK ensures fill), then PF
        """
        self.logger.info(
            f"Executing arbitrage: {opportunity.direction.value} | "
            f"Size: ${size:.2f}"
        )

        # Step 1: Place Polymarket FOK order
        pm_result = await self.pm_client.place_fok_order(
            token_id=opportunity.pm_token,
            side=Side.BUY,
            price=opportunity.pm_price,
            size=size,
        )

        if not pm_result.success:
            self.logger.info("PM order not filled, abandoning arbitrage")
            return ExecutionResult(
                success=False,
                reason="PM_NOT_FILLED",
                pm_order=pm_result,
            )

        self.logger.info(f"PM order filled: {pm_result.filled_size}")

        # Step 2: Place Predict.fun aggressive limit order
        aggressive_price = opportunity.pf_price * (1 + self.aggressive_markup)
        pf_order = await self.pf_client.place_order(
            token_id=opportunity.pf_token,
            side=Side.BUY,
            price=aggressive_price,
            size=size,
        )

        if pf_order.status == OrderStatus.FAILED:
            self.logger.error("Predict.fun order failed immediately")
            await self._handle_unhedged(
                filled_order=pm_result,
                missing_platform=Platform.PREDICT_FUN,
                expected_size=size,
                reason="PF_ORDER_FAILED",
            )
            return ExecutionResult(
                success=False,
                reason="PF_ORDER_FAILED",
                pm_order=pm_result,
                pf_order=pf_order,
                unhedged=size,
            )

        # Step 3: Wait and check fill status
        timeout_sec = self.order_timeout_ms / 1000
        await asyncio.sleep(timeout_sec)

        pf_status = await self.pf_client.get_order_status(pf_order.order_id)

        if pf_status is None:
            self.logger.error("Failed to get Predict.fun order status")
            pf_status = pf_order

        # Step 4: Evaluate result
        if pf_status.filled_size >= size * 0.95:  # 95%+ = success
            self.logger.info(
                f"Arbitrage successful! PM: {pm_result.filled_size}, "
                f"PF: {pf_status.filled_size}"
            )
            return ExecutionResult(
                success=True,
                pm_order=pm_result,
                pf_order=pf_status,
            )

        # Step 5: Cancel unfilled portion
        if pf_status.status == OrderStatus.PENDING:
            await self.pf_client.cancel_order(pf_order.order_id)
            self.logger.info(f"Cancelled Predict.fun order: {pf_order.order_id}")

        # Step 6: Handle unhedged exposure
        if pf_status.filled_size > 0:
            # Partial fill
            unhedged = size - pf_status.filled_size
            self.logger.warning(f"Partial fill, unhedged: ${unhedged:.2f}")
            await self._handle_unhedged(
                filled_order=pm_result,
                missing_platform=Platform.PREDICT_FUN,
                expected_size=unhedged,
                reason="PARTIAL_FILL",
            )
            return ExecutionResult(
                success=False,
                reason="PARTIAL_FILL",
                pm_order=pm_result,
                pf_order=pf_status,
                unhedged=unhedged,
            )
        else:
            # No fill on Predict.fun side
            self.logger.warning(f"Predict.fun not filled, unhedged: ${size:.2f}")
            await self._handle_unhedged(
                filled_order=pm_result,
                missing_platform=Platform.PREDICT_FUN,
                expected_size=size,
                reason="PF_NOT_FILLED",
            )
            return ExecutionResult(
                success=False,
                reason="PF_NOT_FILLED",
                pm_order=pm_result,
                pf_order=pf_status,
                unhedged=size,
            )

    async def _handle_unhedged(
        self,
        filled_order: OrderResult,
        missing_platform: Platform,
        expected_size: float,
        reason: str,
    ) -> None:
        """Handle unhedged position exposure."""
        position = UnhedgedPosition(
            filled_order=filled_order,
            missing_platform=missing_platform,
            expected_size=expected_size,
            reason=reason,
        )
        self._unhedged_positions.append(position)

        # Log for manual attention
        self.logger.warning(
            f"UNHEDGED POSITION: {filled_order.platform.value} "
            f"{filled_order.token_id} ${expected_size:.2f} | "
            f"Reason: {reason}"
        )

        # Check total unhedged exposure
        total_unhedged = sum(
            p.expected_size for p in self._unhedged_positions if not p.resolved
        )

        if total_unhedged > self.max_unhedged:
            self.logger.error(
                f"CRITICAL: Total unhedged exposure ${total_unhedged:.2f} "
                f"exceeds limit ${self.max_unhedged:.2f}"
            )

    async def retry_unhedged(
        self,
        position: UnhedgedPosition,
        max_retries: int = 3,
    ) -> bool:
        """Attempt to hedge an unhedged position."""
        for attempt in range(max_retries):
            position.retry_count += 1
            self.logger.info(
                f"Retry {position.retry_count}/{max_retries} for unhedged position"
            )

            # Get current orderbook
            if position.missing_platform == Platform.PREDICT_FUN:
                ob = await self.pf_client.get_orderbook(
                    position.filled_order.token_id
                )
                if ob and ob.best_ask > 0:
                    result = await self.pf_client.place_order(
                        token_id=ob.token_id,
                        side=Side.BUY,
                        price=ob.best_ask * 1.01,  # 1% above ask
                        size=position.expected_size,
                    )
                    if result.success:
                        position.resolved = True
                        self.logger.info("Unhedged position resolved")
                        return True

            await asyncio.sleep(1)  # Wait between retries

        return False

    def get_unhedged_positions(self) -> List[UnhedgedPosition]:
        """Get list of unresolved unhedged positions."""
        return [p for p in self._unhedged_positions if not p.resolved]

    def get_total_unhedged_exposure(self) -> float:
        """Get total unhedged dollar exposure."""
        return sum(
            p.expected_size for p in self._unhedged_positions if not p.resolved
        )
