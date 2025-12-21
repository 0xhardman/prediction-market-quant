"""Unit tests for PredictFunClient (no live API connection required).

Run with: uv run pytest src/tests/test_predictfun_unit.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.clients.predictfun import PredictFunClient
from src.models import Side, OrderStatus, Order, Trade
from src.exceptions import NotConnectedError


class TestPredictFunClientInit:
    """Test client initialization."""

    def test_init_with_market_and_token(self):
        """Test initialization with market_id and token_id."""
        client = PredictFunClient(market_id=123, token_id="test_token")
        assert client.market_id == 123
        assert client.token_id == "test_token"
        assert not client.is_connected

    def test_init_not_connected(self):
        """Test client starts disconnected."""
        client = PredictFunClient(market_id=123, token_id="test_token")
        assert client._http is None
        assert client._builder is None


class TestPredictFunClientNotConnected:
    """Test behavior when client is not connected."""

    @pytest.fixture
    def client(self):
        return PredictFunClient(market_id=123, token_id="test_token")

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
    async def test_get_orders_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_orders()

    @pytest.mark.asyncio
    async def test_place_market_order_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.place_market_order(Side.BUY, size=10)

    @pytest.mark.asyncio
    async def test_place_orders_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.place_orders([(Side.BUY, 0.5, 10)])

    @pytest.mark.asyncio
    async def test_cancel_orders_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.cancel_orders(["order1", "order2"])

    @pytest.mark.asyncio
    async def test_cancel_all_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.cancel_all()

    @pytest.mark.asyncio
    async def test_get_order_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_order("order_id")

    @pytest.mark.asyncio
    async def test_get_trades_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_trades()

    @pytest.mark.asyncio
    async def test_get_position_not_connected(self, client):
        with pytest.raises(NotConnectedError):
            await client.get_position()


class TestPlaceMarketOrderValidation:
    """Test place_market_order parameter validation."""

    @pytest.fixture
    def connected_client(self):
        client = PredictFunClient(market_id=123, token_id="test_token")
        client._http = MagicMock()
        client._builder = MagicMock()
        client._jwt_expires_at = float("inf")  # Never expires
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
        client = PredictFunClient(market_id=123, token_id="test_token")
        client._http = MagicMock()
        client._builder = MagicMock()
        client._jwt_expires_at = float("inf")
        return client

    @pytest.mark.asyncio
    async def test_orderbook_sorts_bids_descending(self, connected_client):
        """Test that bids are sorted highest price first."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "bids": [[0.01, 100], [0.03, 50], [0.07, 200]],
                "asks": [],
            }
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
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "bids": [],
                "asks": [[0.99, 100], [0.50, 75], [0.08, 200]],
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        ob = await connected_client.get_orderbook()

        # Should be sorted ascending: 0.08, 0.50, 0.99
        assert ob.asks[0] == (0.08, 200.0)
        assert ob.asks[1] == (0.50, 75.0)
        assert ob.asks[2] == (0.99, 100.0)
        assert ob.best_ask == 0.08


class TestGetMidpointAndSpread:
    """Test get_midpoint and get_spread methods."""

    @pytest.fixture
    def connected_client(self):
        client = PredictFunClient(market_id=123, token_id="test_token")
        client._http = MagicMock()
        client._builder = MagicMock()
        client._jwt_expires_at = float("inf")
        return client

    @pytest.mark.asyncio
    async def test_get_midpoint(self, connected_client):
        """Test midpoint calculation."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "bids": [[0.40, 100]],
                "asks": [[0.60, 100]],
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        midpoint = await connected_client.get_midpoint()

        assert midpoint == 0.50

    @pytest.mark.asyncio
    async def test_get_spread(self, connected_client):
        """Test spread calculation."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "bids": [[0.40, 100]],
                "asks": [[0.60, 100]],
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        spread = await connected_client.get_spread()

        assert abs(spread - 0.20) < 0.001

    @pytest.mark.asyncio
    async def test_get_midpoint_no_bids(self, connected_client):
        """Test midpoint returns None when no bids."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "bids": [],
                "asks": [[0.60, 100]],
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._http.get = AsyncMock(return_value=mock_response)

        midpoint = await connected_client.get_midpoint()

        assert midpoint is None


class TestCancelOrders:
    """Test batch cancel operations."""

    @pytest.fixture
    def connected_client(self):
        client = PredictFunClient(market_id=123, token_id="test_token")
        client._http = MagicMock()
        client._builder = MagicMock()
        client._jwt_expires_at = float("inf")
        return client

    @pytest.mark.asyncio
    async def test_cancel_orders_empty_list(self, connected_client):
        """Test cancelling empty list returns empty list."""
        results = await connected_client.cancel_orders([])
        assert results == []


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
