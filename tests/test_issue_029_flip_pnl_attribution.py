"""
ISSUE-029: On a BloFin net-mode position flip (BUY→SELL), _record_trade was called
with the stale prev object whose realized_pnl was one tick behind, omitting the
realized PnL produced by the flip itself.

Fix: _record_trade now accepts realized_pnl_override and override_unrealized_pnl.
The flip path passes pos.realized_pnl (current tick) and 0.0 so the incremental
computation captures the flip's realized delta, and the watermark advances to the
current tick's value to prevent mis-attribution on the next record.

Tests verify:
1. Flip trade record captures the *current* tick's realized_pnl delta (not stale prev's)
2. Watermark advances to pos.realized_pnl after a flip (not to prev.realized_pnl)
3. The next record after a flip only counts incremental PnL from pos.realized_pnl forward
4. Non-flip full-close path (CLOSE branch) is unaffected
5. Override args only change flip behavior; normal _record_trade is unchanged
"""

import pytest
from datetime import datetime, timezone

from src.core.enums import PositionStatus, Side
from src.core.models import Position
from src.portfolio.manager import PortfolioManager


def _pos(
    pos_id: str,
    side: Side,
    realized_pnl: float,
    unrealized_pnl: float = 0.0,
    current_price: float = 51000.0,
    symbol: str = "BTC-USDT",
    strategy_name: str = "test_strat",
) -> Position:
    return Position(
        id=pos_id,
        symbol=symbol,
        side=side,
        entry_price=50000.0,
        current_price=current_price,
        quantity=0.1,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        status=PositionStatus.OPEN,
        strategy_name=strategy_name,
    )


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestFlipPnLAttribution:
    def test_flip_trade_captures_current_tick_realized_pnl(self, pm):
        """
        Core ISSUE-029 regression test.

        Tick N:   BUY, realized_pnl = 0.0   (no trade recorded yet)
        Tick N+1: SELL (flip), realized_pnl = 80.0 on the new position object.

        Before fix: _record_trade(prev) used prev.realized_pnl=0.0,
                    recording incremental = 0 - 0 = 0.  The 80.0 was lost.
        After fix:  _record_trade(prev, realized_pnl_override=80.0, override_unrealized=0.0)
                    records incremental = 80.0 - 0 = 80.0.  Correct.
        """
        # Tick N: open BUY position, realized_pnl=0
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0, unrealized_pnl=5.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})
        assert len(pm.get_trade_history()) == 0, "No trade yet — position still open"

        # Tick N+1: flip to SELL.  BloFin credits 80.0 realized_pnl on the
        # new position object (that's the delta produced by closing the BUY leg).
        pos_sell = _pos("p1", Side.SELL, realized_pnl=80.0, unrealized_pnl=0.0)
        pm.update([pos_sell], {"total_equity": 10080.0, "available": 9000.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1, "Flip should generate exactly one trade record for the BUY leg"

        # The trade should capture the realized delta from the flip: 80.0 - 0 = 80.0
        # unrealized override is 0.0 so pnl = 80.0 + 0.0 = 80.0
        assert trades[0].pnl == pytest.approx(80.0), (
            f"Expected pnl=80.0 (current tick realized delta); got {trades[0].pnl}"
        )

    def test_watermark_advances_to_current_tick_realized_pnl_after_flip(self, pm):
        """
        After a flip, the watermark must be pos.realized_pnl (current tick),
        not prev.realized_pnl (stale), so the next record starts from the right baseline.

        Tick N:   BUY, realized=10.0
        Tick N+1: SELL (flip), realized=90.0 on new pos
        Expected watermark after flip: 90.0 (not 10.0)
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=10.0, unrealized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        pos_sell = _pos("p1", Side.SELL, realized_pnl=90.0, unrealized_pnl=0.0)
        pm.update([pos_sell], {"total_equity": 10090.0, "available": 9000.0})

        # Watermark must be the CURRENT tick's realized_pnl (90.0), not the stale 10.0
        watermark = pm._last_recorded_realized_pnl.get("p1")
        assert watermark == pytest.approx(90.0), (
            f"Watermark should be 90.0 (pos.realized_pnl after flip); got {watermark}"
        )

    def test_next_record_after_flip_does_not_double_count(self, pm):
        """
        After a flip, closing the new leg should only count the PnL accrued
        since the flip's realized_pnl, not re-count the flip's amount.

        Tick N:   BUY, realized=0.0
        Tick N+1: SELL (flip), realized=60.0 → trade 1 records 60.0 - 0 = 60.0
                  watermark → 60.0
        Tick N+2: SELL disappears (close) with realized=60.0 (no additional PnL)
                  → trade 2 records 60.0 - 60.0 = 0.0 (correct: SELL generated no more PnL)

        Without fix: watermark after flip = 0.0 (stale prev.realized_pnl),
                     so trade 2 records 60.0 - 0.0 = 60.0 → double-count!
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        pos_sell = _pos("p1", Side.SELL, realized_pnl=60.0)
        pm.update([pos_sell], {"total_equity": 10060.0, "available": 9000.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(60.0), "Flip should record 60.0"

        # Tick N+2: SELL closes, no additional realized PnL beyond the flip's 60.0
        pm.update([], {"total_equity": 10060.0, "available": 10060.0})

        trades = pm.get_trade_history()
        assert len(trades) == 2
        # SELL was recorded via CLOSE branch with realized=60.0 and watermark=60.0
        # → incremental = 60.0 - 60.0 = 0.0
        assert trades[1].pnl == pytest.approx(0.0), (
            f"SELL close after flip should record 0.0 (no additional PnL); got {trades[1].pnl}"
        )

    def test_flip_unrealized_override_is_zero(self, pm):
        """
        The flip trade record must use 0.0 for unrealized PnL, not the
        prev (old) position's floating unrealized value.

        Even if the old BUY leg had unrealized_pnl=200.0 on the previous tick,
        after the flip closes it, its unrealized becomes 0 by definition.
        The trade PnL = incremental_realized + 0.0.
        """
        # Old BUY had a large floating unrealized
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0, unrealized_pnl=200.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        # Flip: SELL side, realized_pnl=50.0 (the close-out of the BUY leg)
        pos_sell = _pos("p1", Side.SELL, realized_pnl=50.0, unrealized_pnl=0.0)
        pm.update([pos_sell], {"total_equity": 10050.0, "available": 9000.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        # pnl = (50.0 - 0.0) + 0.0  (override_unrealized=0.0, not prev's 200.0)
        assert trades[0].pnl == pytest.approx(50.0), (
            f"Flip pnl should be 50.0 (incremental realized only, unrealized=0); "
            f"got {trades[0].pnl}"
        )

    def test_non_flip_full_close_path_unaffected(self, pm):
        """
        The CLOSE branch (position ID disappears from exchange response) must be
        unaffected by the ISSUE-029 fix.

        Tick N:   BUY, realized=0.0, unrealized=100.0
        Tick N+1: Position disappears → close recorded with incremental=0-0=0, unrealized=100.0
                  pnl = 0 + 100.0 = 100.0

        No override arguments are passed on the CLOSE path, so the normal logic applies.
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0, unrealized_pnl=100.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        # Position disappears (exchange closed it — full close, not a flip)
        pm.update([], {"total_equity": 10100.0, "available": 10100.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1, "Full close should generate exactly one trade"
        # Normal path: incremental = 0.0 - 0.0 = 0.0, unrealized = 100.0 → pnl = 100.0
        assert trades[0].pnl == pytest.approx(100.0), (
            f"Full close pnl should be 100.0 (unrealized at close); got {trades[0].pnl}"
        )

    def test_record_trade_signature_has_override_params(self, pm):
        """
        Structural check: _record_trade must accept realized_pnl_override and
        override_unrealized_pnl keyword arguments (validates the fix is present).
        """
        import inspect
        sig = inspect.signature(pm._record_trade)
        params = list(sig.parameters.keys())
        assert "realized_pnl_override" in params, (
            "_record_trade must have realized_pnl_override parameter (ISSUE-029 fix)"
        )
        assert "override_unrealized_pnl" in params, (
            "_record_trade must have override_unrealized_pnl parameter (ISSUE-029 fix)"
        )

    def test_record_trade_override_defaults_to_none(self, pm):
        """
        Both override params must default to None so existing callers (CLOSE path)
        are unaffected when they do not pass the overrides.
        """
        import inspect
        sig = inspect.signature(pm._record_trade)
        assert sig.parameters["realized_pnl_override"].default is None, (
            "realized_pnl_override must default to None"
        )
        assert sig.parameters["override_unrealized_pnl"].default is None, (
            "override_unrealized_pnl must default to None"
        )

    def test_flip_trade_records_old_side_metadata(self, pm):
        """
        The trade record for a flip must preserve the OLD position's side and
        other metadata (the old BUY leg is what was closed by the flip).
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        pos_sell = _pos("p1", Side.SELL, realized_pnl=40.0)
        pm.update([pos_sell], {"total_equity": 10040.0, "available": 9000.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        # Trade side must be BUY (the old leg being closed)
        assert trades[0].side == Side.BUY, (
            f"Trade for a BUY→SELL flip must record side=BUY; got {trades[0].side}"
        )
        assert trades[0].symbol == "BTC-USDT"

    def test_update_flip_passes_correct_realized_override_to_record_trade(self, pm):
        """
        Integration-level check: after two flips, total recorded PnL equals
        the final cumulative realized_pnl, confirming the override is wired
        correctly all the way through update() → _record_trade().

        Flip 1 (BUY→SELL): curr realized = 30  → records 30 - 0 = 30
        Flip 2 (SELL→BUY): curr realized = 80  → records 80 - 30 = 50
        Final close (BUY disappears): realized = 80 → records 80 - 80 = 0
        Total = 30 + 50 + 0 = 80
        """
        pos_buy = _pos("p1", Side.BUY, realized_pnl=0.0)
        pm.update([pos_buy], {"total_equity": 10000.0, "available": 9900.0})

        pos_sell = _pos("p1", Side.SELL, realized_pnl=30.0)
        pm.update([pos_sell], {"total_equity": 10030.0, "available": 9000.0})

        pos_buy2 = _pos("p1", Side.BUY, realized_pnl=80.0)
        pm.update([pos_buy2], {"total_equity": 10080.0, "available": 9000.0})

        # Final close
        pm.update([], {"total_equity": 10080.0, "available": 10080.0})

        trades = pm.get_trade_history()
        assert len(trades) == 3
        total_pnl = sum(t.pnl for t in trades)
        assert total_pnl == pytest.approx(80.0), (
            f"Total PnL across 2 flips + close should equal cumulative realized 80.0; "
            f"got {total_pnl}"
        )
