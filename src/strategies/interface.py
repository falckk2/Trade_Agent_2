from abc import ABC, abstractmethod
from typing import Any

from src.core.enums import SignalType
from src.core.models import Candle, Signal, utcnow


class IStrategy(ABC):
    """Abstract interface for trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def analyze(self, candles: list[Candle]) -> Signal:
        ...

    @abstractmethod
    def configure(self, params: dict) -> None:
        ...

    def _make_signal(
        self,
        signal_type: SignalType,
        strength: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Signal:
        """Create a signal attributed to this strategy. Shared by all subclasses.

        Always copies ``metadata`` so that the caller's dict cannot be mutated
        through the frozen Signal, and a shared dict cannot bleed across
        multiple Signal instances.
        """
        return Signal(
            signal_type=signal_type,
            symbol="",
            strength=strength,
            strategy_name=self.name,
            timestamp=utcnow(),
            metadata=dict(metadata) if metadata else {},
        )


class IStrategyAggregatorFactory(ABC):
    """Builds a composite strategy from a weighted list of sub-strategies.

    Injected into TradingEngine so the engine never depends on a concrete
    aggregator implementation.
    """

    @abstractmethod
    def build(
        self, strategies: list[tuple[IStrategy, float]]
    ) -> IStrategy:
        """Return a single IStrategy that aggregates the provided weighted strategies."""
        ...
