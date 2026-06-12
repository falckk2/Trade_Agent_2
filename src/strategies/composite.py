from src.core.enums import AggregationMode, SignalType
from src.core.models import Candle, Signal, utcnow
from src.strategies.interface import IStrategy, IStrategyAggregatorFactory


class CompositeStrategy(IStrategy):
    """Combines multiple strategies using configurable aggregation.

    Supports three modes:
    - UNANIMOUS: All strategies must agree on the direction.
    - MAJORITY: More than half must agree.
    - WEIGHTED: Weighted sum of signal strengths determines direction.

    Implements IStrategy so composites can be nested.
    """

    def __init__(
        self,
        name: str = "composite",
        mode: AggregationMode = AggregationMode.WEIGHTED,
    ) -> None:
        self._name = name
        self._mode = mode
        self._children: list[tuple[IStrategy, float]] = []
        self._last_child_signals: dict[str, Signal] = {}

    @property
    def name(self) -> str:
        return self._name

    def add_strategy(self, strategy: IStrategy, weight: float = 1.0) -> None:
        self._children.append((strategy, weight))

    def remove_strategy(self, strategy_name: str) -> None:
        self._children = [
            (s, w) for s, w in self._children if s.name != strategy_name
        ]

    def configure(self, params: dict) -> None:
        mode_str = params.get("mode", self._mode.value)
        self._mode = AggregationMode(mode_str)

    @property
    def last_child_signals(self) -> dict[str, Signal]:
        """Last signal produced by each child strategy, keyed by strategy name."""
        return dict(self._last_child_signals)

    def analyze(self, candles: list[Candle]) -> Signal:
        if not self._children:
            return self._make_signal(SignalType.HOLD, 0.0)

        signals = [
            (strategy.analyze(candles), weight)
            for strategy, weight in self._children
        ]
        self._last_child_signals = {
            strategy.name: sig for (strategy, _), (sig, _) in zip(self._children, signals)
        }

        if self._mode == AggregationMode.UNANIMOUS:
            result = self._aggregate_unanimous(signals)
        elif self._mode == AggregationMode.MAJORITY:
            result = self._aggregate_majority(signals)
        else:
            result = self._aggregate_weighted(signals)

        # Preserve contributing child signals in the aggregate's metadata so
        # downstream consumers (signal log, per-condition performance reports)
        # can attribute the trade to the originating signal — e.g. a webhook
        # alert's `condition` would otherwise be lost in aggregation.
        if result.signal_type != SignalType.HOLD:
            contributing = {
                strategy.name: {
                    "signal_type": sig.signal_type.value,
                    "strength": sig.strength,
                    "metadata": dict(sig.metadata),
                }
                for (strategy, _), (sig, _) in zip(self._children, signals)
                if sig.signal_type != SignalType.HOLD
            }
            if contributing:
                result.metadata["children_signals"] = contributing
        return result

    def _aggregate_unanimous(
        self, signals: list[tuple[Signal, float]]
    ) -> Signal:
        # Filter out HOLD signals
        active = [
            (s, w) for s, w in signals if s.signal_type != SignalType.HOLD
        ]
        if not active:
            return self._make_signal(SignalType.HOLD, 0.0)

        # Check all agree
        first_type = active[0][0].signal_type
        if all(s.signal_type == first_type for s, _ in active):
            total_weight = sum(w for _, w in active)
            if total_weight == 0:
                return self._make_signal(SignalType.HOLD, 0.0)
            avg_strength = sum(s.strength * w for s, w in active) / total_weight
            return self._make_signal(first_type, avg_strength)

        return self._make_signal(SignalType.HOLD, 0.0)

    def _aggregate_majority(
        self, signals: list[tuple[Signal, float]]
    ) -> Signal:
        active = [
            (s, w) for s, w in signals if s.signal_type != SignalType.HOLD
        ]
        if not active:
            return self._make_signal(SignalType.HOLD, 0.0)

        vote_count: dict[SignalType, int] = {}
        vote_strength: dict[SignalType, float] = {}
        for s, w in active:
            vote_count[s.signal_type] = vote_count.get(s.signal_type, 0) + 1
            vote_strength[s.signal_type] = (
                vote_strength.get(s.signal_type, 0.0) + s.strength * w
            )

        total = len(active)
        for sig_type, count in vote_count.items():
            if count > total / 2:
                avg_str = vote_strength[sig_type] / count
                return self._make_signal(sig_type, avg_str)

        return self._make_signal(SignalType.HOLD, 0.0)

    def _aggregate_weighted(
        self, signals: list[tuple[Signal, float]]
    ) -> Signal:
        long_score = 0.0
        short_score = 0.0
        close_score = 0.0
        total_weight = sum(w for _, w in signals)

        if total_weight == 0:
            return self._make_signal(SignalType.HOLD, 0.0)

        for signal, weight in signals:
            if signal.signal_type == SignalType.LONG:
                long_score += signal.strength * weight
            elif signal.signal_type == SignalType.SHORT:
                short_score += signal.strength * weight
            elif signal.signal_type == SignalType.CLOSE:
                close_score += signal.strength * weight

        long_score /= total_weight
        short_score /= total_weight
        close_score /= total_weight

        if close_score > long_score and close_score > short_score:
            return self._make_signal(SignalType.CLOSE, close_score)

        if long_score > short_score and long_score > 0:
            return self._make_signal(SignalType.LONG, long_score)

        if short_score > long_score and short_score > 0:
            return self._make_signal(SignalType.SHORT, short_score)

        return self._make_signal(SignalType.HOLD, 0.0)

    def _make_signal(self, signal_type: SignalType, strength: float) -> Signal:
        return Signal(
            signal_type=signal_type,
            symbol="",
            strength=min(max(strength, 0.0), 1.0),
            strategy_name=self._name,
            timestamp=utcnow(),
            metadata={
                "mode": self._mode.value,
                "children": [s.name for s, _ in self._children],
            },
        )


class WeightedAggregatorFactory(IStrategyAggregatorFactory):
    """Concrete factory that builds a WEIGHTED CompositeStrategy."""

    def build(self, strategies: list[tuple[IStrategy, float]]) -> IStrategy:
        names = [s.name for s, _ in strategies]
        composite = CompositeStrategy(
            name="composite[" + ",".join(names) + "]",
            mode=AggregationMode.WEIGHTED,
        )
        for strategy, weight in strategies:
            composite.add_strategy(strategy, weight)
        return composite
