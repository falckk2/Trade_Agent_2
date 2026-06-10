"""
FABLE-009: Stale portfolio snapshot within a tick after _check_exits closed
positions.

_tick refreshed the portfolio once at the top; if _check_exits then closed a
position (stop-loss/take-profit), the rest of the tick still saw the dead
position in the snapshot — blocking legitimate same-direction re-entries and
letting CLOSE signals target an already-closed position. The fix has
_check_exits return the number of closures and _tick re-runs
_update_portfolio when it is non-zero (and only then — no extra REST calls
on quiet ticks).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.enums import Side, TimeFrame
from src.core.events import EventBus
from src.core.models import Position
from src.engine.trading_engine import TradingEngine


def _position():
    return Position(
        id="pos_1",
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=50000.0,
        current_price=48000.0,  # 4% below entry → stop-out at default 2%
        quantity=0.1,
    )


def _engine(risk_manager, portfolio_manager):
    return TradingEngine(
        exchange=AsyncMock(),
        data_provider=AsyncMock(),
        risk_manager=risk_manager,
        order_executor=AsyncMock(),
        portfolio_manager=portfolio_manager,
        event_bus=EventBus(),
        symbols=["BTC-USDT"],
        timeframe=TimeFrame.M5,
    )


def _components(positions):
    risk_manager = MagicMock()
    risk_manager.should_stop_out.return_value = True
    risk_manager.should_take_profit.return_value = False

    portfolio_manager = MagicMock()
    snapshot = MagicMock()
    snapshot.positions = positions
    snapshot.total_equity = 50000.0
    portfolio_manager.get_snapshot.return_value = snapshot
    return risk_manager, portfolio_manager


class TestCheckExitsReturnsCount:
    @pytest.mark.asyncio
    async def test_returns_number_of_closed_positions(self):
        risk_manager, portfolio_manager = _components([_position(), _position()])
        engine = _engine(risk_manager, portfolio_manager)
        closed = await engine._check_exits(portfolio_manager.get_snapshot())
        assert closed == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_exit_triggered(self):
        risk_manager, portfolio_manager = _components([_position()])
        risk_manager.should_stop_out.return_value = False
        engine = _engine(risk_manager, portfolio_manager)
        closed = await engine._check_exits(portfolio_manager.get_snapshot())
        assert closed == 0


class TestTickRefreshesAfterExits:
    @pytest.mark.asyncio
    async def test_tick_updates_portfolio_again_after_exit(self):
        risk_manager, portfolio_manager = _components([_position()])
        engine = _engine(risk_manager, portfolio_manager)
        engine._update_portfolio = AsyncMock()

        await engine._tick()

        # Once at the top of the tick + once after the exit closed a position
        assert engine._update_portfolio.await_count == 2

    @pytest.mark.asyncio
    async def test_tick_skips_second_update_when_nothing_closed(self):
        risk_manager, portfolio_manager = _components([_position()])
        risk_manager.should_stop_out.return_value = False
        engine = _engine(risk_manager, portfolio_manager)
        engine._update_portfolio = AsyncMock()

        await engine._tick()

        assert engine._update_portfolio.await_count == 1
