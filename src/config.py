"""Configuration loader with environment variable support."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


@dataclass
class PolymarketMarket:
    """Polymarket market configuration."""
    condition_id: str
    yes_token_id: str
    no_token_id: str


@dataclass
class OpinionMarket:
    """Opinion market configuration."""
    market_id: int
    yes_token_id: str
    no_token_id: str


@dataclass
class MarketPair:
    """A pair of matched markets across platforms."""
    name: str
    enabled: bool
    polymarket: PolymarketMarket
    opinion: OpinionMarket


@dataclass
class ArbitrageConfig:
    """Arbitrage parameters."""
    min_profit_threshold: float = 0.02
    max_position_size: float = 50.0
    min_position_size: float = 10.0
    max_unhedged_exposure: float = 50.0
    price_freshness_ms: int = 500
    order_timeout_ms: int = 1000
    aggressive_price_markup: float = 0.002


@dataclass
class PlatformFees:
    """Fee configuration for a platform."""
    taker_fee: float = 0.0
    gas_estimate: float = 0.05


@dataclass
class FeesConfig:
    """Combined fees configuration."""
    polymarket: PlatformFees = field(default_factory=PlatformFees)
    opinion: PlatformFees = field(default_factory=lambda: PlatformFees(taker_fee=0.01, gas_estimate=0.10))


@dataclass
class MonitoringConfig:
    """Monitoring parameters."""
    opinion_poll_interval: float = 0.5
    log_level: str = "INFO"
    log_to_file: bool = True
    log_file: str = "arbitrage.log"


@dataclass
class PolymarketCredentials:
    """Polymarket API credentials."""
    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    proxy_address: str = ""


@dataclass
class OpinionCredentials:
    """Opinion API credentials."""
    api_key: str = ""
    private_key: str = ""


@dataclass
class CredentialsConfig:
    """Combined credentials configuration."""
    polymarket: PolymarketCredentials = field(default_factory=PolymarketCredentials)
    opinion: OpinionCredentials = field(default_factory=OpinionCredentials)


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    enabled: bool = False
    http: str = ""
    https: str = ""


@dataclass
class Config:
    """Main configuration object."""
    markets: list[MarketPair] = field(default_factory=list)
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    fees: FeesConfig = field(default_factory=FeesConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)


def _expand_env_vars(value: str) -> str:
    """Expand environment variables in string values."""
    if not isinstance(value, str):
        return value

    # Match ${VAR_NAME} pattern
    pattern = r'\$\{([^}]+)\}'

    def replace(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return re.sub(pattern, replace, value)


def _expand_env_vars_recursive(obj):
    """Recursively expand environment variables in a dictionary."""
    if isinstance(obj, dict):
        return {k: _expand_env_vars_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars_recursive(item) for item in obj]
    elif isinstance(obj, str):
        return _expand_env_vars(obj)
    return obj


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from YAML file with environment variable expansion."""
    # Load .env file if exists
    env_path = Path(config_path).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Load YAML config
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    # Expand environment variables
    config_data = _expand_env_vars_recursive(raw_config)

    # Parse markets
    markets = []
    for m in config_data.get("markets", []):
        pm = m.get("polymarket", {})
        op = m.get("opinion", {})
        markets.append(MarketPair(
            name=m.get("name", ""),
            enabled=m.get("enabled", False),
            polymarket=PolymarketMarket(
                condition_id=pm.get("condition_id", ""),
                yes_token_id=pm.get("yes_token_id", ""),
                no_token_id=pm.get("no_token_id", ""),
            ),
            opinion=OpinionMarket(
                market_id=op.get("market_id", 0),
                yes_token_id=op.get("yes_token_id", ""),
                no_token_id=op.get("no_token_id", ""),
            ),
        ))

    # Parse arbitrage config
    arb_data = config_data.get("arbitrage", {})
    arbitrage = ArbitrageConfig(
        min_profit_threshold=arb_data.get("min_profit_threshold", 0.02),
        max_position_size=arb_data.get("max_position_size", 50.0),
        min_position_size=arb_data.get("min_position_size", 10.0),
        max_unhedged_exposure=arb_data.get("max_unhedged_exposure", 50.0),
        price_freshness_ms=arb_data.get("price_freshness_ms", 500),
        order_timeout_ms=arb_data.get("order_timeout_ms", 1000),
        aggressive_price_markup=arb_data.get("aggressive_price_markup", 0.002),
    )

    # Parse fees config
    fees_data = config_data.get("fees", {})
    pm_fees = fees_data.get("polymarket", {})
    op_fees = fees_data.get("opinion", {})
    fees = FeesConfig(
        polymarket=PlatformFees(
            taker_fee=pm_fees.get("taker_fee", 0.0),
            gas_estimate=pm_fees.get("gas_estimate", 0.05),
        ),
        opinion=PlatformFees(
            taker_fee=op_fees.get("taker_fee", 0.01),
            gas_estimate=op_fees.get("gas_estimate", 0.10),
        ),
    )

    # Parse monitoring config
    mon_data = config_data.get("monitoring", {})
    monitoring = MonitoringConfig(
        opinion_poll_interval=mon_data.get("opinion_poll_interval", 0.5),
        log_level=mon_data.get("log_level", "INFO"),
        log_to_file=mon_data.get("log_to_file", True),
        log_file=mon_data.get("log_file", "arbitrage.log"),
    )

    # Parse credentials config
    creds_data = config_data.get("credentials", {})
    pm_creds = creds_data.get("polymarket", {})
    op_creds = creds_data.get("opinion", {})
    credentials = CredentialsConfig(
        polymarket=PolymarketCredentials(
            private_key=pm_creds.get("private_key", ""),
            api_key=pm_creds.get("api_key", ""),
            api_secret=pm_creds.get("api_secret", ""),
            api_passphrase=pm_creds.get("api_passphrase", ""),
            proxy_address=pm_creds.get("proxy_address", ""),
        ),
        opinion=OpinionCredentials(
            api_key=op_creds.get("api_key", ""),
            private_key=op_creds.get("private_key", ""),
        ),
    )

    # Parse proxy config
    proxy_data = config_data.get("proxy", {})
    proxy = ProxyConfig(
        enabled=proxy_data.get("enabled", False),
        http=proxy_data.get("http", ""),
        https=proxy_data.get("https", ""),
    )

    return Config(
        markets=markets,
        arbitrage=arbitrage,
        fees=fees,
        monitoring=monitoring,
        credentials=credentials,
        proxy=proxy,
    )


def validate_config(config: Config) -> list[str]:
    """Validate configuration and return list of errors."""
    errors = []

    # Check for enabled markets
    enabled_markets = [m for m in config.markets if m.enabled]
    if not enabled_markets:
        errors.append("No enabled markets configured")

    # Check credentials
    if not config.credentials.polymarket.private_key:
        errors.append("Polymarket private key not configured")
    if not config.credentials.polymarket.api_key:
        errors.append("Polymarket API key not configured")
    if not config.credentials.opinion.api_key:
        errors.append("Opinion API key not configured")

    # Check arbitrage parameters
    if config.arbitrage.min_profit_threshold <= 0:
        errors.append("min_profit_threshold must be positive")
    if config.arbitrage.max_position_size <= 0:
        errors.append("max_position_size must be positive")

    return errors
