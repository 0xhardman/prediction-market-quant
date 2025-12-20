"""Configuration classes for prediction market clients."""

import os
from dataclasses import dataclass


@dataclass
class PolymarketConfig:
    """Configuration for Polymarket client.

    Attributes:
        private_key: Wallet private key for signing
        proxy_address: Proxy/funder address (optional, for GNOSIS_SAFE)
        api_key: API key (optional, will derive if not set)
        api_secret: API secret
        api_passphrase: API passphrase
    """

    private_key: str
    proxy_address: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""

    @classmethod
    def from_env(cls) -> "PolymarketConfig":
        """Load configuration from environment variables.

        Environment variables:
            PM_PRIVATE_KEY: Wallet private key (required)
            PM_PROXY_ADDRESS: Proxy/funder address
            PM_API_KEY: API key
            PM_API_SECRET: API secret
            PM_API_PASSPHRASE: API passphrase
        """
        return cls(
            private_key=os.getenv("PM_PRIVATE_KEY", ""),
            proxy_address=os.getenv("PM_PROXY_ADDRESS", ""),
            api_key=os.getenv("PM_API_KEY", ""),
            api_secret=os.getenv("PM_API_SECRET", ""),
            api_passphrase=os.getenv("PM_API_PASSPHRASE", ""),
        )

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If required fields are missing.
        """
        if not self.private_key:
            raise ValueError("private_key is required")


@dataclass
class PredictFunConfig:
    """Configuration for Predict.fun client.

    Attributes:
        api_key: API key for REST API
        private_key: Privy wallet private key (signer)
        smart_wallet: Smart Wallet address (maker)
    """

    api_key: str
    private_key: str
    smart_wallet: str

    @classmethod
    def from_env(cls) -> "PredictFunConfig":
        """Load configuration from environment variables.

        Environment variables:
            PREDICT_FUN_API_KEY: API key (required)
            PREDICT_FUN_PRIVATE_KEY: Privy wallet private key (required)
            PREDICT_FUN_SMART_WALLET: Smart Wallet address (required)
        """
        return cls(
            api_key=os.getenv("PREDICT_FUN_API_KEY", ""),
            private_key=os.getenv("PREDICT_FUN_PRIVATE_KEY", ""),
            smart_wallet=os.getenv("PREDICT_FUN_SMART_WALLET", ""),
        )

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If required fields are missing.
        """
        missing = []
        if not self.api_key:
            missing.append("api_key")
        if not self.private_key:
            missing.append("private_key")
        if not self.smart_wallet:
            missing.append("smart_wallet")

        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
