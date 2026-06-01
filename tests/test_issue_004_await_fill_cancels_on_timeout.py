"""
ISSUE-004: _await_fill cancels the live order after retry exhaustion instead of
leaving it open on the exchange with no local tracking.

Tests verify:
1. When retries exhaust with a PENDING order, cancel_order is called
2. Order status is set to CANCELLED after successful cancel
3. If cancel also fails, status is set to FAILED
4. When get_order returns None immediately, the post-loop block still fires
   (the "break" on None leaves status non-terminal)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import OrderStatus, OrderType, Side
from src.core.events import EventBus
from src.core.models import Order
from src.execution.executor import OrderExecutor


def _pending_order():
    return Order(
        id="ord_timeout",
        symbol="BTC-USDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=0.1,
        price=None,
        status=OrderStatus.PENDING,
    )


@pytest.fixture
def mock_exchange():
    exchange = AsyncMock()
    exchange.cancel_order = AsyncMock(return_value=True)
    return exchange


@pytest.fixture
def event_bus():
    return EventBus()


class TestAwaitFillCancelsOnTimeout:
    @pytest.mark.asyncio
    async def test_cancel_called_after_retry_exhaustion(self, mock_exchange, event_bus):
        """After all retries, a still-PENDING order must be cancelled."""
        executor = OrderExecutor(
            exchange=mock_exchange,
            event_bus=event_bus,
            fill_poll_interval=0,
            fill_max_retries=3,
        )
        pending = _pending_order()
        # get_order always returns PENDING — retries exhaust
        mock_exchange.get_order = AsyncMock(return_value=pending)

        result = await executor._await_fill(pending, "BTC-USDT")

        mock_exchange.cancel_order.assert_called_once_with("ord_timeout", "BTC-USDT")
        assert result.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_status_set_to_failed_when_cancel_raises(self, mock_exchange, event_bus):
        """If cancel_order itself raises, status becomes FAILED (not left PENDING)."""
        executor = OrderExecutor(
            exchange=mock_exchange,
            event_bus=event_bus,
            fill_poll_interval=0,
            fill_max_retries=2,
        )
        pending = _pending_order()
        mock_exchange.get_order = AsyncMock(return_value=pending)
        mock_exchange.cancel_order = AsyncMock(side_effect=RuntimeError("cancel failed"))

        result = await executor._await_fill(pending, "BTC-USDT")

        assert result.status == OrderStatus.FAILED

    @pytest.mark.asyncio
    async def test_get_order_returns_none_triggers_post_loop_cancel(self, mock_exchange, event_bus):
        """When get_order returns None immediately (ISSUE-001 scenario), the post-loop
        block fires because the original PENDING order is left non-terminal."""
        executor = OrderExecutor(
            exchange=mock_exchange,
            event_bus=event_bus,
            fill_poll_interval=0,
            fill_max_retries=3,
        )
        pending = _pending_order()
        mock_exchange.get_order = AsyncMock(return_value=None)

        result = await executor._await_fill(pending, "BTC-USDT")

        # Should still try to cancel the order
        mock_exchange.cancel_order.assert_called_once_with("ord_timeout", "BTC-USDT")
        assert result.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_filled_order_does_not_trigger_cancel(self, mock_exchange, event_bus):
        """A FILLED order must NOT be cancelled — terminal status exits cleanly."""
        executor = OrderExecutor(
            exchange=mock_exchange,
            event_bus=event_bus,
            fill_poll_interval=0,
            fill_max_retries=3,
        )
        pending = _pending_order()
        filled = Order(
            id="ord_timeout",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            status=OrderStatus.FILLED,
            filled_quantity=0.1,
            average_fill_price=50000.0,
        )
        mock_exchange.get_order = AsyncMock(return_value=filled)

        result = await executor._await_fill(pending, "BTC-USDT")

        mock_exchange.cancel_order.assert_not_called()
        assert result.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_cancelled_order_does_not_trigger_extra_cancel(self, mock_exchange, event_bus):
        """A CANCELLED order is terminal — no additional cancel call should be made."""
        executor = OrderExecutor(
            exchange=mock_exchange,
            event_bus=event_bus,
            fill_poll_interval=0,
            fill_max_retries=3,
        )
        pending = _pending_order()
        cancelled = Order(
            id="ord_timeout",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            status=OrderStatus.CANCELLED,
        )
        mock_exchange.get_order = AsyncMock(return_value=cancelled)

        result = await executor._await_fill(pending, "BTC-USDT")

        mock_exchange.cancel_order.assert_not_called()
        assert result.status == OrderStatus.CANCELLED
