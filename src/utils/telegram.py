"""Telegram notification utility."""

import os

import httpx

from ..logging import get_logger

logger = get_logger("telegram")


class TelegramNotifier:
    """Send notifications via Telegram Bot API."""

    API_URL = "https://api.telegram.org"

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        """Initialize Telegram notifier.

        Args:
            bot_token: Bot token from @BotFather. Defaults to TG_BOT_TOKEN env var.
            chat_id: Chat/channel ID to send messages. Defaults to TG_CHAT_ID env var.
        """
        self.bot_token = bot_token or os.getenv("TG_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TG_CHAT_ID", "")
        self._http: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        """Check if bot token and chat ID are configured."""
        return bool(self.bot_token and self.chat_id)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10)
        return self._http

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat.

        Args:
            message: Message text (supports HTML formatting)
            parse_mode: "HTML" or "Markdown"

        Returns:
            True if sent successfully
        """
        if not self.is_configured:
            logger.warning("Telegram not configured, skipping notification")
            return False

        try:
            client = await self._get_client()
            url = f"{self.API_URL}/bot{self.bot_token}/sendMessage"
            resp = await client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                },
            )

            if resp.status_code == 200:
                logger.debug("Telegram message sent")
                return True
            else:
                logger.error(f"Telegram API error: {resp.status_code} {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def close(self):
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
