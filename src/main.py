"""Main entry point for the arbitrage system."""

import asyncio
import signal
import sys
from pathlib import Path

from .clients.polymarket import PolymarketClient
from .clients.predictfun import PredictFunClient
from .config import load_config, validate_config
from .engine.arbitrage import ArbitrageEngine
from .engine.executor import OrderExecutor
from .utils.logger import setup_logger, get_logger


class ArbitrageRunner:
    """Main runner for the arbitrage system."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.running = False

        # Components (initialized in setup)
        self.config = None
        self.logger = None
        self.pm_client = None
        self.pf_client = None
        self.engine = None
        self.executor = None

    async def setup(self) -> bool:
        """Initialize all components."""
        # Load configuration
        self.config = load_config(self.config_path)

        # Setup logging
        self.logger = setup_logger(
            level=self.config.monitoring.log_level,
            log_to_file=self.config.monitoring.log_to_file,
            log_file=self.config.monitoring.log_file,
        )

        self.logger.info("=" * 60)
        self.logger.info("Polymarket-Predict.fun Arbitrage System")
        self.logger.info("=" * 60)

        # Validate configuration
        errors = validate_config(self.config)
        if errors:
            for err in errors:
                self.logger.error(f"Config error: {err}")
            self.logger.error("Please fix configuration errors before running")
            return False

        # Count enabled markets
        enabled_markets = [m for m in self.config.markets if m.enabled]
        self.logger.info(f"Enabled markets: {len(enabled_markets)}")

        # Initialize clients
        self.logger.info("Initializing clients...")
        self.pm_client = PolymarketClient(self.config)
        self.pf_client = PredictFunClient(self.config)

        # Initialize engine and executor
        self.engine = ArbitrageEngine(self.config)
        self.executor = OrderExecutor(
            self.config,
            self.pm_client,
            self.pf_client,
        )

        self.logger.info("Setup complete")
        return True

    async def connect(self) -> None:
        """Connect to all platforms."""
        self.logger.info("Connecting to platforms...")

        # Connect clients
        await self.pm_client.connect()
        await self.pf_client.connect()

        # Subscribe to markets
        for market in self.config.markets:
            if not market.enabled:
                continue

            self.logger.info(f"Subscribing to market: {market.name}")

            # Subscribe Polymarket tokens (if configured)
            if market.polymarket:
                await self.pm_client.subscribe_orderbook(
                    market.polymarket.yes_token_id
                )
                await self.pm_client.subscribe_orderbook(
                    market.polymarket.no_token_id
                )

        self.logger.info("Connected to all platforms")

    async def disconnect(self) -> None:
        """Disconnect from all platforms."""
        self.logger.info("Disconnecting...")
        await self.pm_client.disconnect()
        await self.pf_client.disconnect()
        self.logger.info("Disconnected")

    async def run_loop(self) -> None:
        """Main arbitrage detection loop."""
        self.logger.info("Starting arbitrage loop...")
        poll_interval = self.config.monitoring.opinion_poll_interval

        while self.running:
            try:
                for market in self.config.markets:
                    if not market.enabled:
                        continue

                    # Skip if either platform is not configured
                    if not market.polymarket or not market.predict_fun:
                        continue

                    # Get Polymarket orderbooks (from WebSocket cache)
                    pm_yes = await self.pm_client.get_orderbook(
                        market.polymarket.yes_token_id
                    )
                    pm_no = await self.pm_client.get_orderbook(
                        market.polymarket.no_token_id
                    )

                    # Get Predict.fun orderbooks (REST fetch)
                    pf_yes, pf_no = await asyncio.gather(
                        self.pf_client.fetch_orderbook(
                            market.predict_fun.yes_token_id,
                            market_id=market.predict_fun.market_id,
                        ),
                        self.pf_client.fetch_orderbook(
                            market.predict_fun.no_token_id,
                            market_id=market.predict_fun.market_id,
                        ),
                    )

                    # Check for arbitrage opportunity (PM ⟷ PF)
                    opportunity = self.engine.check_arbitrage_pm_pf(
                        market,
                        pm_yes,
                        pm_no,
                        pf_yes,
                        pf_no,
                    )

                    if opportunity:
                        # Calculate optimal size
                        size = self.engine.calculate_optimal_size(opportunity)

                        self.logger.info(
                            f"OPPORTUNITY: {market.name} | "
                            f"{opportunity.direction.value} | "
                            f"Profit: {opportunity.profit_pct:.2%} | "
                            f"Size: ${size:.2f}"
                        )

                        # Execute arbitrage
                        result = await self.executor.execute(opportunity, size)

                        if result.success:
                            self.logger.info(
                                f"SUCCESS: Executed arbitrage for ${size:.2f}"
                            )
                        else:
                            self.logger.warning(
                                f"FAILED: {result.reason} | "
                                f"Unhedged: ${result.unhedged:.2f}"
                            )

                # Check unhedged positions
                unhedged = self.executor.get_total_unhedged_exposure()
                if unhedged > 0:
                    self.logger.warning(
                        f"Total unhedged exposure: ${unhedged:.2f}"
                    )

                # Wait before next iteration
                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(poll_interval)

    async def run(self) -> None:
        """Run the arbitrage system."""
        # Setup
        if not await self.setup():
            return

        # Connect
        await self.connect()

        # Setup signal handlers
        self.running = True

        def signal_handler(sig, frame):
            self.logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            # Run main loop
            await self.run_loop()
        finally:
            # Cleanup
            await self.disconnect()
            self.logger.info("Shutdown complete")


def main():
    """Entry point."""
    # Check for config file
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    if not Path(config_path).exists():
        print(f"Error: Config file not found: {config_path}")
        print("Please create a config.yaml file with your settings.")
        sys.exit(1)

    # Run
    runner = ArbitrageRunner(config_path)
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
