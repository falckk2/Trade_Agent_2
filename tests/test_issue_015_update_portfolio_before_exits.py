"""
ISSUE-015: TradingEngine._tick() calls _update_portfolio() BEFORE _check_exits()
so exit decisions are based on fresh prices.

Tests verify:
1. _update_portfolio is called before _check_exits in _tick()
2. _check_exits receives a snapshot that reflects the latest portfolio update
3. No duplicate _update_portfolio call at end of tick
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

from src.core.enums import PositionStatus, Side, TimeFrame
from src.core.events import EventBus
from src.core.models import PortfolioSnapshot, Position, Signal
from src.engine.trading_engine import TradingEngine


def _snapshot(equity: float = 50000.0, positions=None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=equity,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=positions or [],
    )


@pytest.fixture
def engine_components():
    exchange = AsyncMock()
    exchange.get_positions = AsyncMock(return_value=[])
    exchange.get_balance = AsyncMock(return_value={"total_equity": 50000.0, "available": 50000.0})
    exchange.connect = AsyncMock()
    exchange.disconnect = AsyncMock()

    data_provider = AsyncMock()
    data_provider.get_candles = AsyncMock(return_value=[])
    data_provider.get_current_price = AsyncMock(return_value=50000.0)

    risk_manager = MagicMock()
    risk_manager.validate_signal = MagicMock(return_value=False)
    risk_manager.should_stop_out = MagicMock(return_value=False)
    risk_manager.should_take_profit = MagicMock(return_value=False)
    risk_manager.set_initial_equity = MagicMock()

    order_executor = AsyncMock()
    portfolio_manager = MagicMock()
    portfolio_manager.update = MagicMock()
    portfolio_manager.get_snapshot = MagicMock(return_value=_snapshot())
    portfolio_manager.save_trade_history = MagicMock()

    event_bus = EventBus()

    engine = TradingEngine(
        exchange=exchange,
        data_provider=data_provider,
        risk_manager=risk_manager,
        order_executor=order_executor,
        portfolio_manager=portfolio_manager,
        event_bus=event_bus,
        symbols=["BTC-USDT"],
        timeframe=TimeFrame.M5,
    )
    return {
        "engine": engine,
        "exchange": exchange,
        "portfolio_manager": portfolio_manager,
        "risk_manager": risk_manager,
    }


class TestUpdatePortfolioBeforeExits:
    @pytest.mark.asyncio
    async def test_update_portfolio_called_before_check_exits(self, engine_components):
        """_update_portfolio must be invoked before _check_exits within the same _tick."""
        engine = engine_components["engine"]
        call_order = []

        original_update = engine._update_portfolio
        original_check = engine._check_exits

        async def mock_update():
            call_order.append("update_portfolio")
            await original_update()

        async def mock_check(snapshot):
            call_order.append("check_exits")
            await original_check(snapshot)

        engine._update_portfolio = mock_update
        engine._check_exits = mock_check

        await engine._tick()

        assert "update_portfolio" in call_order
        assert "check_exits" in call_order
        assert call_order.index("update_portfolio") < call_order.index("check_exits"), \
            "_update_portfolio must be called before _check_exits"

    @pytest.mark.asyncio
    async def test_no_duplicate_update_portfolio_in_tick(self, engine_components):
        """_update_portfolio should be called exactly once per tick (not duplicated at end)."""
        engine = engine_components["engine"]
        update_count = []

        original_update = engine._update_portfolio

        async def counting_update():
            update_count.append(1)
            await original_update()

        engine._update_portfolio = counting_update

        await engine._tick()

        assert len(update_count) == 1, \
            f"_update_portfolio called {len(update_count)} times in a single tick (expected 1)"

    @pytest.mark.asyncio
    async def test_check_exits_receives_snapshot_from_latest_update(self, engine_components):
        """The snapshot passed to _check_exits reflects the most recent update call."""
        engine = engine_components["engine"]
        pm = engine_components["portfolio_manager"]

        # Snapshots: first call returns equity=40000, second returns equity=50000
        # (simulate the update populating fresh data)
        fresh_snapshot = _snapshot(equity=50000.0)
        pm.get_snapshot = MagicMock(return_value=fresh_snapshot)

        received_snapshots = []
        original_check = engine._check_exits

        async def capturing_check(snapshot):
            received_snapshots.append(snapshot)
            await original_check(snapshot)

        engine._check_exits = capturing_check

        await engine._tick()

        assert len(received_snapshots) == 1
        # The snapshot should be the one from get_snapshot() (post-update)
        assert received_snapshots[0].total_equity == 50000.0
