"""Tests for OrderExecutor."""

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch

from src.core.enums import (
    EventType,
    OrderStatus,
    OrderType,
    Side,
    SignalType,
)
from src.core.events import Event, EventBus
from src.core.models import Order, Position, Signal
from src.execution.executor import OrderExecutor


@pytest.fixture
def executor(mock_exchange, event_bus):
    # Zero poll interval so tests don't actually sleep
    return OrderExecutor(
        exchange=mock_exchange,
        event_bus=event_bus,
        fill_poll_interval=0,
        fill_max_retries=3,
    )


def _signal(signal_type=SignalType.LONG, symbol="BTC-USDT"):
    return Signal(
        signal_type=signal_type,
        symbol=symbol,
        strength=0.8,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestExecuteSignal:
    @pytest.mark.asyncio
    async def test_long_signal_places_buy_order(self, executor, mock_exchange):
        order = await executor.execute_signal(_signal(), 0.1, "BTC-USDT")
        mock_exchange.place_order.assert_called_once_with(
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
        )
        assert order.id == "ord_123"

    @pytest.mark.asyncio
    async def test_short_signal_places_sell_order(self, executor, mock_exchange):
        await executor.execute_signal(_signal(SignalType.SHORT, "ETH-USDT"), 1.0, "ETH-USDT")
        mock_exchange.place_order.assert_called_once_with(
            symbol="ETH-USDT",
            side=Side.SELL,
            order_type=OrderType.MARKET,
            quantity=1.0,
            price=None,
        )

    @pytest.mark.asyncio
    async def test_limit_signal_passes_order_type_and_price(self, executor, mock_exchange):
        sig = Signal(
            signal_type=SignalType.LONG,
            symbol="BTC-USDT",
            strength=0.8,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
            order_type=OrderType.LIMIT,
            limit_price=49000.0,
        )
        await executor.execute_signal(sig, 0.1, "BTC-USDT")
        mock_exchange.place_order.assert_called_once_with(
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=49000.0,
        )

    @pytest.mark.asyncio
    async def test_hold_signal_raises(self, executor):
        with pytest.raises(ValueError):
            await executor.execute_signal(_signal(SignalType.HOLD), 0.1, "BTC-USDT")

    @pytest.mark.asyncio
    async def test_publishes_order_placed_event(self, executor, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.ORDER_PLACED, handler)
        await executor.execute_signal(_signal(), 0.1, "BTC-USDT")

        assert len(received) == 1
        assert received[0].event_type == EventType.ORDER_PLACED

    @pytest.mark.asyncio
    async def test_publishes_order_filled_event_when_filled(self, executor, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.ORDER_FILLED, handler)
        await executor.execute_signal(_signal(), 0.1, "BTC-USDT")

        assert len(received) == 1
        assert received[0].event_type == EventType.ORDER_FILLED

    @pytest.mark.asyncio
    async def test_no_order_filled_event_when_not_filled(self, mock_exchange, event_bus):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        mock_exchange.get_order = AsyncMock(
            return_value=Order(
                id="ord_123", symbol="BTC-USDT", side=Side.BUY,
                order_type=OrderType.MARKET, quantity=0.1, price=None,
                status=OrderStatus.PENDING,
            )
        )
        received = []
        async def handler(event: Event):
            received.append(event)
        event_bus.subscribe(EventType.ORDER_FILLED, handler)

        await executor.execute_signal(_signal(), 0.1, "BTC-USDT")

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_returns_filled_order_with_fill_data(self, executor, mock_exchange):
        order = await executor.execute_signal(_signal(), 0.1, "BTC-USDT")
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 0.1
        assert order.average_fill_price == 50000.0


class TestPartialFill:
    @pytest.fixture
    def partial_fill_order(self):
        return Order(
            id="ord_123",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=5.0,
            price=None,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=3.0,
            average_fill_price=50000.0,
        )

    @pytest.mark.asyncio
    async def test_partial_fill_cancels_remainder(
        self, mock_exchange, event_bus, partial_fill_order
    ):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        mock_exchange.get_order = AsyncMock(return_value=partial_fill_order)

        await executor.execute_signal(_signal(), 5.0, "BTC-USDT")

        mock_exchange.cancel_order.assert_called_once_with("ord_123", "BTC-USDT")

    @pytest.mark.asyncio
    async def test_partial_fill_emits_order_filled_event(
        self, mock_exchange, event_bus, partial_fill_order
    ):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        mock_exchange.get_order = AsyncMock(return_value=partial_fill_order)

        received = []
        async def handler(event: Event):
            received.append(event)
        event_bus.subscribe(EventType.ORDER_FILLED, handler)

        await executor.execute_signal(_signal(), 5.0, "BTC-USDT")

        assert len(received) == 1
        filled_order = received[0].payload["order"]
        assert filled_order.status == OrderStatus.PARTIALLY_FILLED
        assert filled_order.filled_quantity == 3.0

    @pytest.mark.asyncio
    async def test_partial_fill_returns_order_with_actual_quantity(
        self, mock_exchange, event_bus, partial_fill_order
    ):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        mock_exchange.get_order = AsyncMock(return_value=partial_fill_order)

        order = await executor.execute_signal(_signal(), 5.0, "BTC-USDT")

        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == 3.0
        assert order.quantity == 5.0  # original requested quantity preserved


class TestAwaitFill:
    @pytest.mark.asyncio
    async def test_polls_until_filled(self, mock_exchange, event_bus):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        pending = Order(
            id="ord_123", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.PENDING,
        )
        filled = Order(
            id="ord_123", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.FILLED, filled_quantity=0.1, average_fill_price=50000.0,
        )
        mock_exchange.get_order = AsyncMock(side_effect=[pending, pending, filled])

        result = await executor._await_fill(pending, "BTC-USDT")

        assert result.status == OrderStatus.FILLED
        assert mock_exchange.get_order.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_order_if_get_order_returns_none(self, mock_exchange, event_bus):
        executor = OrderExecutor(
            exchange=mock_exchange, event_bus=event_bus, fill_poll_interval=0
        )
        pending = Order(
            id="ord_123", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.PENDING,
        )
        mock_exchange.get_order = AsyncMock(return_value=None)

        result = await executor._await_fill(pending, "BTC-USDT")

        assert result is pending


class TestClosePosition:
    @pytest.mark.asyncio
    async def test_close_buy_position(self, executor, mock_exchange):
        position = Position(
            id="pos_1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
        )
        await executor.close_position(position)
        mock_exchange.place_order.assert_called_once_with(
            symbol="BTC-USDT",
            side=Side.SELL,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
        )

    @pytest.mark.asyncio
    async def test_close_sell_position(self, executor, mock_exchange):
        position = Position(
            id="pos_2", symbol="ETH-USDT", side=Side.SELL,
            entry_price=3000.0, current_price=2900.0, quantity=1.0,
        )
        await executor.close_position(position)
        mock_exchange.place_order.assert_called_once_with(
            symbol="ETH-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            price=None,
        )

    @pytest.mark.asyncio
    async def test_close_position_with_limit_order(self, executor, mock_exchange):
        position = Position(
            id="pos_1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
        )
        await executor.close_position(position, order_type=OrderType.LIMIT, price=51500.0)
        mock_exchange.place_order.assert_called_once_with(
            symbol="BTC-USDT",
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=51500.0,
        )

    @pytest.mark.asyncio
    async def test_close_publishes_order_filled_event_when_filled(self, executor, event_bus):
        received = []
        async def handler(event: Event):
            received.append(event)
        event_bus.subscribe(EventType.ORDER_FILLED, handler)

        position = Position(
            id="pos_1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=51000.0, quantity=0.1,
        )
        await executor.close_position(position)

        assert len(received) == 1
        assert received[0].event_type == EventType.ORDER_FILLED


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_delegates_to_exchange(self, executor, mock_exchange):
        mock_exchange.cancel_order = AsyncMock(return_value=True)
        result = await executor.cancel_order("ord_123", "BTC-USDT")
        mock_exchange.cancel_order.assert_called_once_with("ord_123", "BTC-USDT")
        assert result is True


class TestGetActiveOrders:
    @pytest.mark.asyncio
    async def test_delegates_to_exchange(self, executor, mock_exchange):
        await executor.get_active_orders()
        mock_exchange.get_open_orders.assert_called_once()
