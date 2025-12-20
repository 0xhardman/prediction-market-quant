"""Live tests for PolymarketClient.

These tests connect to real Polymarket API.
Run with: uv run pytest src/tests/test_polymarket.py -v
"""

import pytest

from src.clients import PolymarketClient
from src.models import Side, OrderStatus
from src.exceptions import NotConnectedError, OrderNotFoundError, OrderRejectedError


@pytest.mark.live
@pytest.mark.asyncio
class TestPolymarketConnection:
    """Test connection and authentication."""

    async def test_connect_success(self, pm_token_id):
        """Test successful connection."""
        client = PolymarketClient(token_id=pm_token_id)
        await client.connect()

        assert client.is_connected
        await client.close()
        assert not client.is_connected

    async def test_context_manager(self, pm_token_id):
        """Test async context manager."""
        async with PolymarketClient(token_id=pm_token_id) as client:
            assert client.is_connected
        # After exiting context, should be disconnected
        assert not client.is_connected

    async def test_not_connected_error(self, pm_token_id):
        """Test NotConnectedError when not connected."""
        client = PolymarketClient(token_id=pm_token_id)

        with pytest.raises(NotConnectedError):
            await client.get_orderbook()


@pytest.mark.live
@pytest.mark.asyncio
class TestPolymarketOrderbook:
    """Test orderbook operations."""

    async def test_get_orderbook(self, pm_client):
        """Test fetching orderbook."""
        ob = await pm_client.get_orderbook()

        assert ob is not None
        assert isinstance(ob.bids, list)
        assert isinstance(ob.asks, list)
        assert ob.timestamp > 0

    async def test_orderbook_properties(self, pm_client):
        """Test orderbook helper properties."""
        ob = await pm_client.get_orderbook()

        # Best bid/ask should be float or None
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
class TestPolymarketOrders:
    """Test order operations."""

    async def test_place_and_cancel_order(self, pm_client):
        """Test placing and cancelling an order."""
        try:
            # Place a low-price order that won't fill
            order = await pm_client.place_order(
                side=Side.BUY,
                price=0.01,
                size=5.0,  # PM minimum size
            )

            assert order.id != ""
            assert order.side == Side.BUY
            assert order.price == 0.01
            assert order.size == 5.0

            # Cancel the order
            cancelled = await pm_client.cancel_order(order.id)
            assert cancelled is True

        except OrderRejectedError as e:
            pytest.skip(f"Order rejected: {e.reason}")

    async def test_cancel_nonexistent_order(self, pm_client):
        """Test cancelling a non-existent order."""
        # PM may return False or raise OrderNotFoundError
        try:
            result = await pm_client.cancel_order("nonexistent_order_id_12345")
            assert result is False
        except OrderNotFoundError:
            pass  # Also acceptable

    async def test_get_orders(self, pm_client):
        """Test getting order list."""
        orders = await pm_client.get_orders()

        assert isinstance(orders, list)
        # Each order should have required fields
        for order in orders:
            assert order.id != ""
            assert order.side in (Side.BUY, Side.SELL)


@pytest.mark.live
@pytest.mark.asyncio
class TestPolymarketBalance:
    """Test balance operations."""

    async def test_get_balance(self, pm_client):
        """Test getting USDC balance."""
        balance = await pm_client.get_balance()

        assert isinstance(balance, float)
        assert balance >= 0


@pytest.mark.live
@pytest.mark.asyncio
class TestPolymarketEdgeCases:
    """Test edge cases and error handling."""

    async def test_invalid_price_order(self, pm_client):
        """Test order with invalid price."""
        # Price > 1 should fail
        with pytest.raises(Exception):
            await pm_client.place_order(Side.BUY, price=1.5, size=5.0)

    async def test_minimum_size(self, pm_client):
        """Test order with size below minimum."""
        # Size < 5 should fail for Polymarket
        with pytest.raises(Exception):
            await pm_client.place_order(Side.BUY, price=0.01, size=1.0)

    async def test_multiple_orderbook_calls(self, pm_client):
        """Test multiple consecutive orderbook calls."""
        import asyncio
        for _ in range(3):
            ob = await pm_client.get_orderbook()
            assert ob is not None
            await asyncio.sleep(0.5)  # Avoid rate limiting

    async def test_is_connected_property(self, pm_token_id):
        """Test is_connected property reflects actual state."""
        client = PolymarketClient(token_id=pm_token_id)

        assert not client.is_connected
        await client.connect()
        assert client.is_connected
        await client.close()
        assert not client.is_connected
