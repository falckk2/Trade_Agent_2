"""
ISSUE-007: RiskManager.validate_signal() includes the projected order value when
checking max exposure, preventing a single large signal from breaching the limit.

Tests verify:
1. Projected order size is included in the exposure check
2. A signal that would push exposure over limit is rejected
3. A signal within projected limit passes
4. Comparison uses > (strict), so exactly-at-limit passes
5. min_signal_strength is respected from config (ISSUE-009 co-test)
"""

import pytest
from datetime import datetime, timezone

from src.core.enums import PositionStatus, Side, SignalType
from src.core.models import PortfolioSnapshot, Position, Signal
from src.risk.manager import RiskManager


def _signal(strength: float = 0.8, symbol: str = "BTC-USDT") -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol=symbol,
        strength=strength,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _snapshot(
    equity: float = 10000.0,
    positions: list[Position] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=equity,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=positions or [],
    )


class TestProjectedExposureCheck:
    def test_signal_rejected_when_projected_exposure_exceeds_max(self):
        """
        With max_exposure_pct=0.20, max_position_pct=0.05, equity=10000:
        projected_order = 10000 * 0.05 * 1.0 = 500
        existing exposure = 10000 * 0.19 = 1900  (19%)
        new_exposure = (1900 + 500) / 10000 = 0.24 > 0.20 → rejected
        """
        rm = RiskManager(
            max_position_pct=0.05,
            max_exposure_pct=0.20,
            min_signal_strength=0.0,
        )
        # Existing position using 19% of equity at current price
        pos = Position(
            id="p1", symbol="ETH-USDT", side=Side.BUY,
            entry_price=1000.0, current_price=1000.0,
            quantity=1.9,  # 1.9 * 1000 = 1900 = 19% of 10000
        )
        portfolio = _snapshot(equity=10000.0, positions=[pos])

        result = rm.validate_signal(_signal(strength=1.0), portfolio)

        assert result is False

    def test_signal_passes_when_projected_exposure_within_limit(self):
        """
        max_exposure_pct=0.20, max_position_pct=0.05, equity=10000:
        projected_order = 10000 * 0.05 * 0.5 = 250 (strength=0.5)
        existing exposure = 0 (no positions)
        new_exposure = 250 / 10000 = 0.025 < 0.20 → accepted
        """
        rm = RiskManager(
            max_position_pct=0.05,
            max_exposure_pct=0.20,
            min_signal_strength=0.0,
        )
        result = rm.validate_signal(_signal(strength=0.5), _snapshot())
        assert result is True

    def test_exactly_at_limit_is_accepted(self):
        """
        Strict > comparison: projected ratio == max_exposure_pct should pass.
        max_exposure = 0.20, equity = 10000, max_position_pct = 0.05
        projected = 10000 * 0.05 * 1.0 = 500
        new_exposure_ratio = 500 / 10000 = 0.05, which is < 0.20 — accepted.

        To test the boundary exactly: existing = 1500, projected = 500 → ratio = 0.20
        which equals max (0.20 > 0.20 is False) → passes.
        """
        rm = RiskManager(
            max_position_pct=0.05,
            max_exposure_pct=0.20,
            min_signal_strength=0.0,
        )
        pos = Position(
            id="p1", symbol="ETH-USDT", side=Side.BUY,
            entry_price=1000.0, current_price=1000.0,
            quantity=1.5,  # 1.5 * 1000 = 1500 = 15%
        )
        portfolio = _snapshot(equity=10000.0, positions=[pos])
        # projected_order = 10000 * 0.05 * 1.0 = 500 → ratio = (1500+500)/10000 = 0.20
        # 0.20 > 0.20 is False → accepted
        result = rm.validate_signal(_signal(strength=1.0), portfolio)
        assert result is True

    def test_signal_strength_scales_projected_order_value(self):
        """Lower signal strength reduces the projected order and may avoid rejection."""
        rm = RiskManager(
            max_position_pct=0.05,
            max_exposure_pct=0.20,
            min_signal_strength=0.0,
        )
        # Existing exposure = 1900 (19%)
        pos = Position(
            id="p1", symbol="ETH-USDT", side=Side.BUY,
            entry_price=1000.0, current_price=1000.0,
            quantity=1.9,
        )
        portfolio = _snapshot(equity=10000.0, positions=[pos])

        # strength=1.0: projected=500, total=2400, ratio=0.24 → rejected
        assert rm.validate_signal(_signal(strength=1.0), portfolio) is False

        # strength=0.1: projected=50, total=1950, ratio=0.195 → accepted
        assert rm.validate_signal(_signal(strength=0.1), portfolio) is True

    def test_close_signal_bypasses_exposure_check(self):
        """CLOSE signals skip all risk checks — they must always be allowed."""
        rm = RiskManager(
            max_position_pct=0.05,
            max_exposure_pct=0.01,  # tiny limit
            min_signal_strength=0.0,
        )
        close_signal = Signal(
            signal_type=SignalType.CLOSE,
            symbol="BTC-USDT",
            strength=1.0,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
        )
        result = rm.validate_signal(close_signal, _snapshot())
        assert result is True


class TestMinSignalStrength:
    def test_signal_below_min_strength_rejected(self):
        """Signals weaker than min_signal_strength must be rejected."""
        rm = RiskManager(min_signal_strength=0.3)
        result = rm.validate_signal(_signal(strength=0.05), _snapshot())
        assert result is False

    def test_signal_at_min_strength_accepted(self):
        """Signal at exactly min_signal_strength passes (< check, not <=)."""
        rm = RiskManager(min_signal_strength=0.05)
        result = rm.validate_signal(_signal(strength=0.05), _snapshot())
        assert result is True

    def test_config_value_0_05_accepts_weak_sma_signals(self):
        """With min_signal_strength=0.05 (YAML default), SMA signals with strength=0.06 pass."""
        rm = RiskManager(min_signal_strength=0.05)
        result = rm.validate_signal(_signal(strength=0.06), _snapshot())
        assert result is True

    def test_code_default_0_3_rejects_weak_sma_signals(self):
        """The code default (0.3) would reject most SMA crossover signals (strength ~0.05)."""
        rm = RiskManager()  # Default min_signal_strength=0.3
        result = rm.validate_signal(_signal(strength=0.05), _snapshot())
        assert result is False
