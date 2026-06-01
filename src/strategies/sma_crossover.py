from src.core.enums import SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy


def _sma(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period:
        return None
    return sum(c.close for c in candles[-period:]) / period


class SMACrossoverStrategy(IStrategy):
    """Simple Moving Average crossover strategy.

    Goes LONG when fast SMA crosses above slow SMA.
    Goes SHORT when fast SMA crosses below slow SMA.
    """

    def __init__(
        self,
        name: str = "sma_crossover",
        fast_period: int = 10,
        slow_period: int = 30,
    ) -> None:
        self._name = name
        self._fast_period = fast_period
        self._slow_period = slow_period

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._fast_period = params.get("fast_period", self._fast_period)
        self._slow_period = params.get("slow_period", self._slow_period)

    def analyze(self, candles: list[Candle]) -> Signal:
        if len(candles) < self._slow_period + 1:
            return self._make_signal(SignalType.HOLD)

        fast = _sma(candles, self._fast_period)
        slow = _sma(candles, self._slow_period)
        prev_fast = _sma(candles[:-1], self._fast_period)
        prev_slow = _sma(candles[:-1], self._slow_period)

        if None in (fast, slow, prev_fast, prev_slow):
            return self._make_signal(SignalType.HOLD)

        meta = {"fast_sma": fast, "slow_sma": slow}

        # Bullish crossover: fast crosses above slow
        if prev_fast <= prev_slow and fast > slow:
            spread = (fast - slow) / slow if slow else 0
            return self._make_signal(SignalType.LONG, min(abs(spread) * 100, 1.0), meta)

        # Bearish crossover: fast crosses below slow
        if prev_fast >= prev_slow and fast < slow:
            spread = (slow - fast) / slow if slow else 0
            return self._make_signal(SignalType.SHORT, min(abs(spread) * 100, 1.0), meta)

        return self._make_signal(SignalType.HOLD)
