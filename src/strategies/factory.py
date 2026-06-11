from src.core.enums import AggregationMode
from src.strategies.composite import CompositeStrategy
from src.strategies.ema_ribbon import EMARibbonStrategy
from src.strategies.interface import IStrategy
from src.strategies.ping_pong import PingPongStrategy
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.sma_crossover import SMACrossoverStrategy
from src.strategies.wavetrend import WaveTrendStrategy
from src.strategies.webhook import WebhookSignalStrategy


# Registry of built-in strategy types
_STRATEGY_REGISTRY: dict[str, type[IStrategy]] = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi": RSIStrategy,
    "ping_pong": PingPongStrategy,
    "wavetrend": WaveTrendStrategy,
    "ema_ribbon": EMARibbonStrategy,
    "webhook": WebhookSignalStrategy,
}


class StrategyFactory:
    """Creates strategy instances from YAML configuration."""

    def __init__(self) -> None:
        self._registry: dict[str, type[IStrategy]] = dict(_STRATEGY_REGISTRY)
        self._instances: dict[str, IStrategy] = {}

    def register(self, type_name: str, cls: type[IStrategy]) -> None:
        self._registry[type_name] = cls

    def create_from_config(self, config: list[dict]) -> list[IStrategy]:
        """Create strategies from a list of strategy config dicts.

        First pass: create all non-composite strategies.
        Second pass: create composite strategies that reference children.
        """
        strategies: list[IStrategy] = []
        composites: list[dict] = []

        for entry in config:
            if entry.get("type") == "composite":
                composites.append(entry)
            else:
                strategy = self._create_single(entry)
                if strategy.name in self._instances:
                    raise ValueError(
                        f"Duplicate strategy name '{strategy.name}' in config"
                    )
                self._instances[strategy.name] = strategy
                strategies.append(strategy)

        for entry in composites:
            strategy = self._create_composite(entry)
            if strategy.name in self._instances:
                raise ValueError(
                    f"Duplicate strategy name '{strategy.name}' in config"
                )
            self._instances[strategy.name] = strategy
            strategies.append(strategy)

        return strategies

    def _create_single(self, entry: dict) -> IStrategy:
        type_name = entry["type"]
        cls = self._registry.get(type_name)
        if cls is None:
            raise ValueError(f"Unknown strategy type: {type_name}")

        strategy = cls(name=entry.get("name", type_name))
        if "params" in entry:
            strategy.configure(entry["params"])
        return strategy

    def _create_composite(self, entry: dict) -> CompositeStrategy:
        mode = AggregationMode(entry.get("mode", "weighted"))
        composite = CompositeStrategy(
            name=entry.get("name", "composite"), mode=mode
        )

        for child_ref in entry.get("children", []):
            child_name = child_ref["strategy"]
            weight = child_ref.get("weight", 1.0)
            child = self._instances.get(child_name)
            if child is None:
                raise ValueError(
                    f"Composite references unknown strategy: {child_name}"
                )
            composite.add_strategy(child, weight)

        return composite

    def get_instance(self, name: str) -> IStrategy | None:
        return self._instances.get(name)
