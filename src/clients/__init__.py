"""Market clients for prediction market platforms."""

from dotenv import load_dotenv

load_dotenv()

from .base import BaseClient
from .polymarket import PolymarketClient
from .predictfun import PredictFunClient

__all__ = ["BaseClient", "PolymarketClient", "PredictFunClient"]
