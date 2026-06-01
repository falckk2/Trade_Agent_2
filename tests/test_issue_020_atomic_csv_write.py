"""
ISSUE-020: save_trade_history() writes to a .csv.tmp file first, then atomically
renames to the final CSV path. This prevents corruption on process kill mid-write.

Tests verify:
1. Successful save produces the final CSV, not a .csv.tmp
2. The .csv.tmp file is cleaned up after successful save
3. On write failure, the .csv.tmp is removed and original CSV is preserved
4. The final file contains all trade records
"""

import csv
import os
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.core.enums import Side
from src.core.models import TradeRecord
from src.portfolio.manager import PortfolioManager


def _trade(trade_id: str = "t1") -> TradeRecord:
    now = datetime.now(tz=timezone.utc)
    return TradeRecord(
        id=trade_id,
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=50000.0,
        exit_price=51000.0,
        quantity=0.1,
        pnl=100.0,
        strategy_name="test_strat",
        opened_at=now,
        closed_at=now,
        duration_seconds=60.0,
    )


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestAtomicCsvWrite:
    def test_successful_save_creates_final_csv(self, pm, tmp_path):
        """save_trade_history() must produce the final CSV file."""
        pm._trade_history.append(_trade("t1"))
        pm.save_trade_history()

        csv_path = tmp_path / "trade_history.csv"
        assert csv_path.exists(), "trade_history.csv was not created"

    def test_tmp_file_absent_after_successful_save(self, pm, tmp_path):
        """The .csv.tmp file must be removed after a successful save (renamed away)."""
        pm._trade_history.append(_trade("t1"))
        pm.save_trade_history()

        tmp_path_file = tmp_path / "trade_history.csv.tmp"
        assert not tmp_path_file.exists(), ".csv.tmp file should not exist after successful save"

    def test_csv_contains_all_trades(self, pm, tmp_path):
        """All trade records must appear in the saved CSV."""
        trades = [_trade(f"t{i}") for i in range(5)]
        pm._trade_history.extend(trades)
        pm.save_trade_history()

        csv_path = tmp_path / "trade_history.csv"
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 5
        ids_in_csv = {row["id"] for row in rows}
        assert ids_in_csv == {f"t{i}" for i in range(5)}

    def test_existing_csv_preserved_on_write_failure(self, pm, tmp_path):
        """If the write to .csv.tmp fails, the existing CSV must not be truncated."""
        # Write initial data
        csv_path = tmp_path / "trade_history.csv"
        csv_path.write_text("id,symbol\nt0,BTC-USDT\n", encoding="utf-8")
        original_content = csv_path.read_text()

        pm._trade_history.append(_trade("t1"))

        # Make the write fail
        with patch("builtins.open", side_effect=OSError("disk full")):
            pm.save_trade_history()  # Should not raise

        # Original file must be unchanged
        assert csv_path.exists()
        assert csv_path.read_text() == original_content

    def test_tmp_file_removed_on_failure(self, pm, tmp_path):
        """On write failure, the partial .csv.tmp file must be cleaned up."""
        pm._trade_history.append(_trade("t1"))

        # Create a stale tmp file first to simulate leftover
        tmp_file = tmp_path / "trade_history.csv.tmp"
        tmp_file.write_text("stale content", encoding="utf-8")

        with patch("builtins.open", side_effect=OSError("fail")):
            pm.save_trade_history()

        # The tmp file should be gone (unlink(missing_ok=True))
        assert not tmp_file.exists()

    def test_os_replace_is_used_not_rename(self, pm, tmp_path):
        """Verify os.replace is called (atomic POSIX rename), not shutil.move or plain rename."""
        pm._trade_history.append(_trade("t1"))

        with patch("os.replace") as mock_replace:
            pm.save_trade_history()
            mock_replace.assert_called_once()
            args = mock_replace.call_args[0]
            # First arg should be .csv.tmp path, second should be the .csv path
            assert str(args[0]).endswith(".csv.tmp")
            assert str(args[1]).endswith("trade_history.csv")
