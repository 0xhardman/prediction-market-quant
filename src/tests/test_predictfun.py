"""Live tests for PredictFunClient.

These tests connect to real Predict.fun API.
Run with: uv run pytest src/tests/test_predictfun.py -v
"""

import pytest

from src.clients import PredictFunClient
from src.models import Side, OrderStatus
from src.exceptions import (
    NotConnectedError,
    OrderNotFoundError,
    InsufficientBalanceError,
    OrderRejectedError,
)


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunConnection:
    """Test connection and authentication."""

    async def test_connect_success(self, pf_market_config):
        """Test successful connection."""
        client = PredictFunClient(
            market_id=pf_market_config["market_id"],
            token_id=pf_market_config["token_id"],
        )
        await client.connect()

        assert client.is_connected
        await client.close()
        assert not client.is_connected

    async def test_context_manager(self, pf_market_config):
        """Test async context manager."""
        async with PredictFunClient(
            market_id=pf_market_config["market_id"],
            token_id=pf_market_config["token_id"],
        ) as client:
            assert client.is_connected
        # After exiting context, should be disconnected
        assert not client.is_connected

    async def test_not_connected_error(self, pf_market_config):
        """Test NotConnectedError when not connected."""
        client = PredictFunClient(
            market_id=pf_market_config["market_id"],
            token_id=pf_market_config["token_id"],
        )

        with pytest.raises(NotConnectedError):
            await client.get_orderbook()


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunOrderbook:
    """Test orderbook operations."""

    async def test_get_orderbook(self, pf_client):
        """Test fetching orderbook."""
        ob = await pf_client.get_orderbook()

        assert ob is not None
        assert isinstance(ob.bids, list)
        assert isinstance(ob.asks, list)
        assert ob.timestamp > 0

    async def test_orderbook_properties(self, pf_client):
        """Test orderbook helper properties."""
        ob = await pf_client.get_orderbook()

        if ob.bids:
            assert isinstance(ob.best_bid, float)
            assert 0 <= ob.best_bid <= 1
        if ob.asks:
            assert isinstance(ob.best_ask, float)
            assert 0 <= ob.best_ask <= 1
        if ob.bids and ob.asks:
            assert ob.spread is not None
            assert ob.spread >= 0


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunOrders:
    """Test order operations."""

    async def test_place_and_cancel_order(self, pf_client):
        """Test placing and cancelling an order."""
        try:
            # Place a low-price order that won't fill
            order = await pf_client.place_order(
                side=Side.BUY,
                price=0.01,
                size=100.0,  # PF minimum value ~0.9 USD
            )

            assert order.id != ""
            assert order.side == Side.BUY
            assert order.price == 0.01
            assert order.size == 100.0
            assert order.status == OrderStatus.OPEN

            # Cancel the order
            cancelled = await pf_client.cancel_order(order.id)
            assert cancelled is True

        except InsufficientBalanceError:
            pytest.skip("Insufficient balance for order test")
        except OrderRejectedError as e:
            pytest.skip(f"Order rejected: {e.reason}")

    async def test_cancel_nonexistent_order(self, pf_client):
        """Test cancelling a non-existent order."""
        with pytest.raises(OrderNotFoundError):
            await pf_client.cancel_order("nonexistent_order_hash_12345")

    async def test_get_orders(self, pf_client):
        """Test getting order list."""
        orders = await pf_client.get_orders()

        assert isinstance(orders, list)
        for order in orders:
            assert order.id != ""
            assert order.side in (Side.BUY, Side.SELL)


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunBalance:
    """Test balance operations."""

    async def test_get_balance(self, pf_client):
        """Test getting USDT balance."""
        balance = await pf_client.get_balance()

        assert isinstance(balance, float)
        assert balance >= 0


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunEdgeCases:
    """Test edge cases and error handling."""

    async def test_multiple_orderbook_calls(self, pf_client):
        """Test multiple consecutive orderbook calls."""
        for _ in range(3):
            ob = await pf_client.get_orderbook()
            assert ob is not None

    async def test_balance_after_operations(self, pf_client):
        """Test balance remains consistent after operations."""
        balance1 = await pf_client.get_balance()
        await pf_client.get_orderbook()
        await pf_client.get_orders()
        balance2 = await pf_client.get_balance()

        # Balance should be same (no trades executed)
        assert balance1 == balance2


@pytest.mark.live
@pytest.mark.asyncio
class TestPredictFunJWTRefresh:
    """Test JWT token refresh mechanism."""

    async def test_jwt_expiry_tracking(self, pf_client):
        """Test that JWT expiry time is set after connection."""
        # After connection, JWT expiry should be set
        assert pf_client._jwt_expires_at > 0

    async def test_operations_refresh_token_if_needed(self, pf_client):
        """Test that operations check and refresh token if needed."""
        original_expiry = pf_client._jwt_expires_at

        # Force token to appear expired
        pf_client._jwt_expires_at = 0

        # This should trigger a refresh
        await pf_client.get_orderbook()

        # Expiry should be updated
        assert pf_client._jwt_expires_at > original_expiry
