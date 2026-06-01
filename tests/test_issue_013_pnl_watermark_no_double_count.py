"""
ISSUE-013: PortfolioManager._record_trade uses _last_recorded_realized_pnl watermark
to compute incremental realized PnL, preventing double-counting across position flips.

Tests verify:
1. First trade for a position records full realized_pnl
2. After a flip, only the delta (incremental) realized_pnl is recorded
3. Watermark is cleaned up when a position fully closes (ID disappears)
4. Two sequential flips accumulate correctly
"""

import pytest
from datetime import datetime, timezone

from src.core.enums import Side, PositionStatus
from src.core.models import Position
from src.portfolio.manager import PortfolioManager


def _pos(pos_id: str, side: Side, realized_pnl: float, unrealized_pnl: float = 0.0) -> Position:
    return Position(
        id=pos_id,
        symbol="BTC-USDT",
        side=side,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        status=PositionStatus.OPEN,
        strategy_name="test_strat",
    )


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestPnLWatermarkNoDoubleCount:
    def test_first_trade_records_full_realized_pnl(self, pm):
        """When a position closes for the first time, all realized_pnl is recorded."""
        pos = _pos("p1", Side.BUY, realized_pnl=100.0, unrealized_pnl=50.0)
        pm.update([pos], {"total_equity": 10000.0, "available": 9900.0})

        # Position disappears (closed)
        pm.update([], {"total_equity": 10150.0, "available": 10150.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        # pnl = (100.0 - 0.0) + 50.0 = 150.0
        assert trades[0].pnl == pytest.approx(150.0)

    def test_flip_records_only_incremental_realized_pnl(self, pm):
        """
        Simulate two updates:
        1. BUY position with realized_pnl=100 and unrealized=0
        2. Same ID but SELL (flip): cumulative realized_pnl=150

        When the flip is detected, _record_trade is called on the PREVIOUS (BUY) position.
        The BUY had realized_pnl=100, watermark was 0 → incremental = 100.

        Then after the SELL is recorded as prev, update 3 closes the SELL (realized=150).
        Incremental = 150 - 100 = 50.

        This verifies the watermark correctly tracks the cumulative amount already recorded.
        """
        # First update: BUY with realized_pnl=100
        pos_buy = _pos("p1", Side.BUY, realized_pnl=100.0, unrealized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})
        assert len(pm.get_trade_history()) == 0  # no trade yet (not closed)

        # Second update: same ID, SELL side (flipped) with higher cumulative realized_pnl=150
        pos_sell = _pos("p1", Side.SELL, realized_pnl=150.0, unrealized_pnl=0.0)
        pm.update([pos_sell], {"total_equity": 10100.0, "available": 9000.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1  # flip of BUY recorded
        # Records the BUY position: incremental = 100 - 0 = 100 (watermark was 0)
        assert trades[0].pnl == pytest.approx(100.0)
        # Watermark should now be 100 (the BUY's realized_pnl)
        assert pm._last_recorded_realized_pnl.get("p1") == pytest.approx(100.0)

        # Third update: SELL position closes
        pm.update([], {"total_equity": 10150.0, "available": 10150.0})
        trades = pm.get_trade_history()
        assert len(trades) == 2
        # SELL had realized=150, watermark was 100 → incremental = 50
        assert trades[1].pnl == pytest.approx(50.0)

    def test_watermark_cleaned_up_after_position_closes(self, pm):
        """After a position fully disappears, its watermark entry is removed."""
        pos = _pos("p1", Side.BUY, realized_pnl=200.0)
        pm.update([pos], {"total_equity": 10000.0, "available": 9000.0})
        pm.update([], {"total_equity": 10200.0, "available": 10200.0})

        # Watermark should be gone
        assert "p1" not in pm._last_recorded_realized_pnl

    def test_double_flip_does_not_double_count(self, pm):
        """
        Three updates simulate two flips:
        Update 1: BUY, realized=0 (no trade yet)
        Update 2: SELL, realized=100 (flip! BUY→SELL recorded: incremental=0-0=0)
        Update 3: BUY again, realized=180 (flip! SELL→BUY recorded: incremental=100-0=100)

        The watermark (set in step 2 to 0, since BUY had realized=0) means step 3 records
        incremental = 100 - 0 = 100 for the SELL position.

        Without the watermark (original bug), step 3 would use raw 100, but that same 100
        was already recorded in step 2 — total would be 0 + 100 = 100 anyway in this case.

        Key test: full close of the final BUY (realized=180) should only record 80 more
        (180 - 100) because 100 was already recorded for the SELL flip.
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9000.0})

        pos_sell = _pos("p1", Side.SELL, realized_pnl=100.0)
        pm.update([pos_sell], {"total_equity": 10100.0, "available": 9000.0})

        pos_buy2 = _pos("p1", Side.BUY, realized_pnl=180.0)
        pm.update([pos_buy2], {"total_equity": 10180.0, "available": 9000.0})

        # Final close of BUY with realized=180
        pm.update([], {"total_equity": 10180.0, "available": 10180.0})

        trades = pm.get_trade_history()
        total_pnl = sum(t.pnl for t in trades)
        # Flip1: BUY(realized=0) → watermark=0, incremental=0
        # Flip2: SELL(realized=100) → watermark=0, incremental=100
        # Close: BUY(realized=180) → watermark=100, incremental=80
        # Total = 0 + 100 + 80 = 180 (not 0 + 100 + 180 = 280)
        assert total_pnl == pytest.approx(180.0)
        assert len(trades) == 3

    def test_watermark_tracks_per_position_id(self, pm):
        """Watermarks are per position ID, not shared across different positions."""
        pos_a = _pos("p_a", Side.BUY, realized_pnl=50.0)
        pos_b = _pos("p_b", Side.SELL, realized_pnl=30.0)
        pm.update([pos_a, pos_b], {"total_equity": 10000.0, "available": 9000.0})

        # Both close
        pm.update([], {"total_equity": 10080.0, "available": 10080.0})

        trades = pm.get_trade_history()
        pnls = sorted(t.pnl for t in trades)
        assert pnls == pytest.approx(sorted([50.0, 30.0]))
