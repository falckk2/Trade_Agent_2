"""Tests for PortfolioManager."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from src.core.enums import EventType, OrderStatus, OrderType, PositionStatus, Side
from src.core.events import Event, EventBus
from src.core.models import Order, Position
from src.portfolio.manager import PortfolioManager


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestUpdate:
    def test_initial_update(self, pm):
        positions = [
            Position(
                id="p1",
                symbol="BTC-USDT",
                side=Side.BUY,
                entry_price=50000.0,
                current_price=51000.0,
                quantity=0.1,
                unrealized_pnl=100.0,
                strategy_name="test",
            )
        ]
        pm.update(positions, {"total_equity": 50100.0, "available": 48000.0})

        snapshot = pm.get_snapshot()
        assert snapshot.total_equity == 50100.0
        assert len(snapshot.positions) == 1

    def test_detects_closed_position(self, pm):
        # First update with a position
        pos = Position(
            id="p1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            current_price=51000.0,
            quantity=0.1,
            unrealized_pnl=100.0,
            realized_pnl=0.0,
            strategy_name="test",
        )
        pm.update([pos], {"total_equity": 50100.0, "available": 48000.0})

        # Second update without the position (it was closed)
        pm.update([], {"total_equity": 50200.0, "available": 50200.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        assert trades[0].symbol == "BTC-USDT"

    def test_snapshots_accumulate(self, pm):
        for i in range(5):
            pm.update([], {"total_equity": 50000.0 + i * 100, "available": 50000.0})
        assert len(pm.get_snapshots()) == 5


class TestStrategyPnl:
    def test_unrealized_pnl(self, pm):
        pos = Position(
            id="p1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            current_price=51000.0,
            quantity=0.1,
            unrealized_pnl=100.0,
            strategy_name="strat_a",
        )
        pm.update([pos], {"total_equity": 50100.0, "available": 48000.0})
        assert pm.get_strategy_pnl("strat_a") == 100.0

    def test_realized_pnl_after_close(self, pm):
        pos = Position(
            id="p1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            current_price=51000.0,
            quantity=0.1,
            unrealized_pnl=100.0,
            realized_pnl=50.0,
            strategy_name="strat_a",
        )
        pm.update([pos], {"total_equity": 50150.0, "available": 48000.0})
        pm.update([], {"total_equity": 50200.0, "available": 50200.0})

        # After close, realized PnL should reflect the trade
        pnl = pm.get_strategy_pnl("strat_a")
        assert pnl == 150.0  # realized + unrealized from the closed position


class TestTradeHistory:
    def test_filter_by_strategy(self, pm):
        pos_a = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
            unrealized_pnl=100.0, strategy_name="strat_a",
        )
        pos_b = Position(
            id="p2", symbol="ETH-USDT", side=Side.BUY,
            entry_price=3000.0, current_price=3100.0, quantity=1.0,
            unrealized_pnl=100.0, strategy_name="strat_b",
        )
        pm.update([pos_a, pos_b], {"total_equity": 50000.0, "available": 48000.0})
        pm.update([], {"total_equity": 50200.0, "available": 50200.0})

        assert len(pm.get_trade_history("strat_a")) == 1
        assert len(pm.get_trade_history("strat_b")) == 1
        assert len(pm.get_trade_history()) == 2


class TestCsvPersistence:
    def test_save_and_load(self, tmp_path):
        pm1 = PortfolioManager(data_dir=str(tmp_path))
        pos = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
            unrealized_pnl=100.0, strategy_name="test",
        )
        pm1.update([pos], {"total_equity": 50100.0, "available": 48000.0})
        pm1.update([], {"total_equity": 50200.0, "available": 50200.0})
        pm1.save_trade_history()

        # Load in a new instance
        pm2 = PortfolioManager(data_dir=str(tmp_path))
        trades = pm2.get_trade_history()
        assert len(trades) == 1
        assert trades[0].symbol == "BTC-USDT"


class TestFillPriceUsedAsExitPrice:
    def test_uses_fill_price_when_available(self, tmp_path):
        event_bus = EventBus()
        pm = PortfolioManager(data_dir=str(tmp_path), event_bus=event_bus)

        pos = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
            unrealized_pnl=100.0, strategy_name="test",
        )
        pm.update([pos], {"total_equity": 50100.0, "available": 48000.0})

        # Simulate ORDER_FILLED event for the close order (SELL side)
        close_order = Order(
            id="ord_close", symbol="BTC-USDT", side=Side.SELL,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.FILLED, filled_quantity=0.1,
            average_fill_price=51500.0,
        )
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            event_bus.publish(Event(event_type=EventType.ORDER_FILLED, payload={"order": close_order}))
        )

        # Now close the position
        pm.update([], {"total_equity": 50200.0, "available": 50200.0})

        trades = pm.get_trade_history()
        assert len(trades) == 1
        assert trades[0].exit_price == 51500.0

    def test_falls_back_to_current_price_when_no_fill_event(self, tmp_path):
        pm = PortfolioManager(data_dir=str(tmp_path))

        pos = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
            unrealized_pnl=100.0, strategy_name="test",
        )
        pm.update([pos], {"total_equity": 50100.0, "available": 48000.0})
        pm.update([], {"total_equity": 50200.0, "available": 50200.0})

        trades = pm.get_trade_history()
        assert trades[0].exit_price == 51000.0


class TestGetAllStrategyNames:
    def test_includes_active_and_realized(self, pm):
        pos = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
            unrealized_pnl=100.0, strategy_name="active_strat",
        )
        pm.update([pos], {"total_equity": 50000.0, "available": 48000.0})
        # Close it
        pm.update([], {"total_equity": 50100.0, "available": 50100.0})
        # Add a new position with different strategy
        pos2 = Position(
            id="p2", symbol="ETH-USDT", side=Side.BUY,
            entry_price=3000.0, current_price=3000.0, quantity=1.0,
            strategy_name="new_strat",
        )
        pm.update([pos2], {"total_equity": 50100.0, "available": 47000.0})

        names = pm.get_all_strategy_names()
        assert "active_strat" in names
        assert "new_strat" in names
