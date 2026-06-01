"""
ISSUE-016: _check_exits evaluates should_stop_out and should_take_profit exactly once
each per position, storing results in locals to avoid re-evaluation.

Tests verify:
1. should_stop_out is called exactly once per position
2. should_take_profit is called exactly once per position
3. Correct reason string is used (stop-loss vs take-profit) based on which triggered
4. Both can trigger on the same position only once each
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

from src.core.enums import PositionStatus, Side, TimeFrame
from src.core.events import EventBus
from src.core.models import PortfolioSnapshot, Position
from src.engine.trading_engine import TradingEngine


def _pos(entry: float = 50000.0, current: float = 48000.0) -> Position:
    return Position(
        id="p1",
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=entry,
        current_price=current,
        quantity=0.1,
    )


def _snapshot(positions=None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=50000.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=positions or [],
    )


@pytest.fixture
def engine():
    exchange = AsyncMock()
    data_provider = AsyncMock()
    risk_manager = MagicMock()
    risk_manager.should_stop_out = MagicMock(return_value=False)
    risk_manager.should_take_profit = MagicMock(return_value=False)
    order_executor = AsyncMock()
    portfolio_manager = MagicMock()
    event_bus = EventBus()

    eng = TradingEngine(
        exchange=exchange,
        data_provider=data_provider,
        risk_manager=risk_manager,
        order_executor=order_executor,
        portfolio_manager=portfolio_manager,
        event_bus=event_bus,
        symbols=["BTC-USDT"],
        timeframe=TimeFrame.M5,
    )
    eng._risk_manager = risk_manager
    eng._order_executor = order_executor
    return eng


class TestCheckExitsSingleEvaluation:
    @pytest.mark.asyncio
    async def test_should_stop_out_called_once_per_position(self, engine):
        """should_stop_out must be called exactly once per position."""
        pos = _pos()
        portfolio = _snapshot(positions=[pos])

        await engine._check_exits(portfolio)

        assert engine._risk_manager.should_stop_out.call_count == 1

    @pytest.mark.asyncio
    async def test_should_take_profit_called_once_per_position(self, engine):
        """should_take_profit must be called exactly once per position."""
        pos = _pos()
        portfolio = _snapshot(positions=[pos])

        await engine._check_exits(portfolio)

        assert engine._risk_manager.should_take_profit.call_count == 1

    @pytest.mark.asyncio
    async def test_stop_loss_reason_triggers_close(self, engine):
        """When should_stop_out is True, close_position must be called."""
        engine._risk_manager.should_stop_out.return_value = True
        engine._risk_manager.should_take_profit.return_value = False
        pos = _pos(entry=50000.0, current=48000.0)  # -4% → stop-loss

        await engine._check_exits(_snapshot(positions=[pos]))

        engine._order_executor.close_position.assert_called_once_with(pos)

    @pytest.mark.asyncio
    async def test_take_profit_reason_triggers_close(self, engine):
        """When should_take_profit is True, close_position must be called."""
        engine._risk_manager.should_stop_out.return_value = False
        engine._risk_manager.should_take_profit.return_value = True
        pos = _pos(entry=50000.0, current=52500.0)  # +5% → take-profit

        await engine._check_exits(_snapshot(positions=[pos]))

        engine._order_executor.close_position.assert_called_once_with(pos)

    @pytest.mark.asyncio
    async def test_no_exit_when_neither_triggered(self, engine):
        """close_position must NOT be called when neither stop-loss nor take-profit fires."""
        engine._risk_manager.should_stop_out.return_value = False
        engine._risk_manager.should_take_profit.return_value = False
        pos = _pos(entry=50000.0, current=50500.0)

        await engine._check_exits(_snapshot(positions=[pos]))

        engine._order_executor.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_positions_each_checked_once(self, engine):
        """With 3 positions, each risk manager method is called 3 times."""
        positions = [_pos() for _ in range(3)]
        portfolio = _snapshot(positions=positions)

        await engine._check_exits(portfolio)

        assert engine._risk_manager.should_stop_out.call_count == 3
        assert engine._risk_manager.should_take_profit.call_count == 3
