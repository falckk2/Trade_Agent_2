import json
import logging
from pathlib import Path

from src.core.enums import EventType, Side, SignalType
from src.core.events import Event, EventBus
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
        baseline_file: Path | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._max_position_pct = max_position_pct
        self._max_exposure_pct = max_exposure_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._default_stop_loss_pct = default_stop_loss_pct
        self._default_take_profit_pct = default_take_profit_pct
        self._min_signal_strength = min_signal_strength
        self._baseline_file = baseline_file
        self._event_bus = event_bus
        # FABLE-011: alert once per halt onset, not on every rejected signal
        self._drawdown_alerted = False
        # FABLE-007: drawdown is measured from a trailing high-watermark, not
        # a fixed initial equity — otherwise the halt threshold decays as the
        # account grows (10% of the original baseline can be a tiny fraction
        # of current equity after profits).
        self._peak_equity: float | None = self._load_baseline()

    def _load_baseline(self) -> float | None:
        """Load the persisted equity high-watermark from disk, if available."""
        if self._baseline_file is None:
            return None
        try:
            if self._baseline_file.exists():
                data = json.loads(self._baseline_file.read_text())
                # Legacy key from the fixed-baseline era (ISSUE-034) is
                # accepted as the starting peak.
                raw = data.get("peak_equity", data.get("initial_equity"))
                equity = float(raw)
                logger.info(
                    "Drawdown high-watermark loaded from %s: $%.2f",
                    self._baseline_file, equity,
                )
                return equity
        except Exception:
            logger.exception("Failed to load drawdown baseline from %s", self._baseline_file)
        return None

    def _save_baseline(self) -> None:
        """Persist the equity high-watermark so drawdown survives restarts."""
        if self._baseline_file is None or self._peak_equity is None:
            return
        try:
            self._baseline_file.parent.mkdir(parents=True, exist_ok=True)
            self._baseline_file.write_text(
                json.dumps({"peak_equity": self._peak_equity})
            )
        except Exception:
            logger.exception("Failed to save drawdown baseline to %s", self._baseline_file)

    def set_initial_equity(self, equity: float) -> None:
        """Seed the drawdown high-watermark with the opening balance.

        Call this once at startup. Only takes effect if no peak is already
        known (persisted or in-memory); the peak then ratchets up on its own
        as equity grows. Delete the baseline file to reset deliberately.
        External deposits/withdrawals are not detected — after a transfer,
        delete the baseline file so the watermark restarts from the new
        balance.
        """
        if self._peak_equity is None:
            self._peak_equity = equity
            self._save_baseline()
            logger.info("Drawdown high-watermark seeded at $%.2f", equity)

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

        # Track the equity high-watermark for the drawdown check
        if self._peak_equity is None:
            self._peak_equity = portfolio.total_equity
            self._save_baseline()
        elif portfolio.total_equity > self._peak_equity:
            self._peak_equity = portfolio.total_equity
            self._save_baseline()

        # Check max drawdown from the peak
        if self._peak_equity > 0:
            drawdown = (
                self._peak_equity - portfolio.total_equity
            ) / self._peak_equity
            if drawdown >= self._max_drawdown_pct:
                logger.warning(
                    "Max drawdown reached (%.2f%% from peak $%.2f), rejecting signal",
                    drawdown * 100,
                    self._peak_equity,
                )
                if not self._drawdown_alerted:
                    self._drawdown_alerted = True
                    self._alert(
                        "critical",
                        f"Max drawdown halt: equity ${portfolio.total_equity:,.2f} "
                        f"is {drawdown * 100:.1f}% below peak ${self._peak_equity:,.2f}. "
                        f"New signals are rejected; open positions remain live.",
                    )
                return False
            self._drawdown_alerted = False

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

    def _alert(self, level: str, message: str) -> None:
        """Publish an ALERT event (sync context — validate_signal is sync)."""
        if self._event_bus is None:
            return
        self._event_bus.publish_sync(
            Event(event_type=EventType.ALERT, payload={"level": level, "message": message})
        )

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
