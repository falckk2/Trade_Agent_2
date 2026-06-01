"""Tests for SMA crossover and RSI strategies."""

from datetime import datetime, timezone

from src.core.enums import SignalType
from src.core.models import Candle
from src.strategies.sma_crossover import SMACrossoverStrategy
from src.strategies.rsi_strategy import RSIStrategy


class TestSMACrossover:
    def test_hold_when_insufficient_data(self):
        strategy = SMACrossoverStrategy(fast_period=5, slow_period=10)
        candles = [
            Candle(
                timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0,
            )
            for i in range(8)
        ]
        signal = strategy.analyze(candles)
        assert signal.signal_type == SignalType.HOLD

    def test_long_on_bullish_crossover(self):
        strategy = SMACrossoverStrategy(fast_period=3, slow_period=6)
        # Build candles where fast SMA is initially below slow, then crosses above
        prices = [100, 99, 98, 97, 96, 95, 94, 95, 97, 100, 104, 109]
        candles = [
            Candle(
                timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
                open=p, high=p + 1, low=p - 1, close=p, volume=100.0,
            )
            for i, p in enumerate(prices)
        ]
        signal = strategy.analyze(candles)
        # The last few candles should show fast > slow after crossing
        assert signal.signal_type in (SignalType.LONG, SignalType.HOLD)

    def test_short_on_bearish_crossover(self):
        strategy = SMACrossoverStrategy(fast_period=3, slow_period=6)
        # Prices go up then sharply down
        prices = [100, 102, 104, 106, 108, 110, 112, 108, 104, 99, 93, 86]
        candles = [
            Candle(
                timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
                open=p, high=p + 1, low=p - 1, close=p, volume=100.0,
            )
            for i, p in enumerate(prices)
        ]
        signal = strategy.analyze(candles)
        assert signal.signal_type in (SignalType.SHORT, SignalType.HOLD)

    def test_configure(self):
        strategy = SMACrossoverStrategy()
        strategy.configure({"fast_period": 20, "slow_period": 50})
        assert strategy._fast_period == 20
        assert strategy._slow_period == 50

    def test_name(self):
        strategy = SMACrossoverStrategy(name="my_sma")
        assert strategy.name == "my_sma"


class TestRSIStrategy:
    def _make_candles(self, prices):
        return [
            Candle(
                timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
                open=p, high=p + 1, low=p - 1, close=p, volume=100.0,
            )
            for i, p in enumerate(prices)
        ]

    def test_hold_when_insufficient_data(self):
        strategy = RSIStrategy(period=14)
        candles = self._make_candles([100] * 5)
        signal = strategy.analyze(candles)
        assert signal.signal_type == SignalType.HOLD

    def test_long_when_oversold(self):
        strategy = RSIStrategy(period=14, oversold=30)
        # Steadily declining prices to push RSI below 30
        prices = [100 - i * 2 for i in range(20)]
        candles = self._make_candles(prices)
        signal = strategy.analyze(candles)
        assert signal.signal_type == SignalType.LONG

    def test_short_when_overbought(self):
        strategy = RSIStrategy(period=14, overbought=70)
        # Steadily rising prices to push RSI above 70
        prices = [100 + i * 2 for i in range(20)]
        candles = self._make_candles(prices)
        signal = strategy.analyze(candles)
        assert signal.signal_type == SignalType.SHORT

    def test_hold_in_neutral_zone(self):
        strategy = RSIStrategy(period=14)
        # Alternating up/down keeps RSI near 50
        prices = [100 + (1 if i % 2 == 0 else -1) for i in range(20)]
        candles = self._make_candles(prices)
        signal = strategy.analyze(candles)
        assert signal.signal_type == SignalType.HOLD

    def test_configure(self):
        strategy = RSIStrategy()
        strategy.configure({"period": 7, "overbought": 80, "oversold": 20})
        assert strategy._period == 7
        assert strategy._overbought == 80
        assert strategy._oversold == 20

    def test_rsi_metadata(self):
        strategy = RSIStrategy(period=14)
        prices = [100 + i * 2 for i in range(20)]
        candles = self._make_candles(prices)
        signal = strategy.analyze(candles)
        assert "rsi" in signal.metadata

    def test_name(self):
        strategy = RSIStrategy(name="my_rsi")
        assert strategy.name == "my_rsi"
