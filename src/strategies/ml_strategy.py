from abc import ABC, abstractmethod

import numpy as np

from src.core.enums import SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy


class IMLModel(ABC):
    """Abstract interface for ML models used in trading strategies."""

    @abstractmethod
    def predict(self, features: np.ndarray) -> float:
        """Return a prediction score. Positive = bullish, negative = bearish."""
        ...

    @abstractmethod
    def extract_features(self, candles: list[Candle]) -> np.ndarray:
        """Extract feature vector from candle data."""
        ...


class MLStrategy(IStrategy):
    """Wraps any ML model behind the standard strategy interface.

    The model must implement IMLModel. This adapter:
    1. Extracts features from candles using the model
    2. Gets a prediction score
    3. Maps the score to a trading Signal
    """

    def __init__(
        self,
        name: str,
        model: IMLModel,
        long_threshold: float = 0.5,
        short_threshold: float = -0.5,
    ) -> None:
        self._name = name
        self._model = model
        self._long_threshold = long_threshold
        self._short_threshold = short_threshold

    @property
    def name(self) -> str:
        return self._name

    def configure(self, params: dict) -> None:
        self._long_threshold = params.get("long_threshold", self._long_threshold)
        self._short_threshold = params.get("short_threshold", self._short_threshold)

    def analyze(self, candles: list[Candle]) -> Signal:
        if len(candles) < 2:
            return self._make_signal(SignalType.HOLD)

        features = self._model.extract_features(candles)
        prediction = self._model.predict(features)
        meta = {"prediction": float(prediction)}

        if prediction >= self._long_threshold:
            strength = min(
                (prediction - self._long_threshold)
                / (1.0 - self._long_threshold + 1e-9),
                1.0,
            )
            return self._make_signal(SignalType.LONG, abs(strength), meta)

        if prediction <= self._short_threshold:
            strength = min(
                (self._short_threshold - prediction)
                / (1.0 + self._short_threshold + 1e-9),
                1.0,
            )
            return self._make_signal(SignalType.SHORT, abs(strength), meta)

        return self._make_signal(SignalType.HOLD, metadata=meta)
