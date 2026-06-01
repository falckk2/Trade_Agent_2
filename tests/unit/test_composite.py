"""Tests for CompositeStrategy."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.core.enums import AggregationMode, SignalType
from src.core.models import Candle, Signal
from src.strategies.composite import CompositeStrategy
from src.strategies.interface import IStrategy


def _make_mock_strategy(name: str, signal_type: SignalType, strength: float):
    strategy = MagicMock(spec=IStrategy)
    strategy.name = name
    strategy.analyze.return_value = Signal(
        signal_type=signal_type,
        symbol="",
        strength=strength,
        strategy_name=name,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return strategy


def _sample_candles():
    return [
        Candle(
            timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0,
        )
        for i in range(10)
    ]


class TestCompositeUnanimous:
    def test_all_agree_long(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.UNANIMOUS)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.8), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.LONG, 0.6), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.LONG
        assert signal.strength > 0

    def test_disagreement_returns_hold(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.UNANIMOUS)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.8), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.SHORT, 0.6), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD

    def test_all_hold_returns_hold(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.UNANIMOUS)
        comp.add_strategy(_make_mock_strategy("a", SignalType.HOLD, 0.0), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.HOLD, 0.0), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD


class TestCompositeMajority:
    def test_majority_long(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.MAJORITY)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.7), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.LONG, 0.5), 1.0)
        comp.add_strategy(_make_mock_strategy("c", SignalType.SHORT, 0.6), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.LONG

    def test_no_majority_returns_hold(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.MAJORITY)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.7), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.SHORT, 0.5), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD


class TestCompositeWeighted:
    def test_weighted_favors_higher_weight(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.WEIGHTED)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.8), 3.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.SHORT, 0.6), 1.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.LONG

    def test_weighted_short_wins(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.WEIGHTED)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.3), 1.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.SHORT, 0.9), 2.0)

        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.SHORT


class TestZeroWeightGuard:
    def test_unanimous_zero_weights_returns_hold(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.UNANIMOUS)
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.8), 0.0)
        comp.add_strategy(_make_mock_strategy("b", SignalType.LONG, 0.6), 0.0)
        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD


class TestCompositeGeneral:
    def test_empty_composite_returns_hold(self):
        comp = CompositeStrategy(name="test")
        signal = comp.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD

    def test_add_and_remove_strategy(self):
        comp = CompositeStrategy(name="test")
        s1 = _make_mock_strategy("a", SignalType.LONG, 0.5)
        comp.add_strategy(s1, 1.0)
        assert len(comp._children) == 1

        comp.remove_strategy("a")
        assert len(comp._children) == 0

    def test_configure_mode(self):
        comp = CompositeStrategy(name="test", mode=AggregationMode.UNANIMOUS)
        comp.configure({"mode": "weighted"})
        assert comp._mode == AggregationMode.WEIGHTED

    def test_name(self):
        comp = CompositeStrategy(name="my_composite")
        assert comp.name == "my_composite"

    def test_signal_metadata_includes_children(self):
        comp = CompositeStrategy(name="test")
        comp.add_strategy(_make_mock_strategy("a", SignalType.LONG, 0.8), 1.0)
        signal = comp.analyze(_sample_candles())
        assert "children" in signal.metadata
        assert "a" in signal.metadata["children"]
