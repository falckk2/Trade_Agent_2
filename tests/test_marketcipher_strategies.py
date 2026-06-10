"""
Tests for the MarketCipher-inspired strategies (FABLE-017 groundwork):
WaveTrendStrategy (MarketCipher B core oscillator) and EMARibbonStrategy
(MarketCipher A core ribbon-cross).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core.enums import SignalType
from src.core.models import Candle
from src.strategies.ema_ribbon import EMARibbonStrategy
from src.strategies.factory import StrategyFactory
from src.strategies.wavetrend import WaveTrendStrategy, compute_wavetrend, _ema_series


def _candles(prices, spread=10.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            timestamp=start + timedelta(hours=i),
            open=p, high=p + spread, low=p - spread, close=p,
            volume=100.0,
        )
        for i, p in enumerate(prices)
    ]


class TestEMAHelper:
    def test_constant_series_converges_to_value(self):
        ema = _ema_series([100.0] * 50, 10)
        assert ema[-1] == pytest.approx(100.0)

    def test_ema_lags_step_change(self):
        ema = _ema_series([100.0] * 30 + [200.0] * 5, 10)
        assert 100.0 < ema[-1] < 200.0


class TestWaveTrend:
    def test_hold_with_insufficient_data(self):
        strategy = WaveTrendStrategy()
        signal = strategy.analyze(_candles([100.0] * 20))
        assert signal.signal_type == SignalType.HOLD

    def test_flat_market_is_hold(self):
        strategy = WaveTrendStrategy()
        signal = strategy.analyze(_candles([100.0] * 80))
        assert signal.signal_type == SignalType.HOLD

    def test_oversold_extreme_produces_negative_wt(self):
        # Steep sustained decline pushes the oscillator deep negative
        prices = [1000.0 - 8 * i for i in range(80)]
        wt1, wt2 = compute_wavetrend(_candles(prices), 10, 21)
        assert wt2[-1] < -40

    def test_long_on_upcross_in_oversold_zone(self):
        strategy = WaveTrendStrategy()
        signal = strategy._signal_from_waves(
            prev_wt1=-70.0, prev_wt2=-65.0, wt1=-60.0, wt2=-64.0
        )
        assert signal.signal_type == SignalType.LONG
        assert 0.1 <= signal.strength <= 1.0

    def test_short_on_downcross_in_overbought_zone(self):
        strategy = WaveTrendStrategy()
        signal = strategy._signal_from_waves(
            prev_wt1=70.0, prev_wt2=65.0, wt1=60.0, wt2=64.0
        )
        assert signal.signal_type == SignalType.SHORT

    def test_cross_outside_extreme_zone_is_hold(self):
        strategy = WaveTrendStrategy()
        signal = strategy._signal_from_waves(
            prev_wt1=-10.0, prev_wt2=-5.0, wt1=0.0, wt2=-4.0
        )
        assert signal.signal_type == SignalType.HOLD

    def test_v_reversal_eventually_signals_long(self):
        """Crash then sharp recovery must produce a LONG somewhere on the way up.

        The decline includes a sawtooth wobble — a perfectly linear decline
        keeps wt1 marginally above wt2 forever (no fresh cross to detect),
        which never happens in real price data.
        """
        prices = [1000.0 - 8 * i + (12 if i % 6 < 3 else -12) for i in range(60)] + \
                 [520.0 + 10 * i for i in range(20)]
        candles = _candles(prices)
        strategy = WaveTrendStrategy()
        signals = [
            strategy.analyze(candles[: i + 1]).signal_type
            for i in range(55, len(candles))
        ]
        assert SignalType.LONG in signals


class TestEMARibbon:
    def test_hold_with_insufficient_data(self):
        strategy = EMARibbonStrategy()
        assert strategy.analyze(_candles([100.0] * 30)).signal_type == SignalType.HOLD

    def test_long_on_pullback_recovery_in_uptrend(self):
        # Long uptrend, pullback dips the fast EMA under the mid, recovery
        # crosses it back up while mid stays above slow → LONG
        prices = (
            [100.0 + 2 * i for i in range(80)]      # uptrend
            + [260.0 - 6 * i for i in range(12)]    # sharp pullback
            + [190.0 + 8 * i for i in range(15)]    # recovery
        )
        candles = _candles(prices)
        strategy = EMARibbonStrategy()
        signals = [
            strategy.analyze(candles[: i + 1]).signal_type
            for i in range(80, len(candles))
        ]
        assert SignalType.LONG in signals

    def test_flat_market_is_hold(self):
        strategy = EMARibbonStrategy()
        assert strategy.analyze(_candles([100.0] * 100)).signal_type == SignalType.HOLD


class TestFactoryRegistration:
    def test_both_types_constructible_from_config(self):
        factory = StrategyFactory()
        strategies = factory.create_from_config([
            {"name": "wt1h", "type": "wavetrend",
             "params": {"channel_length": 9, "overbought": 60}},
            {"name": "ribbon", "type": "ema_ribbon",
             "params": {"fast_period": 5, "mid_period": 13, "slow_period": 34}},
        ])
        assert {s.name for s in strategies} == {"wt1h", "ribbon"}
        wt = factory.get_instance("wt1h")
        assert wt._channel_length == 9
        assert wt._overbought == 60
