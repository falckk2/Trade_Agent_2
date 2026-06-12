"""
FABLE-012: No performance metrics — trade history existed but was never
analyzed.

Adds PortfolioManager.get_performance_stats(strategy_name=None) computing
win rate, profit factor, expectancy, fees, streaks etc. from recorded trades
(TradeRecord.pnl is already net of fees), and a dashboard table
(build_performance_stats_table) with per-strategy rows plus TOTAL.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core.enums import Side
from src.core.models import TradeRecord
from src.dashboard.components import build_performance_stats_table
from src.portfolio.manager import PortfolioManager


def _trade(pnl, strategy="s1", fee=0.1, duration=60.0, n=[0]):
    n[0] += 1
    now = datetime.now(tz=timezone.utc)
    return TradeRecord(
        id=f"t{n[0]}",
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=50000.0,
        exit_price=50000.0 + pnl,
        quantity=0.1,
        pnl=pnl,
        strategy_name=strategy,
        opened_at=now - timedelta(seconds=duration),
        closed_at=now,
        duration_seconds=duration,
        fee=fee,
    )


@pytest.fixture
def pm(tmp_path):
    manager = PortfolioManager(data_dir=str(tmp_path))
    manager._trade_history.extend(
        [
            _trade(100.0, "s1"),   # win
            _trade(-50.0, "s1"),   # loss
            _trade(-30.0, "s1"),   # loss
            _trade(200.0, "s2"),   # win
        ]
    )
    return manager


class TestGetPerformanceStats:
    def test_overall_stats(self, pm):
        stats = pm.get_performance_stats()
        assert stats["trade_count"] == 4
        assert stats["win_count"] == 2
        assert stats["loss_count"] == 2
        assert stats["win_rate"] == pytest.approx(0.5)
        assert stats["net_pnl"] == pytest.approx(220.0)
        assert stats["gross_profit"] == pytest.approx(300.0)
        assert stats["gross_loss"] == pytest.approx(80.0)
        assert stats["profit_factor"] == pytest.approx(300.0 / 80.0)
        assert stats["total_fees"] == pytest.approx(0.4)
        assert stats["expectancy"] == pytest.approx(55.0)

    def test_per_strategy_filter(self, pm):
        stats = pm.get_performance_stats("s1")
        assert stats["trade_count"] == 3
        assert stats["net_pnl"] == pytest.approx(20.0)
        assert stats["win_rate"] == pytest.approx(1 / 3)

    def test_max_consecutive_losses(self, pm):
        stats = pm.get_performance_stats("s1")
        assert stats["max_consecutive_losses"] == 2

    def test_avg_win_and_loss(self, pm):
        stats = pm.get_performance_stats("s1")
        assert stats["avg_win"] == pytest.approx(100.0)
        assert stats["avg_loss"] == pytest.approx(-40.0)

    def test_no_trades_returns_zeroes(self, tmp_path):
        manager = PortfolioManager(data_dir=str(tmp_path))
        stats = manager.get_performance_stats()
        assert stats["trade_count"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["profit_factor"] == 0.0
        assert stats["expectancy"] == 0.0

    def test_all_wins_profit_factor_is_inf(self, tmp_path):
        manager = PortfolioManager(data_dir=str(tmp_path))
        manager._trade_history.append(_trade(100.0))
        assert manager.get_performance_stats()["profit_factor"] == float("inf")


class TestStatsTable:
    def test_table_renders_rows_and_total(self, pm):
        rows = [
            {"name": name, **pm.get_performance_stats(name)}
            for name in ["s1", "s2"]
        ]
        rows.append({"name": "TOTAL", **pm.get_performance_stats()})
        table = build_performance_stats_table(rows)
        assert len(table.rowData) == 3
        assert table.rowData[-1]["Strategy"] == "TOTAL"
        assert table.rowData[-1]["Trades"] == 4

    def test_inf_profit_factor_rendered_as_symbol(self, tmp_path):
        manager = PortfolioManager(data_dir=str(tmp_path))
        manager._trade_history.append(_trade(100.0))
        rows = [{"name": "s1", **manager.get_performance_stats()}]
        table = build_performance_stats_table(rows)
        assert table.rowData[0]["Profit Factor"] == "∞"
