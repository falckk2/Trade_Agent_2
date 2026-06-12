"""
Dashboard equity curve survives restarts (FABLE-018 follow-up, 2026-06-12).

equity_curve.csv was already persisted per tick, but PortfolioManager started
with an empty in-memory _snapshots list, so every supervised restart
(FABLE-015 makes those routine) blanked the dashboard chart. The manager now
seeds _snapshots from the CSV at construction.
"""

from datetime import datetime, timedelta, timezone

from src.portfolio.manager import PortfolioManager

T0 = datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)


def _manager_with_snapshots(tmp_path, count):
    pm = PortfolioManager(data_dir=str(tmp_path))
    for i in range(count):
        pm.update(positions=[], balance={"total_equity": 50_000.0 + i, "available": 50_000.0})
    return pm


class TestEquityCurveRestartPersistence:
    def test_snapshots_reload_after_restart(self, tmp_path):
        _manager_with_snapshots(tmp_path, 3)

        reborn = PortfolioManager(data_dir=str(tmp_path))  # simulated restart
        snapshots = reborn.get_snapshots()
        assert len(snapshots) == 3
        assert snapshots[-1].total_equity == 50_002.0
        # loaded snapshots carry no positions — chart-only data
        assert snapshots[-1].positions == []

    def test_get_snapshot_returns_last_known_equity_before_first_tick(self, tmp_path):
        _manager_with_snapshots(tmp_path, 2)
        reborn = PortfolioManager(data_dir=str(tmp_path))
        # before any live update() the cards show last persisted equity, not $0
        assert reborn.get_snapshot().total_equity == 50_001.0

    def test_no_file_starts_empty(self, tmp_path):
        pm = PortfolioManager(data_dir=str(tmp_path))
        assert pm.get_snapshots() == []

    def test_malformed_rows_skipped(self, tmp_path):
        _manager_with_snapshots(tmp_path, 2)
        with open(tmp_path / "equity_curve.csv", "a") as f:
            f.write("not-a-date,oops,x,y,z\n")
        reborn = PortfolioManager(data_dir=str(tmp_path))
        assert len(reborn.get_snapshots()) == 2

    def test_retention_cap_applied_on_load(self, tmp_path):
        pm = _manager_with_snapshots(tmp_path, 1)
        # forge a long history straight into the CSV
        with open(tmp_path / "equity_curve.csv", "a") as f:
            for i in range(10_100):
                ts = (T0 + timedelta(seconds=30 * i)).isoformat()
                f.write(f"{ts},50000.0,0,0,0\n")
        reborn = PortfolioManager(data_dir=str(tmp_path))
        assert len(reborn.get_snapshots()) == 10_000

    def test_live_updates_append_after_reload(self, tmp_path):
        _manager_with_snapshots(tmp_path, 2)
        reborn = PortfolioManager(data_dir=str(tmp_path))
        reborn.update(positions=[], balance={"total_equity": 60_000.0, "available": 60_000.0})
        snapshots = reborn.get_snapshots()
        assert len(snapshots) == 3
        assert snapshots[-1].total_equity == 60_000.0
