"""WaveTrend oscillator strategy — the core of MarketCipher B.

MarketCipher B's primary signal is the WaveTrend oscillator (LazyBear's
public formulation): an EMA-smoothed channel index over the typical price.
The classic entry is a wt1/wt2 cross while the oscillator sits in an
overbought/oversold extreme.

    ap  = (high + low + close) / 3        # typical price
    esa = EMA(ap, channel_length)
    d   = EMA(|ap - esa|, channel_length)
    ci  = (ap - esa) / (0.015 * d)
    wt1 = EMA(ci, average_length)
    wt2 = SMA(wt1, 4)

LONG  when wt1 crosses above wt2 with wt2 <= oversold (default -53)
SHORT when wt1 crosses below wt2 with wt2 >= overbought (default +53)
Strength scales with how deep into the extreme zone wt2 sits.
"""

from src.core.enums import SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy


def _ema_series(values: list[float], period: int) -> list[float]:
    """Full EMA series, seeded with the first value (Pine-script style)."""
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _sma_at(values: list[float], period: int, idx: int) -> float:
    start = max(0, idx - period + 1)
    window = values[start: idx + 1]
    return sum(window) / len(window)


def compute_wavetrend(
    candles: list[Candle], channel_length: int, average_length: int
) -> tuple[list[float], list[float]]:
    """Return (wt1, wt2) series for the candle list."""
    ap = [(c.high + c.low + c.close) / 3 for c in candles]
    esa = _ema_series(ap, channel_length)
    d = _ema_series([abs(a - e) for a, e in zip(ap, esa)], channel_length)
    ci = [
        (a - e) / (0.015 * dd) if dd > 0 else 0.0
        for a, e, dd in zip(ap, esa, d)
    ]
    wt1 = _ema_series(ci, average_length)
    wt2 = [_sma_at(wt1, 4, i) for i in range(len(wt1))]
    return wt1, wt2


class WaveTrendStrategy(IStrategy):
    """MarketCipher-B-style WaveTrend cross strategy."""

    def __init__(
        self,
        name: str = "wavetrend",
        channel_length: int = 10,
        average_length: int = 21,
        overbought: float = 53.0,
        oversold: float = -53.0,
    ) -> None:
        self._name = name
        self._channel_length = channel_length
        self._average_length = average_length
        self._overbought = overbought
        self._oversold = oversold

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._channel_length = params.get("channel_length", self._channel_length)
        self._average_length = params.get("average_length", self._average_length)
        self._overbought = params.get("overbought", self._overbought)
        self._oversold = params.get("oversold", self._oversold)

    def analyze(self, candles: list[Candle]) -> Signal:
        # Need enough bars for the EMAs to converge past their seeds
        min_bars = self._channel_length + self._average_length + 10
        if len(candles) < min_bars:
            return self._make_signal(SignalType.HOLD)

        wt1, wt2 = compute_wavetrend(
            candles, self._channel_length, self._average_length
        )
        return self._signal_from_waves(wt1[-2], wt2[-2], wt1[-1], wt2[-1])

    def _signal_from_waves(
        self, prev_wt1: float, prev_wt2: float, wt1: float, wt2: float
    ) -> Signal:
        """Cross + extreme-zone decision, separated for direct testing."""
        meta = {"wt1": wt1, "wt2": wt2}

        crossed_up = prev_wt1 <= prev_wt2 and wt1 > wt2
        crossed_down = prev_wt1 >= prev_wt2 and wt1 < wt2

        if crossed_up and wt2 <= self._oversold:
            depth = (self._oversold - wt2) / abs(self._oversold)
            return self._make_signal(
                SignalType.LONG, min(max(depth, 0.1), 1.0), meta
            )
        if crossed_down and wt2 >= self._overbought:
            depth = (wt2 - self._overbought) / self._overbought
            return self._make_signal(
                SignalType.SHORT, min(max(depth, 0.1), 1.0), meta
            )
        return self._make_signal(SignalType.HOLD, metadata=meta)
