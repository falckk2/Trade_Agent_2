from abc import ABC, abstractmethod

from src.core.models import PortfolioSnapshot, Position, TradeRecord


class IPortfolioManager(ABC):
    """Abstract interface for portfolio tracking."""

    @abstractmethod
    def update(
        self, positions: list[Position], balance: dict[str, float]
    ) -> None:
        ...

    @abstractmethod
    def get_snapshot(self) -> PortfolioSnapshot:
        ...

    @abstractmethod
    def get_snapshots(self) -> list[PortfolioSnapshot]:
        ...

    @abstractmethod
    def get_strategy_pnl(self, strategy_name: str) -> float:
        ...

    @abstractmethod
    def get_trade_history(
        self, strategy_name: str | None = None
    ) -> list[TradeRecord]:
        ...

    @abstractmethod
    def get_all_strategy_names(self) -> list[str]:
        ...

    @abstractmethod
    def get_performance_stats(self, strategy_name: str | None = None) -> dict:
        """Compute performance statistics from recorded trades.

        With strategy_name=None, stats cover all trades; otherwise only the
        named strategy's trades.
        """
        ...

    @abstractmethod
    def save_trade_history(self) -> None:
        ...
