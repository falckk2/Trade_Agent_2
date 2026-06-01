"""Tests for MLStrategy wrapper."""

from datetime import datetime, timezone

import numpy as np

from src.core.enums import SignalType
from src.core.models import Candle
from src.strategies.ml_strategy import IMLModel, MLStrategy


class DummyModel(IMLModel):
    """A deterministic model for testing."""

    def __init__(self, prediction: float):
        self._prediction = prediction

    def predict(self, features: np.ndarray) -> float:
        return self._prediction

    def extract_features(self, candles: list[Candle]) -> np.ndarray:
        closes = [c.close for c in candles[-10:]]
        return np.array(closes)


def _sample_candles(n=20):
    return [
        Candle(
            timestamp=datetime(2025, 1, 1, 0, i, tzinfo=timezone.utc),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=100.0,
        )
        for i in range(n)
    ]


class TestMLStrategy:
    def test_long_signal_above_threshold(self):
        model = DummyModel(prediction=0.8)
        strategy = MLStrategy(name="ml_test", model=model, long_threshold=0.5)
        signal = strategy.analyze(_sample_candles())
        assert signal.signal_type == SignalType.LONG
        assert signal.strength > 0

    def test_short_signal_below_threshold(self):
        model = DummyModel(prediction=-0.8)
        strategy = MLStrategy(
            name="ml_test", model=model, short_threshold=-0.5
        )
        signal = strategy.analyze(_sample_candles())
        assert signal.signal_type == SignalType.SHORT
        assert signal.strength > 0

    def test_hold_in_neutral_zone(self):
        model = DummyModel(prediction=0.2)
        strategy = MLStrategy(
            name="ml_test",
            model=model,
            long_threshold=0.5,
            short_threshold=-0.5,
        )
        signal = strategy.analyze(_sample_candles())
        assert signal.signal_type == SignalType.HOLD

    def test_hold_with_insufficient_candles(self):
        model = DummyModel(prediction=0.9)
        strategy = MLStrategy(name="ml_test", model=model)
        signal = strategy.analyze([])
        assert signal.signal_type == SignalType.HOLD

    def test_prediction_in_metadata(self):
        model = DummyModel(prediction=0.7)
        strategy = MLStrategy(name="ml_test", model=model, long_threshold=0.5)
        signal = strategy.analyze(_sample_candles())
        assert "prediction" in signal.metadata
        assert signal.metadata["prediction"] == 0.7

    def test_configure(self):
        model = DummyModel(prediction=0.0)
        strategy = MLStrategy(name="ml_test", model=model)
        strategy.configure({"long_threshold": 0.3, "short_threshold": -0.3})
        assert strategy._long_threshold == 0.3
        assert strategy._short_threshold == -0.3

    def test_name(self):
        model = DummyModel(prediction=0.0)
        strategy = MLStrategy(name="my_ml", model=model)
        assert strategy.name == "my_ml"
