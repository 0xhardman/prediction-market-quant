"""Pytest configuration and fixtures for client tests."""

import asyncio
import os

import pytest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "live: mark test as requiring live API connection"
    )


@pytest.fixture(autouse=True)
def _check_live_credentials(request):
    """Auto-skip live tests if credentials not available."""
    # Only check for tests marked with 'live'
    if not any(m.name == "live" for m in request.node.iter_markers()):
        return

    # Determine which platform based on test file name
    test_file = request.node.fspath.basename

    if "polymarket" in test_file:
        if not os.getenv("PM_PRIVATE_KEY"):
            pytest.skip("PM_PRIVATE_KEY not set - skipping live PM test")

    elif "predictfun" in test_file:
        missing = []
        if not os.getenv("PREDICT_FUN_API_KEY"):
            missing.append("PREDICT_FUN_API_KEY")
        if not os.getenv("PREDICT_FUN_PRIVATE_KEY"):
            missing.append("PREDICT_FUN_PRIVATE_KEY")
        if not os.getenv("PREDICT_FUN_SMART_WALLET"):
            missing.append("PREDICT_FUN_SMART_WALLET")
        if missing:
            pytest.skip(f"Missing credentials: {', '.join(missing)}")


@pytest.fixture(scope="session")
def pm_token_id():
    """Get a valid Polymarket token ID for testing."""
    import httpx

    async def fetch():
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://clob.polymarket.com/sampling-markets", timeout=10
            )
            markets = resp.json().get("data", [])
            for m in markets[:20]:
                if m.get("closed"):
                    continue
                tokens = m.get("tokens", [])
                if tokens:
                    token_id = tokens[0].get("token_id", "")
                    if token_id:
                        return token_id
        return None

    return asyncio.run(fetch())


@pytest.fixture(scope="session")
def pf_market_config():
    """Predict.fun test market configuration.

    Uses environment variables if available, otherwise defaults.
    """
    return {
        "market_id": int(os.getenv("PF_TEST_MARKET_ID", "415")),
        "token_id": os.getenv(
            "PF_TEST_TOKEN_ID",
            "14862668150972542930258837689755111839426102234146323070055218172124000064169",
        ),
    }


@pytest.fixture
async def pm_client(pm_token_id):
    """Create and connect a PolymarketClient for testing."""
    from src.clients import PolymarketClient

    client = PolymarketClient(token_id=pm_token_id)
    await client.connect()
    yield client
    await client.close()


@pytest.fixture
async def pf_client(pf_market_config):
    """Create and connect a PredictFunClient for testing."""
    from src.clients import PredictFunClient

    client = PredictFunClient(
        market_id=pf_market_config["market_id"],
        token_id=pf_market_config["token_id"],
    )
    await client.connect()
    yield client
    await client.close()


@pytest.fixture
async def pm_cleanup_orders(pm_client):
    """Fixture that cleans up any orders after test."""
    created_orders = []
    yield created_orders
    # Cleanup: cancel any orders that were created during test
    for order_id in created_orders:
        try:
            await pm_client.cancel_order(order_id)
        except Exception:
            pass  # Best effort cleanup


@pytest.fixture
async def pf_cleanup_orders(pf_client):
    """Fixture that cleans up any orders after test."""
    created_orders = []
    yield created_orders
    # Cleanup: cancel any orders that were created during test
    for order_id in created_orders:
        try:
            await pf_client.cancel_order(order_id)
        except Exception:
            pass  # Best effort cleanup
