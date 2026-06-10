"""
FABLE-004: Trade history was persisted only at graceful shutdown.

A crash (OOM, kill -9, power loss) lost every trade of the session — and
since _load_trade_history reconstructs per-strategy realized P&L from the
CSV, the loss silently corrupted cumulative P&L across restarts. The fix
appends each trade to trade_history.csv inside _record_trade, while
save_trade_history() keeps doing the atomic full rewrite at shutdown.
"""

import csv
from datetime import datetime, timedelta, timezone

import pytest

from src.core.enums import Side
from src.core.models import Position
from src.portfolio.manager import PortfolioManager


def _position(pos_id="pos_1", symbol="BTC-USDT", realized=100.0):
    return Position(
        id=pos_id,
        symbol=symbol,
        side=Side.BUY,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        unrealized_pnl=0.0,
        realized_pnl=realized,
        strategy_name="test_strategy",
        opened_at=datetime.now(tz=timezone.utc) - timedelta(minutes=5),
    )


def _read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def manager(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestIncrementalPersistence:
    def test_trade_appended_to_csv_without_explicit_save(self, manager, tmp_path):
        balance = {"total_equity": 50000.0, "available": 48000.0}
        manager.update([_position()], balance)
        manager.update([], balance)  # position disappears → trade recorded

        rows = _read_csv(tmp_path / "trade_history.csv")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC-USDT"
        assert rows[0]["strategy_name"] == "test_strategy"
        assert float(rows[0]["pnl"]) == 100.0

    def test_multiple_trades_append_with_single_header(self, manager, tmp_path):
        balance = {"total_equity": 50000.0, "available": 48000.0}
        for i in range(3):
            manager.update([_position(pos_id=f"pos_{i}")], balance)
            manager.update([], balance)

        path = tmp_path / "trade_history.csv"
        rows = _read_csv(path)
        assert len(rows) == 3
        with open(path, encoding="utf-8") as f:
            header_lines = [line for line in f if line.startswith("id,")]
        assert len(header_lines) == 1

    def test_appended_rows_reload_correctly_after_crash(self, manager, tmp_path):
        """Simulate a crash: never call save_trade_history, just reconstruct."""
        balance = {"total_equity": 50000.0, "available": 48000.0}
        manager.update([_position()], balance)
        manager.update([], balance)
        # no save_trade_history() — simulates unclean exit

        reloaded = PortfolioManager(data_dir=str(tmp_path))
        history = reloaded.get_trade_history()
        assert len(history) == 1
        assert history[0].pnl == 100.0
        assert reloaded.get_strategy_pnl("test_strategy") == 100.0

    def test_shutdown_rewrite_does_not_duplicate_appended_trades(
        self, manager, tmp_path
    ):
        balance = {"total_equity": 50000.0, "available": 48000.0}
        manager.update([_position()], balance)
        manager.update([], balance)
        manager.save_trade_history()  # atomic full rewrite

        rows = _read_csv(tmp_path / "trade_history.csv")
        assert len(rows) == 1

        reloaded = PortfolioManager(data_dir=str(tmp_path))
        assert len(reloaded.get_trade_history()) == 1

    def test_append_failure_does_not_abort_trade_recording(
        self, manager, tmp_path, monkeypatch
    ):
        balance = {"total_equity": 50000.0, "available": 48000.0}

        def boom(trade):
            raise OSError("disk full")

        # _append_trade_to_csv catches internally; patch one level deeper
        monkeypatch.setattr("src.portfolio.manager.open", boom, raising=False)
        manager.update([_position()], balance)
        manager.update([], balance)

        # In-memory record must survive the failed append
        assert len(manager.get_trade_history()) == 1
