"""Integration tests for the TradingEngine with mocked exchange."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.enums import (
    OrderStatus,
    OrderType,
    PositionStatus,
    Side,
    SignalType,
    TimeFrame,
)
from src.core.events import EventBus
from src.core.models import Candle, Order, Position
from src.data.provider import MarketDataProvider
from src.engine.trading_engine import TradingEngine
from src.execution.executor import OrderExecutor
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.strategies.sma_crossover import SMACrossoverStrategy
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.composite import CompositeStrategy


@pytest.fixture
def engine_components(mock_exchange, tmp_path):
    event_bus = EventBus()
    data_provider = MarketDataProvider(exchange=mock_exchange, event_bus=event_bus)
    risk_manager = RiskManager(
        max_position_pct=0.05,
        max_exposure_pct=0.50,
        max_drawdown_pct=0.10,
    )
    order_executor = OrderExecutor(exchange=mock_exchange, event_bus=event_bus)
    portfolio_manager = PortfolioManager(data_dir=str(tmp_path))

    engine = TradingEngine(
        exchange=mock_exchange,
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
        "exchange": mock_exchange,
        "data_provider": data_provider,
        "portfolio_manager": portfolio_manager,
        "event_bus": event_bus,
    }


def _make_trending_candles(direction="up", count=50):
    """Create candles that will trigger SMA crossover signals."""
    candles = []
    base = 50000.0
    for i in range(count):
        if direction == "up":
            price = base + i * 50
        else:
            price = base - i * 50
        candles.append(
            Candle(
                timestamp=datetime(2025, 1, 1, i // 60, i % 60, tzinfo=timezone.utc),
                open=price,
                high=price + 20,
                low=price - 20,
                close=price + 10,
                volume=1000.0,
            )
        )
    return candles


class TestEngineLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, engine_components):
        engine = engine_components["engine"]
        exchange = engine_components["exchange"]

        # Start engine in background, stop after one tick
        async def run():
            task = asyncio.create_task(engine.start(interval_seconds=0.1))
            await asyncio.sleep(0.3)
            await engine.stop()
            task.cancel()

        await run()
        exchange.connect.assert_called_once()
        exchange.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_add_remove_strategy(self, engine_components):
        engine = engine_components["engine"]
        sma = SMACrossoverStrategy(name="test_sma")

        engine.add_strategy(sma, symbols=["BTC-USDT"])
        assert len(engine.strategies) == 1

        engine.remove_strategy("test_sma")
        assert len(engine.strategies) == 0


class TestEngineWithStrategies:
    @pytest.mark.asyncio
    async def test_processes_strategy_signals(self, engine_components):
        engine = engine_components["engine"]
        exchange = engine_components["exchange"]

        # Set up exchange to return trending candles
        candles = _make_trending_candles("up", 50)
        exchange.get_candles = AsyncMock(return_value=candles)
        exchange.get_ticker = AsyncMock(return_value={"last": 52000.0})

        sma = SMACrossoverStrategy(name="test_sma", fast_period=5, slow_period=15)
        engine.add_strategy(sma, symbols=["BTC-USDT"])

        # Run one tick
        await exchange.connect()
        await engine._tick()

        # Portfolio should have been updated
        snapshot = engine_components["portfolio_manager"].get_snapshot()
        assert snapshot is not None

    @pytest.mark.asyncio
    async def test_composite_strategy_in_engine(self, engine_components):
        engine = engine_components["engine"]
        exchange = engine_components["exchange"]

        candles = _make_trending_candles("up", 50)
        exchange.get_candles = AsyncMock(return_value=candles)
        exchange.get_ticker = AsyncMock(return_value={"last": 52000.0})

        sma = SMACrossoverStrategy(name="sma", fast_period=5, slow_period=15)
        rsi = RSIStrategy(name="rsi", period=14)
        composite = CompositeStrategy(name="combined")
        composite.add_strategy(sma, 0.6)
        composite.add_strategy(rsi, 0.4)

        engine.add_strategy(composite, symbols=["BTC-USDT"])

        await exchange.connect()
        await engine._tick()

        # Should not raise
        snapshot = engine_components["portfolio_manager"].get_snapshot()
        assert snapshot is not None


class TestEnginePortfolioUpdates:
    @pytest.mark.asyncio
    async def test_portfolio_updated_each_tick(self, engine_components):
        engine = engine_components["engine"]
        exchange = engine_components["exchange"]
        pm = engine_components["portfolio_manager"]

        exchange.get_candles = AsyncMock(return_value=[])
        exchange.get_positions = AsyncMock(return_value=[])
        exchange.get_balance = AsyncMock(
            return_value={"total_equity": 50000.0, "available": 50000.0}
        )

        await exchange.connect()
        await engine._tick()
        await engine._tick()

        snapshots = pm.get_snapshots()
        assert len(snapshots) >= 2
