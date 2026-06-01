"""PingPong strategy — alternates LONG/SHORT on a fixed time interval.

For testing only: verifies that the full trade execution pipeline works.
"""

import time

from src.core.enums import SignalType
from src.core.models import Candle, Signal, utcnow
from src.strategies.interface import IStrategy


class PingPongStrategy(IStrategy):
    """Alternates between LONG and SHORT every `interval_seconds`.

    Ignores market data entirely — purely time-driven.
    """

    def __init__(self, name: str = "ping_pong", interval_seconds: float = 10.0) -> None:
        self._name = name
        self._interval = interval_seconds
        self._last_switch = time.monotonic()
        self._current_signal = SignalType.LONG

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._interval = float(params.get("interval_seconds", self._interval))

    def analyze(self, candles: list[Candle]) -> Signal:
        now = time.monotonic()
        if now - self._last_switch >= self._interval:
            self._current_signal = (
                SignalType.SHORT
                if self._current_signal == SignalType.LONG
                else SignalType.LONG
            )
            self._last_switch = now

        return self._make_signal(
            self._current_signal,
            strength=1.0,
            metadata={"interval_seconds": self._interval},
        )
