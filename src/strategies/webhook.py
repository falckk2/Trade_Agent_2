"""Webhook-fed strategy (FABLE-017).

Bridges external alert sources — primarily TradingView/MarketCipher alert
webhooks — into the engine's normal strategy pipeline. The HTTP receiver
(`src.webhook.server`) calls `inject()` when an authenticated alert arrives;
the engine consumes it on its next tick through the standard `analyze()`
interface, so risk validation, position sizing, TP/SL attachment, and
portfolio recording all apply unchanged.

Design constraints:
- Strategies are symbol-agnostic (the engine attaches the symbol), so each
  webhook strategy instance must be configured for exactly ONE symbol in
  strategies.yaml. The receiver enforces that the alert's symbol matches.
- Signals are consume-once and expire after `max_age_seconds` so a missed
  tick or a long outage cannot trade stale information.
- Webhook signals cannot be backtested — treat as live-only and size
  conservatively (weight in strategies.yaml).
"""

import logging
import threading
from typing import Any

from src.core.enums import SignalType
from src.core.models import Candle, Signal, utcnow
from src.strategies.interface import IStrategy

logger = logging.getLogger(__name__)

_ACTION_MAP = {
    "long": SignalType.LONG,
    "buy": SignalType.LONG,
    "short": SignalType.SHORT,
    "sell": SignalType.SHORT,
    "close": SignalType.CLOSE,
    "exit": SignalType.CLOSE,
}


def parse_action(action: str) -> SignalType | None:
    """Map a webhook action string to a SignalType (None if unknown)."""
    return _ACTION_MAP.get(action.strip().lower())


class WebhookSignalStrategy(IStrategy):
    """Holds the most recent externally injected signal; analyze() consumes it.

    Thread-safe: `inject()` may be called from the HTTP receiver while the
    engine tick reads from another context.
    """

    def __init__(self, name: str = "webhook", max_age_seconds: float = 300.0) -> None:
        self._name = name
        self._max_age_seconds = max_age_seconds
        self._lock = threading.Lock()
        # (signal_type, strength, metadata, received_at) or None
        self._pending: tuple[SignalType, float, dict[str, Any], Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._max_age_seconds = float(
            params.get("max_age_seconds", self._max_age_seconds)
        )

    def inject(
        self,
        signal_type: SignalType,
        strength: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store a signal for consumption on the next engine tick.

        A newer alert replaces an unconsumed older one — the latest external
        view wins.
        """
        strength = max(0.0, min(1.0, strength))
        with self._lock:
            if self._pending is not None:
                logger.info(
                    "Webhook strategy '%s': replacing unconsumed %s signal",
                    self._name,
                    self._pending[0].value,
                )
            self._pending = (signal_type, strength, dict(metadata or {}), utcnow())
        logger.info(
            "Webhook strategy '%s': %s signal injected (strength %.2f)",
            self._name,
            signal_type.value,
            strength,
        )

    def analyze(self, candles: list[Candle]) -> Signal:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return self._make_signal(SignalType.HOLD)

        signal_type, strength, metadata, received_at = pending
        age = (utcnow() - received_at).total_seconds()
        if age > self._max_age_seconds:
            logger.warning(
                "Webhook strategy '%s': dropping stale %s signal (%.0fs old, max %s)",
                self._name,
                signal_type.value,
                age,
                self._max_age_seconds,
            )
            return self._make_signal(SignalType.HOLD)

        metadata.setdefault("source", "webhook")
        metadata["signal_age_seconds"] = round(age, 1)
        return self._make_signal(signal_type, strength, metadata)
