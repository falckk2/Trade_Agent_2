"""EMA ribbon trend strategy — the core idea behind MarketCipher A.

MarketCipher A is built on a ribbon of EMAs; its primary signals come from
fast/slow EMA crosses filtered by the ribbon's overall trend direction.
This implementation uses a three-EMA stack:

LONG  when EMA(fast) crosses above EMA(mid) while EMA(mid) > EMA(slow)
      (fresh momentum in an established uptrend)
SHORT when EMA(fast) crosses below EMA(mid) while EMA(mid) < EMA(slow)
Strength scales with the fast/mid spread, like the SMA crossover strategy.
"""

from src.core.enums import SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy
from src.strategies.wavetrend import _ema_series


class EMARibbonStrategy(IStrategy):
    """Trend-filtered EMA crossover (MarketCipher-A-style)."""

    def __init__(
        self,
        name: str = "ema_ribbon",
        fast_period: int = 8,
        mid_period: int = 21,
        slow_period: int = 55,
    ) -> None:
        self._name = name
        self._fast = fast_period
        self._mid = mid_period
        self._slow = slow_period

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._fast = params.get("fast_period", self._fast)
        self._mid = params.get("mid_period", self._mid)
        self._slow = params.get("slow_period", self._slow)

    def analyze(self, candles: list[Candle]) -> Signal:
        if len(candles) < self._slow + 10:
            return self._make_signal(SignalType.HOLD)

        closes = [c.close for c in candles]
        fast = _ema_series(closes, self._fast)
        mid = _ema_series(closes, self._mid)
        slow = _ema_series(closes, self._slow)

        meta = {"ema_fast": fast[-1], "ema_mid": mid[-1], "ema_slow": slow[-1]}

        crossed_up = fast[-2] <= mid[-2] and fast[-1] > mid[-1]
        crossed_down = fast[-2] >= mid[-2] and fast[-1] < mid[-1]
        uptrend = mid[-1] > slow[-1]
        downtrend = mid[-1] < slow[-1]

        if crossed_up and uptrend:
            spread = (fast[-1] - mid[-1]) / mid[-1] if mid[-1] else 0.0
            return self._make_signal(
                SignalType.LONG, min(abs(spread) * 100, 1.0), meta
            )
        if crossed_down and downtrend:
            spread = (mid[-1] - fast[-1]) / mid[-1] if mid[-1] else 0.0
            return self._make_signal(
                SignalType.SHORT, min(abs(spread) * 100, 1.0), meta
            )
        return self._make_signal(SignalType.HOLD, metadata=meta)
