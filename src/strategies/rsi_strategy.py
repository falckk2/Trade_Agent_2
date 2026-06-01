from src.core.enums import SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy


def _compute_rsi(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period + 1:
        return None

    changes = [candles[i].close - candles[i - 1].close for i in range(1, len(candles))]
    gains = [max(c, 0.0) for c in changes]
    losses = [abs(min(c, 0.0)) for c in changes]

    # Seed with simple average of first `period` values (Wilder's initialisation)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Apply Wilder's smoothing over remaining values
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class RSIStrategy(IStrategy):
    """Relative Strength Index strategy.

    Goes LONG when RSI drops below oversold threshold.
    Goes SHORT when RSI rises above overbought threshold.
    """

    def __init__(
        self,
        name: str = "rsi",
        period: int = 14,
        overbought: float = 70.0,
        oversold: float = 30.0,
    ) -> None:
        self._name = name
        self._period = period
        self._overbought = overbought
        self._oversold = oversold
        self._validate_thresholds(overbought, oversold)

    @staticmethod
    def _validate_thresholds(overbought: float, oversold: float) -> None:
        if not (0 < oversold < overbought < 100):
            raise ValueError(
                f"RSI thresholds must satisfy 0 < oversold ({oversold}) "
                f"< overbought ({overbought}) < 100"
            )

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        overbought = params.get("overbought", self._overbought)
        oversold = params.get("oversold", self._oversold)
        self._validate_thresholds(overbought, oversold)
        self._period = params.get("period", self._period)
        self._overbought = overbought
        self._oversold = oversold

    def analyze(self, candles: list[Candle]) -> Signal:
        rsi = _compute_rsi(candles, self._period)

        if rsi is None:
            return self._make_signal(SignalType.HOLD)

        meta = {"rsi": rsi}

        if rsi <= self._oversold:
            strength = min((self._oversold - rsi) / self._oversold, 1.0)
            return self._make_signal(SignalType.LONG, strength, meta)

        if rsi >= self._overbought:
            strength = min((rsi - self._overbought) / (100 - self._overbought), 1.0)
            return self._make_signal(SignalType.SHORT, strength, meta)

        return self._make_signal(SignalType.HOLD, metadata=meta)
