"""
FABLE-003: Shutdown race — close_all_positions ran while a tick was in flight.

Previously stop() set _running=False and immediately closed all positions
while engine_task could still be mid-tick; the in-flight tick could place a
NEW order after positions were closed, leaving an orphaned position after
"shutdown complete". The fix moves cleanup into start()'s finally block
(strictly ordered after the last tick) and makes stop() wait on a _drained
event until cleanup has fully finished.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.enums import TimeFrame
from src.core.events import EventBus
from src.engine.trading_engine import TradingEngine


def _make_engine(exchange=None, portfolio_manager=None, tick=None):
    exchange = exchange or AsyncMock()
    exchange.get_positions = AsyncMock(return_value=[])
    exchange.get_balance = AsyncMock(
        return_value={"total_equity": 50000.0, "available": 48000.0}
    )

    if portfolio_manager is None:
        portfolio_manager = MagicMock()
        snapshot = MagicMock()
        snapshot.positions = []
        snapshot.total_equity = 50000.0
        portfolio_manager.get_snapshot.return_value = snapshot

    engine = TradingEngine(
        exchange=exchange,
        data_provider=AsyncMock(),
        risk_manager=MagicMock(),
        order_executor=AsyncMock(),
        portfolio_manager=portfolio_manager,
        event_bus=EventBus(),
        symbols=["BTC-USDT"],
        timeframe=TimeFrame.M5,
    )
    if tick is not None:
        engine._tick = tick
    return engine, exchange, portfolio_manager


class TestShutdownDrain:
    @pytest.mark.asyncio
    async def test_stop_waits_for_inflight_tick_before_cleanup(self):
        """A slow tick must complete before close_all_positions runs."""
        events: list[str] = []
        tick_started = asyncio.Event()

        async def slow_tick():
            events.append("tick_start")
            tick_started.set()
            await asyncio.sleep(0.05)
            events.append("tick_end")

        engine, exchange, _ = _make_engine(tick=slow_tick)

        async def record_close():
            events.append("close_all")

        engine.close_all_positions = record_close

        task = asyncio.create_task(engine.start(interval_seconds=10))
        await tick_started.wait()
        await engine.stop()
        await task

        assert events == ["tick_start", "tick_end", "close_all"], (
            f"cleanup ran before the in-flight tick finished: {events}"
        )

    @pytest.mark.asyncio
    async def test_stop_returns_only_after_cleanup_finished(self):
        engine, exchange, portfolio_manager = _make_engine(tick=AsyncMock())

        task = asyncio.create_task(engine.start(interval_seconds=10))
        await asyncio.sleep(0.01)
        await engine.stop()

        # By the time stop() returns, cleanup must have fully run.
        exchange.disconnect.assert_awaited_once()
        portfolio_manager.save_trade_history.assert_called_once()
        await task

    @pytest.mark.asyncio
    async def test_stop_interrupts_interval_sleep(self):
        """stop() must not wait out the remainder of a long interval."""
        engine, _, _ = _make_engine(tick=AsyncMock())

        task = asyncio.create_task(engine.start(interval_seconds=3600))
        await asyncio.sleep(0.01)  # let the first tick run, loop now sleeping
        await asyncio.wait_for(engine.stop(), timeout=1.0)
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_no_new_tick_after_stop_requested(self):
        tick_count = 0

        async def counting_tick():
            nonlocal tick_count
            tick_count += 1

        engine, _, _ = _make_engine(tick=counting_tick)

        task = asyncio.create_task(engine.start(interval_seconds=0.01))
        await asyncio.sleep(0.05)
        await engine.stop()
        count_at_stop = tick_count
        await task
        await asyncio.sleep(0.05)
        assert tick_count == count_at_stop

    @pytest.mark.asyncio
    async def test_stop_close_positions_false_skips_close(self):
        engine, _, _ = _make_engine(tick=AsyncMock())
        engine.close_all_positions = AsyncMock()

        task = asyncio.create_task(engine.start(interval_seconds=10))
        await asyncio.sleep(0.01)
        await engine.stop(close_positions=False)
        await task

        engine.close_all_positions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_without_start_still_cleans_up(self):
        engine, exchange, portfolio_manager = _make_engine()
        engine.close_all_positions = AsyncMock()

        await engine.stop()

        engine.close_all_positions.assert_awaited_once()
        exchange.disconnect.assert_awaited_once()
        portfolio_manager.save_trade_history.assert_called_once()
