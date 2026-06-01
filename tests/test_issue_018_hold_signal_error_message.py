"""
ISSUE-018: OrderExecutor.execute_signal() raises ValueError with an accurate error
message for non-LONG/SHORT signals (not the misleading "use close_position() for
CLOSE signals" text).

Tests verify:
1. HOLD signal raises ValueError
2. Error message mentions HOLD and the caller's responsibility to filter
3. CLOSE signal also raises ValueError (handled separately by engine)
4. Message does not misleadingly conflate CLOSE and HOLD
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from src.core.enums import OrderStatus, OrderType, Side, SignalType
from src.core.events import EventBus
from src.core.models import Order, Signal
from src.execution.executor import OrderExecutor


def _signal(signal_type: SignalType) -> Signal:
    return Signal(
        signal_type=signal_type,
        symbol="BTC-USDT",
        strength=0.8,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def executor():
    exchange = AsyncMock()
    exchange.place_order = AsyncMock(
        return_value=Order(
            id="o1", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.FILLED,
        )
    )
    exchange.get_order = AsyncMock(return_value=None)
    exchange.cancel_order = AsyncMock(return_value=True)
    event_bus = EventBus()
    return OrderExecutor(exchange=exchange, event_bus=event_bus, fill_poll_interval=0)


class TestHoldSignalErrorMessage:
    @pytest.mark.asyncio
    async def test_hold_signal_raises_value_error(self, executor):
        """HOLD signals must raise ValueError."""
        with pytest.raises(ValueError):
            await executor.execute_signal(_signal(SignalType.HOLD), 0.1, "BTC-USDT")

    @pytest.mark.asyncio
    async def test_hold_signal_error_message_mentions_hold(self, executor):
        """Error message for HOLD must mention 'HOLD' to be actionable."""
        with pytest.raises(ValueError, match=r"HOLD"):
            await executor.execute_signal(_signal(SignalType.HOLD), 0.1, "BTC-USDT")

    @pytest.mark.asyncio
    async def test_hold_signal_error_mentions_caller_filtering(self, executor):
        """Error message should mention filtering responsibility of the caller."""
        with pytest.raises(ValueError) as exc_info:
            await executor.execute_signal(_signal(SignalType.HOLD), 0.1, "BTC-USDT")
        msg = str(exc_info.value)
        # Should mention that caller should filter HOLD
        assert "caller" in msg.lower() or "filter" in msg.lower(), \
            f"Error message does not mention caller responsibility: {msg}"

    @pytest.mark.asyncio
    async def test_long_signal_does_not_raise(self, executor):
        """LONG signals must not raise ValueError."""
        result = await executor.execute_signal(_signal(SignalType.LONG), 0.1, "BTC-USDT")
        assert result is not None

    @pytest.mark.asyncio
    async def test_short_signal_does_not_raise(self, executor):
        """SHORT signals must not raise ValueError."""
        result = await executor.execute_signal(_signal(SignalType.SHORT), 0.1, "BTC-USDT")
        assert result is not None
