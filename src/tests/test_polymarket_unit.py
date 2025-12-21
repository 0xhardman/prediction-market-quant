"""Unit tests for PolymarketClient (no live API connection required).

Run with: uv run pytest src/tests/test_polymarket_unit.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.clients.polymarket import PolymarketClient
from src.models import Side, OrderStatus, Order, Trade
from src.exceptions import NotConnectedError, InsufficientBalanceError, OrderRejectedError


class TestPolymarketClientInit:
    """Test client initialization."""

    def test_init_with_token_id(self):
        """Test initialization with token_id."""
        client = PolymarketClient(token_id="test_token")
        assert client.token_id == "test_token"
        assert not client.is_connected

    def test_init_not_connected(self):
        """Test client starts disconnected."""
        client = PolymarketClient(token_id="test_token")
        assert client._client is None
        assert client._http is None


class TestPolymarketClientNotConnected:
    """Test behavior when client is not connected."""

    @pytest.fixture
    def client(self):
        return PolymarketClient(token_id="test_token")

    @pytest.mark.asyncio
    async def test_get_orderbook_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_orderbook()

    @pytest.mark.asyncio
    async def test_place_order_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.place_order(Side.BUY, 0.5, 10)

    @pytest.mark.asyncio
    async def test_cancel_order_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.cancel_order("order_id")

    @pytest.mark.asyncio
    async def test_get_balance_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_balance()

    @pytest.mark.asyncio
    async def test_get_orders_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_orders()

    @pytest.mark.asyncio
    async def test_place_market_order_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.place_market_order(Side.BUY, size=10)

    @pytest.mark.asyncio
    async def test_cancel_all_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.cancel_all()

    @pytest.mark.asyncio
    async def test_get_trades_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_trades()


class TestPlaceMarketOrderValidation:
    """Test place_market_order parameter validation."""

    @pytest.fixture
    def connected_client(self):
        client = PolymarketClient(token_id="test_token")
        client._client = MagicMock()
        client._http = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_buy_requires_size_or_value(self, connected_client):
        """BUY market order requires either size or value."""
        with pytest.raises(ValueError, match="requires either size or value"):
            await connected_client.place_market_order(Side.BUY)

    @pytest.mark.asyncio
    async def test_sell_requires_size(self, connected_client):
        """SELL market order requires size."""
        with pytest.raises(ValueError, match="requires size"):
            await connected_client.place_market_order(Side.SELL, value=100)


class TestOrderbookSorting:
    """Test orderbook sorting behavior."""

    @pytest.fixture
    def connected_client(self):
        client = PolymarketClient(token_id="test_token")
        client._client = MagicMock()
        client._http = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_orderbook_sorts_bids_descending(self, connected_client):
        """Test that bids are sorted highest price first."""
        # API returns bids in ascending order (0.01 -> 0.07)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "bids": [
                {"price": "0.01", "size": "100"},
                {"price": "0.03", "size": "50"},
                {"price": "0.07", "size": "200"},
            ],
            "asks": [],
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        ob = await connected_client.get_orderbook()

        # Should be sorted descending: 0.07, 0.03, 0.01
        assert ob.bids[0] == (0.07, 200.0)
        assert ob.bids[1] == (0.03, 50.0)
        assert ob.bids[2] == (0.01, 100.0)
        assert ob.best_bid == 0.07

    @pytest.mark.asyncio
    async def test_orderbook_sorts_asks_ascending(self, connected_client):
        """Test that asks are sorted lowest price first."""
        # API returns asks in descending order (0.99 -> 0.08)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "bids": [],
            "asks": [
                {"price": "0.99", "size": "100"},
                {"price": "0.50", "size": "75"},
                {"price": "0.08", "size": "200"},
            ],
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        ob = await connected_client.get_orderbook()

        # Should be sorted ascending: 0.08, 0.50, 0.99
        assert ob.asks[0] == (0.08, 200.0)
        assert ob.asks[1] == (0.50, 75.0)
        assert ob.asks[2] == (0.99, 100.0)
        assert ob.best_ask == 0.08


class TestOrderParsing:
    """Test order response parsing."""

    @pytest.fixture
    def connected_client(self):
        client = PolymarketClient(token_id="test_token")
        client._client = MagicMock()
        client._http = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_get_orders_parsing(self, connected_client):
        """Test parsing of orders response."""
        mock_orders = [
            {
                "id": "order1",
                "asset_id": "token1",
                "side": "BUY",
                "price": "0.5",
                "original_size": "10",
                "size_matched": "5",
                "status": "OPEN",
            },
            {
                "id": "order2",
                "asset_id": "token2",
                "side": "SELL",
                "price": "0.7",
                "original_size": "20",
                "size_matched": "0",
                "status": "PENDING",
            },
        ]
        connected_client._client.get_orders = MagicMock(return_value=mock_orders)

        with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f)):
            orders = await connected_client.get_orders()

        assert len(orders) == 2
        assert orders[0].id == "order1"
        assert orders[0].side == Side.BUY
        assert orders[0].price == 0.5
        assert orders[0].size == 10
        assert orders[0].filled_size == 5
        assert orders[0].status == OrderStatus.OPEN

        assert orders[1].id == "order2"
        assert orders[1].side == Side.SELL
        assert orders[1].status == OrderStatus.PENDING


class TestTradeModel:
    """Test Trade model."""

    def test_trade_creation(self):
        """Test creating a Trade object."""
        trade = Trade(
            id="trade1",
            order_id="order1",
            token_id="token1",
            side=Side.BUY,
            price=0.5,
            size=10,
            fee=0.01,
        )
        assert trade.id == "trade1"
        assert trade.side == Side.BUY
        assert trade.fee == 0.01

    def test_trade_defaults(self):
        """Test Trade default values."""
        trade = Trade(
            id="trade1",
            order_id="order1",
            token_id="token1",
            side=Side.SELL,
            price=0.5,
            size=10,
        )
        assert trade.fee == 0.0
        assert trade.timestamp > 0


class TestBaseClientDefaults:
    """Test BaseClient default implementations."""

    @pytest.mark.asyncio
    async def test_base_client_market_order_not_implemented(self):
        """Test that BaseClient.place_market_order raises NotImplementedError."""
        from src.clients.base import BaseClient

        # Create a minimal concrete implementation
        class MinimalClient(BaseClient):
            async def connect(self): pass
            async def close(self): pass
            async def get_orderbook(self): pass
            async def place_order(self, side, price, size): pass
            async def cancel_order(self, order_id): pass
            async def get_balance(self): pass
            async def get_orders(self): pass

        client = MinimalClient()
        with pytest.raises(NotImplementedError, match="Market orders not supported"):
            await client.place_market_order(Side.BUY, size=10)

    @pytest.mark.asyncio
    async def test_base_client_batch_orders_not_implemented(self):
        """Test that BaseClient batch methods raise NotImplementedError."""
        from src.clients.base import BaseClient

        class MinimalClient(BaseClient):
            async def connect(self): pass
            async def close(self): pass
            async def get_orderbook(self): pass
            async def place_order(self, side, price, size): pass
            async def cancel_order(self, order_id): pass
            async def get_balance(self): pass
            async def get_orders(self): pass

        client = MinimalClient()

        with pytest.raises(NotImplementedError):
            await client.place_orders([])

        with pytest.raises(NotImplementedError):
            await client.cancel_orders([])

        with pytest.raises(NotImplementedError):
            await client.cancel_all()

        with pytest.raises(NotImplementedError):
            await client.get_order("id")

        with pytest.raises(NotImplementedError):
            await client.get_trades()

        with pytest.raises(NotImplementedError):
            await client.get_midpoint()

        with pytest.raises(NotImplementedError):
            await client.get_spread()
