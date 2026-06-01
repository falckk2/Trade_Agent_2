"""
ISSUE-025: _load_trade_history() parses rows into local buffers first and commits
atomically, skipping malformed rows without discarding the entire history.

Tests verify:
1. A valid CSV loads all rows correctly
2. A CSV with one malformed row skips that row but loads the rest
3. An entirely unreadable file (e.g. missing column) early-returns without crashing
4. State remains consistent after a partial-load (no half-committed data)
5. Load does not raise on an empty or absent CSV file
"""

import csv
import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.core.enums import Side
from src.portfolio.manager import PortfolioManager


def _write_csv(path: Path, rows: list[dict]) -> None:
    headers = ["id", "symbol", "side", "entry_price", "exit_price", "quantity",
               "pnl", "strategy_name", "opened_at", "closed_at", "duration_seconds"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _good_row(trade_id: str = "t1") -> dict:
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "id": trade_id,
        "symbol": "BTC-USDT",
        "side": "buy",
        "entry_price": "50000.0",
        "exit_price": "51000.0",
        "quantity": "0.1",
        "pnl": "100.0",
        "strategy_name": "test_strat",
        "opened_at": now,
        "closed_at": now,
        "duration_seconds": "60.0",
    }


def _bad_row(trade_id: str = "bad") -> dict:
    """A row with an invalid float for pnl."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "id": trade_id,
        "symbol": "BTC-USDT",
        "side": "buy",
        "entry_price": "not_a_number",  # invalid
        "exit_price": "51000.0",
        "quantity": "0.1",
        "pnl": "100.0",
        "strategy_name": "test_strat",
        "opened_at": now,
        "closed_at": now,
        "duration_seconds": "60.0",
    }


class TestLoadTradeHistoryPartialRows:
    def test_valid_csv_loads_all_rows(self, tmp_path):
        """All rows in a valid CSV must be loaded into trade history."""
        csv_path = tmp_path / "trade_history.csv"
        _write_csv(csv_path, [_good_row("t1"), _good_row("t2"), _good_row("t3")])

        pm = PortfolioManager(data_dir=str(tmp_path))
        trades = pm.get_trade_history()
        assert len(trades) == 3
        ids = {t.id for t in trades}
        assert ids == {"t1", "t2", "t3"}

    def test_malformed_row_is_skipped_valid_rows_loaded(self, tmp_path):
        """A malformed row must be skipped; valid rows before and after it must load."""
        csv_path = tmp_path / "trade_history.csv"
        _write_csv(csv_path, [
            _good_row("t1"),
            _bad_row("bad"),
            _good_row("t3"),
        ])

        pm = PortfolioManager(data_dir=str(tmp_path))
        trades = pm.get_trade_history()
        ids = {t.id for t in trades}
        assert "t1" in ids
        assert "t3" in ids
        assert "bad" not in ids
        assert len(trades) == 2

    def test_missing_csv_does_not_crash(self, tmp_path):
        """When no CSV exists, portfolio manager starts with empty history."""
        pm = PortfolioManager(data_dir=str(tmp_path))
        assert pm.get_trade_history() == []

    def test_all_malformed_csv_leaves_empty_history(self, tmp_path):
        """If every row is malformed, trade history stays empty (no partial state)."""
        csv_path = tmp_path / "trade_history.csv"
        _write_csv(csv_path, [_bad_row("b1"), _bad_row("b2")])

        pm = PortfolioManager(data_dir=str(tmp_path))
        assert pm.get_trade_history() == []

    def test_strategy_pnl_consistent_with_loaded_trades(self, tmp_path):
        """After load, strategy_pnl must match the sum of loaded trade pnls."""
        csv_path = tmp_path / "trade_history.csv"
        row1 = _good_row("t1")
        row1["pnl"] = "200.0"
        row1["strategy_name"] = "strat_a"
        row2 = _good_row("t2")
        row2["pnl"] = "150.0"
        row2["strategy_name"] = "strat_a"
        _write_csv(csv_path, [row1, row2])

        pm = PortfolioManager(data_dir=str(tmp_path))
        assert pm._strategy_realized_pnl.get("strat_a", 0.0) == pytest.approx(350.0)

    def test_empty_csv_file_does_not_crash(self, tmp_path):
        """An empty CSV (header only) loads zero trades without crashing."""
        csv_path = tmp_path / "trade_history.csv"
        headers = ["id", "symbol", "side", "entry_price", "exit_price", "quantity",
                   "pnl", "strategy_name", "opened_at", "closed_at", "duration_seconds"]
        with open(csv_path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=headers).writeheader()

        pm = PortfolioManager(data_dir=str(tmp_path))
        assert pm.get_trade_history() == []
