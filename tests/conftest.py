"""Shared test fixtures."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import (
    AggregationMode,
    OrderStatus,
    OrderType,
    PositionStatus,
    Side,
    SignalType,
    TimeFrame,
)
from src.core.events import EventBus
from src.core.models import Candle, Order, Position, PortfolioSnapshot, Signal


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def sample_candles():
    """Generate a list of 50 sample candles with an uptrend."""
    candles = []
    base_price = 50000.0
    for i in range(50):
        price = base_price + i * 100 + (i % 5) * 50
        candles.append(
            Candle(
                timestamp=datetime(2025, 1, 1, i // 60, i % 60, tzinfo=timezone.utc),
                open=price,
                high=price + 50,
                low=price - 30,
                close=price + 20,
                volume=1000.0 + i * 10,
            )
        )
    return candles


@pytest.fixture
def downtrend_candles():
    """Generate candles with a downtrend for testing short signals."""
    candles = []
    base_price = 60000.0
    for i in range(50):
        price = base_price - i * 100 - (i % 3) * 30
        candles.append(
            Candle(
                timestamp=datetime(2025, 1, 1, i // 60, i % 60, tzinfo=timezone.utc),
                open=price,
                high=price + 30,
                low=price - 50,
                close=price - 20,
                volume=800.0 + i * 5,
            )
        )
    return candles


@pytest.fixture
def sample_signal():
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTC-USDT",
        strength=0.8,
        strategy_name="test_strategy",
        timestamp=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def sample_position():
    return Position(
        id="pos_1",
        symbol="BTC-USDT",
        side=Side.BUY,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        unrealized_pnl=100.0,
        realized_pnl=0.0,
        status=PositionStatus.OPEN,
        strategy_name="test_strategy",
    )


@pytest.fixture
def sample_portfolio():
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=50000.0,
        unrealized_pnl=100.0,
        realized_pnl=50.0,
        positions=[],
        strategy_pnl={"test_strategy": 150.0},
    )


@pytest.fixture
def mock_exchange():
    exchange = AsyncMock()
    exchange.connect = AsyncMock()
    exchange.disconnect = AsyncMock()
    exchange.get_balance = AsyncMock(
        return_value={"total_equity": 50000.0, "available": 48000.0}
    )
    exchange.place_order = AsyncMock(
        return_value=Order(
            id="ord_123",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            status=OrderStatus.PENDING,
        )
    )
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.get_open_orders = AsyncMock(return_value=[])
    exchange.get_order = AsyncMock(
        return_value=Order(
            id="ord_123",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            status=OrderStatus.FILLED,
            filled_quantity=0.1,
            average_fill_price=50000.0,
        )
    )
    exchange.get_positions = AsyncMock(return_value=[])
    exchange.get_candles = AsyncMock(return_value=[])
    exchange.get_ticker = AsyncMock(return_value={"last": 50000.0})
    return exchange
