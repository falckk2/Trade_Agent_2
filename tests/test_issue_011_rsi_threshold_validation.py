"""
ISSUE-011: RSIStrategy validates thresholds in __init__ and configure() to prevent
ZeroDivisionError when overbought=100 or oversold=0.

Tests verify:
1. overbought=100 raises ValueError at construction
2. oversold=0 raises ValueError at construction
3. overbought <= oversold raises ValueError
4. Valid thresholds construct successfully
5. configure() validates before assigning (state preserved on failure)
6. Normal usage with valid thresholds (no division by zero)
"""

import pytest
from datetime import datetime, timezone

from src.core.models import Candle
from src.core.enums import SignalType
from src.strategies.rsi_strategy import RSIStrategy


def _make_candles(n: int = 30) -> list[Candle]:
    """Return n candles with alternating prices to produce a non-trivial RSI."""
    candles = []
    price = 50000.0
    for i in range(n):
        price += 100.0 if i % 3 else -50.0
        candles.append(
            Candle(
                timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
                open=price,
                high=price + 50,
                low=price - 30,
                close=price + 20,
                volume=1000.0,
            )
        )
    return candles


class TestRSIThresholdValidation:
    def test_overbought_100_raises_value_error(self):
        """overbought=100 would cause (100 - overbought) = 0 division — must raise."""
        with pytest.raises(ValueError, match="overbought"):
            RSIStrategy(overbought=100.0, oversold=30.0)

    def test_oversold_0_raises_value_error(self):
        """oversold=0 would cause (oversold - rsi) / oversold = /0 — must raise."""
        with pytest.raises(ValueError):
            RSIStrategy(overbought=70.0, oversold=0.0)

    def test_overbought_equal_to_oversold_raises(self):
        """overbought must be strictly greater than oversold."""
        with pytest.raises(ValueError):
            RSIStrategy(overbought=50.0, oversold=50.0)

    def test_overbought_below_oversold_raises(self):
        """overbought < oversold is invalid."""
        with pytest.raises(ValueError):
            RSIStrategy(overbought=30.0, oversold=70.0)

    def test_overbought_above_100_raises(self):
        """overbought > 100 violates RSI range."""
        with pytest.raises(ValueError):
            RSIStrategy(overbought=110.0, oversold=30.0)

    def test_negative_oversold_raises(self):
        """oversold <= 0 must raise."""
        with pytest.raises(ValueError):
            RSIStrategy(overbought=70.0, oversold=-5.0)

    def test_valid_thresholds_construct_successfully(self):
        """Standard thresholds (30/70) must construct without error."""
        strat = RSIStrategy(overbought=70.0, oversold=30.0)
        assert strat is not None

    def test_configure_with_invalid_overbought_preserves_state(self):
        """configure() with overbought=100 raises ValueError and does NOT mutate state."""
        strat = RSIStrategy(overbought=70.0, oversold=30.0)

        with pytest.raises(ValueError):
            strat.configure({"overbought": 100.0, "oversold": 30.0})

        # State preserved
        assert strat._overbought == 70.0
        assert strat._oversold == 30.0

    def test_configure_with_valid_params_updates_state(self):
        """Valid configure() call should update thresholds successfully."""
        strat = RSIStrategy(overbought=70.0, oversold=30.0)
        strat.configure({"overbought": 80.0, "oversold": 20.0})
        assert strat._overbought == 80.0
        assert strat._oversold == 20.0

    def test_analyze_does_not_divide_by_zero(self):
        """With standard thresholds, analyze() must never raise ZeroDivisionError."""
        strat = RSIStrategy(overbought=70.0, oversold=30.0)
        candles = _make_candles(30)
        # Should not raise
        signal = strat.analyze(candles)
        assert signal.signal_type in (SignalType.LONG, SignalType.SHORT, SignalType.HOLD)

    def test_strength_is_bounded_0_to_1(self):
        """Signal strength must always be in [0.0, 1.0]."""
        strat = RSIStrategy(overbought=70.0, oversold=30.0)
        candles = _make_candles(50)
        signal = strat.analyze(candles)
        assert 0.0 <= signal.strength <= 1.0
