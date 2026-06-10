import logging
import threading

import requests

from src.notifications.interface import INotifier

logger = logging.getLogger(__name__)

_LEVEL_PREFIX = {"critical": "🚨", "warning": "⚠️"}


class TelegramNotifier(INotifier):
    """Sends alerts to a Telegram chat via the Bot API (FABLE-011).

    No-op when credentials are missing, so it can always be wired in.
    The HTTP POST runs in a daemon thread — notify() never blocks the
    event loop and never raises.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        background: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._background = background
        self._timeout = timeout
        if not self.enabled:
            logger.info(
                "TelegramNotifier disabled — set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID to enable alerts"
            )

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def notify(self, level: str, message: str) -> None:
        if not self.enabled or not message:
            return
        text = f"{_LEVEL_PREFIX.get(level, '')} [{level.upper()}] Trade Agent 2: {message}"
        if self._background:
            threading.Thread(
                target=self._post, args=(text,), daemon=True
            ).start()
        else:
            self._post(text)

    def _post(self, text: str) -> None:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                logger.error(
                    "Telegram alert failed (HTTP %d): %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception:
            logger.exception("Telegram alert failed")
