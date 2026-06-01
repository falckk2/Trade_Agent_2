import logging

from src.core.enums import Side, SignalType
from src.core.models import PortfolioSnapshot, Position, Signal
from src.risk.interface import IRiskManager

logger = logging.getLogger(__name__)


class RiskManager(IRiskManager):
    """Concrete risk manager with configurable limits."""

    def __init__(
        self,
        max_position_pct: float = 0.05,
        max_exposure_pct: float = 0.20,
        max_drawdown_pct: float = 0.10,
        default_stop_loss_pct: float = 0.02,
        default_take_profit_pct: float = 0.04,
        min_signal_strength: float = 0.3,
    ) -> None:
        self._max_position_pct = max_position_pct
        self._max_exposure_pct = max_exposure_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._default_stop_loss_pct = default_stop_loss_pct
        self._default_take_profit_pct = default_take_profit_pct
        self._min_signal_strength = min_signal_strength
        self._initial_equity: float | None = None  # Set on first portfolio update

    def set_initial_equity(self, equity: float) -> None:
        """Explicitly set the baseline equity for drawdown calculation.

        Call this once at startup with the opening balance so the drawdown
        window starts from the correct value rather than the first signal.
        """
        if self._initial_equity is None:
            self._initial_equity = equity

    def validate_signal(
        self, signal: Signal, portfolio: PortfolioSnapshot
    ) -> bool:
        if signal.signal_type == SignalType.HOLD:
            return False

        if signal.signal_type == SignalType.CLOSE:
            return True

        # Reject weak signals
        if signal.strength < self._min_signal_strength:
            logger.debug(
                "Signal rejected — strength %.2f below minimum %.2f",
                signal.strength,
                self._min_signal_strength,
            )
            return False

        # Track initial equity for drawdown check
        if self._initial_equity is None:
            self._initial_equity = portfolio.total_equity

        # Check max drawdown
        if self._initial_equity > 0:
            drawdown = (
                self._initial_equity - portfolio.total_equity
            ) / self._initial_equity
            if drawdown >= self._max_drawdown_pct:
                logger.warning(
                    "Max drawdown reached (%.2f%%), rejecting signal",
                    drawdown * 100,
                )
                return False

        # Check total exposure including the projected new position
        total_exposure = sum(
            abs(p.quantity * p.current_price) for p in portfolio.positions
        )
        if portfolio.total_equity > 0:
            # Estimate the value of the proposed order so we can reject signals
            # that would push total exposure past the limit in a single step.
            projected_order_value = (
                portfolio.total_equity
                * self._max_position_pct
                * signal.strength
            )
            new_exposure_ratio = (
                total_exposure + projected_order_value
            ) / portfolio.total_equity
            if new_exposure_ratio > self._max_exposure_pct:
                logger.warning(
                    "Order would exceed max exposure (projected %.2f%% > limit %.2f%%), "
                    "rejecting signal",
                    new_exposure_ratio * 100,
                    self._max_exposure_pct * 100,
                )
                return False

        return True

    def calculate_position_size(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_price: float,
    ) -> float:
        if current_price <= 0 or portfolio.total_equity <= 0:
            return 0.0

        max_value = portfolio.total_equity * self._max_position_pct
        # Scale by signal strength
        position_value = max_value * signal.strength
        quantity = position_value / current_price
        return quantity

    def should_stop_out(self, position: Position) -> bool:
        if position.side == Side.BUY:
            stop_price = position.entry_price * (1 - self._default_stop_loss_pct)
            return position.current_price <= stop_price
        else:
            stop_price = position.entry_price * (1 + self._default_stop_loss_pct)
            return position.current_price >= stop_price

    def should_take_profit(self, position: Position) -> bool:
        if position.side == Side.BUY:
            tp_price = position.entry_price * (1 + self._default_take_profit_pct)
            return position.current_price >= tp_price
        else:
            tp_price = position.entry_price * (1 - self._default_take_profit_pct)
            return position.current_price <= tp_price

    def get_stop_loss(self, signal: Signal, entry_price: float) -> float:
        if signal.signal_type == SignalType.LONG:
            return entry_price * (1 - self._default_stop_loss_pct)
        elif signal.signal_type == SignalType.SHORT:
            return entry_price * (1 + self._default_stop_loss_pct)
        return entry_price

    def get_take_profit(self, signal: Signal, entry_price: float) -> float:
        if signal.signal_type == SignalType.LONG:
            return entry_price * (1 + self._default_take_profit_pct)
        elif signal.signal_type == SignalType.SHORT:
            return entry_price * (1 - self._default_take_profit_pct)
        return entry_price
