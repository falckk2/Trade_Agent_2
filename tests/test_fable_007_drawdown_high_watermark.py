"""
FABLE-007: Drawdown baseline never ratcheted up.

Drawdown was measured from a fixed initial equity persisted once at first
startup. After profits, the halt threshold decayed relative to current
equity (e.g. equity 50k → 100k meant the 10% halt only fired below 45k — a
55% loss of current equity). The fix tracks a trailing high-watermark that
ratchets up in validate_signal and is persisted (key `peak_equity`, legacy
`initial_equity` still accepted as the starting peak).
"""

import json
from datetime import datetime, timezone

import pytest

from src.core.enums import SignalType
from src.core.models import PortfolioSnapshot, Signal
from src.risk.manager import RiskManager


def _signal(signal_type=SignalType.LONG, strength=0.8):
    return Signal(
        signal_type=signal_type,
        symbol="BTC-USDT",
        strength=strength,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _portfolio(equity=50000.0):
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=equity,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=[],
    )


class TestHighWatermark:
    def test_peak_ratchets_up_with_equity(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.set_initial_equity(50000.0)
        # Equity doubles — peak must follow
        assert rm.validate_signal(_signal(), _portfolio(100000.0)) is True
        # 15% below the NEW peak (100k), but well above the old baseline (50k)
        assert rm.validate_signal(_signal(), _portfolio(85000.0)) is False

    def test_drawdown_within_limit_from_peak_accepted(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.set_initial_equity(50000.0)
        rm.validate_signal(_signal(), _portfolio(100000.0))
        # 5% below peak — fine
        assert rm.validate_signal(_signal(), _portfolio(95000.0)) is True

    def test_fixed_baseline_behavior_preserved_without_growth(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.validate_signal(_signal(), _portfolio(50000.0))
        assert rm.validate_signal(_signal(), _portfolio(42000.0)) is False

    def test_set_initial_equity_does_not_lower_existing_peak(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.set_initial_equity(100000.0)
        rm.set_initial_equity(50000.0)  # second call must be a no-op
        assert rm.validate_signal(_signal(), _portfolio(85000.0)) is False


class TestPersistence:
    def test_peak_persisted_and_reloaded(self, tmp_path):
        baseline = tmp_path / "initial_equity.json"
        rm = RiskManager(max_drawdown_pct=0.10, baseline_file=baseline)
        rm.set_initial_equity(50000.0)
        rm.validate_signal(_signal(), _portfolio(100000.0))  # ratchet + save

        data = json.loads(baseline.read_text())
        assert data["peak_equity"] == 100000.0

        rm2 = RiskManager(max_drawdown_pct=0.10, baseline_file=baseline)
        # Peak restored: 15% below 100k is rejected even after restart
        assert rm2.validate_signal(_signal(), _portfolio(85000.0)) is False

    def test_legacy_initial_equity_key_accepted_as_starting_peak(self, tmp_path):
        baseline = tmp_path / "initial_equity.json"
        baseline.write_text(json.dumps({"initial_equity": 80000.0}))

        rm = RiskManager(max_drawdown_pct=0.10, baseline_file=baseline)
        assert rm.validate_signal(_signal(), _portfolio(70000.0)) is False
        assert rm.validate_signal(_signal(), _portfolio(79000.0)) is True
