"""
ISSUE-010: EventBus/MarketDataProvider dispatches async subscriber callbacks via
loop.create_task() rather than silently dropping coroutines.

Tests verify:
1. Sync callbacks are called directly (existing behavior preserved)
2. Async callbacks returning coroutines are scheduled via loop.create_task()
3. Coroutines are NOT silently dropped (RuntimeWarning would indicate a drop)
4. Both sync and async subscribers can coexist
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import EventType, TimeFrame
from src.core.events import Event, EventBus
from src.core.models import Candle
from src.data.provider import MarketDataProvider


def _make_candle() -> Candle:
    return Candle(
        timestamp=datetime.now(timezone.utc),
        open=100.0, high=110.0, low=90.0, close=105.0, volume=1000.0,
    )


def _candle_update_event(symbol: str = "BTC-USDT", tf: TimeFrame | None = None) -> Event:
    return Event(
        event_type=EventType.CANDLE_UPDATE,
        payload={"symbol": symbol, "candle": _make_candle(), "timeframe": tf},
    )


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_exchange():
    exc = AsyncMock()
    exc.get_candles = AsyncMock(return_value=[])
    return exc


@pytest.fixture
def provider(mock_exchange, event_bus):
    p = MarketDataProvider(exchange=mock_exchange, event_bus=event_bus)
    # Seed cache so _on_candle_update has something to update
    candle = _make_candle()
    p._candle_cache[("BTC-USDT", TimeFrame.M5)] = [candle]
    return p


class TestAsyncSubscriberDispatched:
    @pytest.mark.asyncio
    async def test_sync_subscriber_called_directly(self, provider, event_bus):
        """Sync callbacks must still be called synchronously."""
        called_with = []

        def sync_cb(candle: Candle):
            called_with.append(candle)

        await provider.subscribe("BTC-USDT", TimeFrame.M5, sync_cb)

        event_bus.publish_sync(_candle_update_event())

        assert len(called_with) == 1
        assert isinstance(called_with[0], Candle)

    @pytest.mark.asyncio
    async def test_async_subscriber_is_scheduled_not_dropped(self, provider, event_bus):
        """Async callbacks must be scheduled via loop.create_task(), not silently dropped."""
        collected = []

        async def async_cb(candle: Candle):
            collected.append(candle)

        await provider.subscribe("BTC-USDT", TimeFrame.M5, async_cb)

        # Fire the event inside a running event loop
        event_bus.publish_sync(_candle_update_event())

        # Allow the scheduled task to run
        await asyncio.sleep(0)

        assert len(collected) == 1, (
            "Async subscriber was not called — coroutine was dropped instead of scheduled"
        )

    @pytest.mark.asyncio
    async def test_async_subscriber_result_is_not_a_dropped_coroutine(self, provider, event_bus):
        """Verifies the coroutine detection path (iscoroutine) is entered for async cb."""
        import asyncio as _asyncio

        created_tasks = []
        original_create_task = _asyncio.get_event_loop().create_task

        async def async_cb(candle: Candle):
            pass

        await provider.subscribe("BTC-USDT", TimeFrame.M5, async_cb)

        with MagicMock() as mock_loop:
            # Patch get_running_loop to capture create_task calls
            loop = _asyncio.get_running_loop()
            real_create_task = loop.create_task

            def capturing_create_task(coro, **kwargs):
                created_tasks.append(coro)
                return real_create_task(coro, **kwargs)

            loop.create_task = capturing_create_task
            try:
                event_bus.publish_sync(_candle_update_event())
            finally:
                loop.create_task = real_create_task

        await asyncio.sleep(0)
        assert len(created_tasks) >= 1, "No task was created for the async subscriber"

    @pytest.mark.asyncio
    async def test_sync_and_async_subscribers_coexist(self, provider, event_bus):
        """Both sync and async subscribers on the same symbol receive the candle."""
        sync_received = []
        async_received = []

        def sync_cb(candle: Candle):
            sync_received.append(candle)

        async def async_cb(candle: Candle):
            async_received.append(candle)

        await provider.subscribe("BTC-USDT", TimeFrame.M5, sync_cb)
        await provider.subscribe("BTC-USDT", TimeFrame.M5, async_cb)

        event_bus.publish_sync(_candle_update_event())
        await asyncio.sleep(0)

        assert len(sync_received) == 1
        assert len(async_received) == 1
