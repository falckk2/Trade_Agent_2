from abc import ABC, abstractmethod

from src.core.models import PortfolioSnapshot, Position, Signal


class IRiskManager(ABC):
    """Abstract interface for risk management."""

    @abstractmethod
    def validate_signal(
        self, signal: Signal, portfolio: PortfolioSnapshot
    ) -> bool:
        ...

    @abstractmethod
    def calculate_position_size(
        self, signal: Signal, portfolio: PortfolioSnapshot, current_price: float
    ) -> float:
        ...

    @abstractmethod
    def get_stop_loss(self, signal: Signal, entry_price: float) -> float:
        ...

    @abstractmethod
    def get_take_profit(self, signal: Signal, entry_price: float) -> float:
        ...

    @abstractmethod
    def should_stop_out(self, position: Position) -> bool:
        """Return True if the position has breached its stop-loss level."""
        ...

    @abstractmethod
    def should_take_profit(self, position: Position) -> bool:
        """Return True if the position has reached its take-profit level."""
        ...

    @abstractmethod
    def set_initial_equity(self, equity: float) -> None:
        """Set the baseline equity for drawdown calculations.

        Should be called once at startup with the opening balance.
        """
        ...
