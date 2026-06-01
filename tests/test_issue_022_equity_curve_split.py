"""
ISSUE-022: Targeted tests for the realized/unrealized P&L split fix.

The fix (2026-06-01) covers three components:
1. PortfolioSnapshot has strategy_pnl_realized and strategy_pnl_unrealized fields.
2. PortfolioManager._build_snapshot populates both new fields per strategy.
3. _build_strategy_equity_curves in callbacks.py plots two traces per strategy
   (solid realized line + dotted unrealized overlay).

These targeted tests verify all three components are correctly implemented.
"""

import pytest
from datetime import datetime, timezone

from src.core.enums import PositionStatus, Side
from src.core.models import Position, PortfolioSnapshot
from src.portfolio.manager import PortfolioManager
from src.dashboard.callbacks import _build_strategy_equity_curves


def _pos(
    pos_id: str,
    strategy: str,
    unrealized: float,
    realized: float = 0.0,
    current_price: float = 51000.0,
) -> Position:
    return Position(
        id=pos_id,
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=50000.0,
        current_price=current_price,
        quantity=0.1,
        unrealized_pnl=unrealized,
        realized_pnl=realized,
        strategy_name=strategy,
    )


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestPortfolioSnapshotSplitFields:
    """Verify PortfolioSnapshot fields and _build_snapshot population (ISSUE-022 fix)."""

    def test_snapshot_fields_exist_on_model(self):
        """PortfolioSnapshot dataclass must declare strategy_pnl_realized and strategy_pnl_unrealized."""
        snap = PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            total_equity=10000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
        assert hasattr(snap, "strategy_pnl_realized"), \
            "PortfolioSnapshot must have strategy_pnl_realized field"
        assert hasattr(snap, "strategy_pnl_unrealized"), \
            "PortfolioSnapshot must have strategy_pnl_unrealized field"
        assert isinstance(snap.strategy_pnl_realized, dict)
        assert isinstance(snap.strategy_pnl_unrealized, dict)

    def test_realized_only_includes_closed_trades(self, pm):
        """strategy_pnl_realized comes from _strategy_realized_pnl (closed trades only)."""
        # Manually inject realized PnL to simulate a closed trade
        pm._strategy_realized_pnl["strat_a"] = 300.0
        # No open positions — unrealized should be 0
        pm.update([], {"total_equity": 10300.0, "available": 10300.0})
        snap = pm.get_snapshot()

        assert snap.strategy_pnl_realized.get("strat_a", 0.0) == pytest.approx(300.0)
        assert snap.strategy_pnl_unrealized.get("strat_a", 0.0) == pytest.approx(0.0)

    def test_unrealized_only_includes_open_positions(self, pm):
        """strategy_pnl_unrealized sums open-position unrealized PnL only."""
        pos = _pos("p1", "strat_b", unrealized=150.0)
        pm.update([pos], {"total_equity": 10150.0, "available": 10000.0})
        snap = pm.get_snapshot()

        assert snap.strategy_pnl_unrealized.get("strat_b", 0.0) == pytest.approx(150.0)
        # No closed trades for strat_b
        assert snap.strategy_pnl_realized.get("strat_b", 0.0) == pytest.approx(0.0)

    def test_both_fields_populated_for_mixed_state(self, pm):
        """Both fields correctly populated when there are both closed and open PnL."""
        pm._strategy_realized_pnl["strat_c"] = 100.0
        pos = _pos("p1", "strat_c", unrealized=50.0)
        pm.update([pos], {"total_equity": 10150.0, "available": 10000.0})
        snap = pm.get_snapshot()

        assert snap.strategy_pnl_realized.get("strat_c", 0.0) == pytest.approx(100.0)
        assert snap.strategy_pnl_unrealized.get("strat_c", 0.0) == pytest.approx(50.0)
        # Combined backward-compat field should still equal realized + unrealized
        assert snap.strategy_pnl.get("strat_c", 0.0) == pytest.approx(150.0)

    def test_realized_stable_when_position_closes(self, pm):
        """
        Key ISSUE-022 fix: strategy_pnl_realized does not drop when a position
        closes (unrealized snaps to 0 — realized stays at the recorded trade value).
        """
        pos = _pos("p1", "strat_d", unrealized=200.0)
        pm.update([pos], {"total_equity": 10200.0, "available": 10000.0})
        snap_open = pm.get_snapshot()

        assert snap_open.strategy_pnl_unrealized.get("strat_d", 0.0) == pytest.approx(200.0)
        realized_before_close = snap_open.strategy_pnl_realized.get("strat_d", 0.0)

        # Close position — it disappears from next update
        pm.update([], {"total_equity": 10200.0, "available": 10200.0})
        snap_closed = pm.get_snapshot()

        # Unrealized correctly goes to 0 (position gone)
        assert snap_closed.strategy_pnl_unrealized.get("strat_d", 0.0) == pytest.approx(0.0)
        # Realized is recorded from the trade — no drop
        realized_after_close = snap_closed.strategy_pnl_realized.get("strat_d", 0.0)
        assert realized_after_close >= realized_before_close, (
            "Realized P&L must not drop when position closes — that was the ISSUE-022 bug"
        )


class TestBuildStrategyEquityCurvesTraces:
    """Verify _build_strategy_equity_curves produces two traces per strategy."""

    def _make_snapshot(
        self,
        strat: str,
        realized: float,
        unrealized: float,
    ) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            total_equity=10000.0 + realized + unrealized,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            strategy_pnl={strat: realized + unrealized},
            strategy_pnl_realized={strat: realized},
            strategy_pnl_unrealized={strat: unrealized},
        )

    def test_two_traces_per_strategy_when_all_selected(self):
        """Two traces per strategy: one realized (solid), one unrealized (dotted)."""
        snaps = [
            self._make_snapshot("strat_a", realized=100.0, unrealized=50.0),
            self._make_snapshot("strat_a", realized=120.0, unrealized=30.0),
        ]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="all")

        trace_names = [t.name for t in fig.data]
        assert "strat_a (realized)" in trace_names, \
            "Expected a 'strat_a (realized)' trace"
        assert "strat_a (unrealized)" in trace_names, \
            "Expected a 'strat_a (unrealized)' trace"
        assert len(fig.data) == 2, \
            f"Expected exactly 2 traces for one strategy; got {len(fig.data)}"

    def test_realized_trace_is_solid_line(self):
        """Realized trace uses solid line style (width=2, no dash)."""
        snaps = [self._make_snapshot("strat_a", realized=100.0, unrealized=50.0)]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="all")

        realized_trace = next(t for t in fig.data if "(realized)" in t.name)
        # Should be a solid line (no dash attribute or dash is not "dot")
        line_dash = realized_trace.line.dash if realized_trace.line.dash else None
        assert line_dash != "dot", "Realized trace must not be a dotted line"

    def test_unrealized_trace_is_dotted_line(self):
        """Unrealized trace uses dotted line style (dash='dot')."""
        snaps = [self._make_snapshot("strat_a", realized=100.0, unrealized=50.0)]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="all")

        unrealized_trace = next(t for t in fig.data if "(unrealized)" in t.name)
        assert unrealized_trace.line.dash == "dot", \
            "Unrealized trace must use dotted line style"

    def test_realized_trace_values_match_snapshot_data(self):
        """Realized trace y-values come from strategy_pnl_realized, not the combined value."""
        snaps = [
            self._make_snapshot("strat_a", realized=100.0, unrealized=50.0),
            self._make_snapshot("strat_a", realized=150.0, unrealized=25.0),
        ]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="all")

        realized_trace = next(t for t in fig.data if "(realized)" in t.name)
        assert list(realized_trace.y) == pytest.approx([100.0, 150.0]), \
            "Realized trace y-values must come from strategy_pnl_realized"

    def test_unrealized_trace_values_match_snapshot_data(self):
        """Unrealized trace y-values come from strategy_pnl_unrealized, not combined."""
        snaps = [
            self._make_snapshot("strat_a", realized=100.0, unrealized=50.0),
            self._make_snapshot("strat_a", realized=150.0, unrealized=25.0),
        ]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="all")

        unrealized_trace = next(t for t in fig.data if "(unrealized)" in t.name)
        assert list(unrealized_trace.y) == pytest.approx([50.0, 25.0]), \
            "Unrealized trace y-values must come from strategy_pnl_unrealized"

    def test_strategy_filter_shows_only_selected_strategy(self):
        """When a specific strategy is selected, only its traces appear."""
        snaps = [
            PortfolioSnapshot(
                timestamp=datetime.now(tz=timezone.utc),
                total_equity=10000.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                strategy_pnl={"strat_a": 100.0, "strat_b": 50.0},
                strategy_pnl_realized={"strat_a": 100.0, "strat_b": 50.0},
                strategy_pnl_unrealized={"strat_a": 0.0, "strat_b": 0.0},
            )
        ]
        fig = _build_strategy_equity_curves(snaps, selected_strategy="strat_a")

        for trace in fig.data:
            assert "strat_b" not in trace.name, \
                f"Filtered view should not include strat_b trace; got '{trace.name}'"
        strat_a_traces = [t for t in fig.data if "strat_a" in t.name]
        assert len(strat_a_traces) == 2, "Should show exactly 2 traces for strat_a"

    def test_empty_snapshots_returns_empty_figure(self):
        """Empty snapshot list returns an empty figure without raising."""
        fig = _build_strategy_equity_curves([], selected_strategy="all")
        assert fig is not None
        assert len(fig.data) == 0
