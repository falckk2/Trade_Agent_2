"""
FABLE-001: Exchange-side stop-loss/take-profit protection.

Positions previously had no protective orders on the exchange — exits relied
entirely on the engine's poll loop, so a crashed/disconnected bot left open
futures positions unprotected. The fix:
1. TradingEngine computes SL/TP via RiskManager and passes them to
   execute_signal.
2. OrderExecutor forwards stop_loss/take_profit to exchange.place_order.
3. BloFinExchange attaches tpTriggerPrice/slTriggerPrice (tick-rounded,
   order price -1 = market) to the entry order.
4. After close_position, leftover TP/SL trigger orders are cancelled so they
   cannot fire and open an opposite position in net mode.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.exchange.blofin_exchange as exchange_module
from src.core.enums import OrderStatus, OrderType, PositionStatus, Side, SignalType
from src.core.events import EventBus
from src.core.models import Order, Position, Signal
from src.exchange.blofin_exchange import BloFinExchange
from src.execution.executor import OrderExecutor


@pytest.fixture(autouse=True)
def reset_singleton():
    exchange_module._active_instances = 0
    yield
    exchange_module._active_instances = 0


@pytest.fixture
def blofin():
    exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
    exc._client = MagicMock()
    exc._instrument_specs["BTC-USDT"] = {
        "contract_value": 0.001,
        "lot_size": 1.0,
        "min_size": 1.0,
        "tick_size": 0.1,
    }
    exc._client.trading.place_order.return_value = {
        "code": "0",
        "data": [{"orderId": "ord_1"}],
    }
    return exc


@pytest.fixture
def executor(mock_exchange, event_bus):
    return OrderExecutor(
        exchange=mock_exchange,
        event_bus=event_bus,
        fill_poll_interval=0,
        fill_max_retries=3,
    )


def _signal(signal_type=SignalType.LONG):
    return Signal(
        signal_type=signal_type,
        symbol="BTC-USDT",
        strength=0.8,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestExchangeAttachesTpsl:
    @pytest.mark.asyncio
    async def test_place_order_attaches_trigger_prices(self, blofin):
        await blofin.place_order(
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
        )
        kwargs = blofin._client.trading.place_order.call_args.kwargs
        assert kwargs["slTriggerPrice"] == "49000.0"
        assert kwargs["slOrderPrice"] == "-1"
        assert kwargs["tpTriggerPrice"] == "52000.0"
        assert kwargs["tpOrderPrice"] == "-1"

    @pytest.mark.asyncio
    async def test_place_order_without_tpsl_omits_trigger_params(self, blofin):
        await blofin.place_order(
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
        )
        kwargs = blofin._client.trading.place_order.call_args.kwargs
        assert "slTriggerPrice" not in kwargs
        assert "tpTriggerPrice" not in kwargs

    @pytest.mark.asyncio
    async def test_trigger_prices_rounded_to_tick_size(self, blofin):
        await blofin.place_order(
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
            stop_loss=49000.0333,
            take_profit=52000.0666,
        )
        kwargs = blofin._client.trading.place_order.call_args.kwargs
        assert kwargs["slTriggerPrice"] == "49000.0"
        assert kwargs["tpTriggerPrice"] == "52000.1"

    def test_round_to_tick_no_spec_returns_price_unchanged(self, blofin):
        assert blofin._round_to_tick("UNKNOWN-USDT", 123.456) == 123.456


class TestCancelTpslOrders:
    @pytest.mark.asyncio
    async def test_cancels_each_active_tpsl_order(self, blofin):
        blofin._client.trading.get_active_tpsl_orders.return_value = {
            "data": [{"tpslId": "t1"}, {"tpslId": "t2"}]
        }
        cancelled = await blofin.cancel_tpsl_orders("BTC-USDT")
        assert cancelled == 2
        calls = blofin._client.trading.cancel_tpsl_order.call_args_list
        assert {c.kwargs["tpsl_id"] for c in calls} == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_list_failure_returns_zero(self, blofin):
        blofin._client.trading.get_active_tpsl_orders.side_effect = RuntimeError("boom")
        assert await blofin.cancel_tpsl_orders("BTC-USDT") == 0

    @pytest.mark.asyncio
    async def test_single_cancel_failure_does_not_abort_rest(self, blofin):
        blofin._client.trading.get_active_tpsl_orders.return_value = {
            "data": [{"tpslId": "t1"}, {"tpslId": "t2"}]
        }
        blofin._client.trading.cancel_tpsl_order.side_effect = [
            RuntimeError("boom"),
            {"code": "0"},
        ]
        assert await blofin.cancel_tpsl_orders("BTC-USDT") == 1


class TestExecutorForwardsTpsl:
    @pytest.mark.asyncio
    async def test_execute_signal_forwards_stop_loss_take_profit(
        self, executor, mock_exchange
    ):
        await executor.execute_signal(
            _signal(), 0.1, "BTC-USDT", stop_loss=49000.0, take_profit=52000.0
        )
        kwargs = mock_exchange.place_order.call_args.kwargs
        assert kwargs["stop_loss"] == 49000.0
        assert kwargs["take_profit"] == 52000.0

    @pytest.mark.asyncio
    async def test_close_position_cancels_leftover_tpsl(
        self, executor, mock_exchange, sample_position
    ):
        await executor.close_position(sample_position)
        mock_exchange.cancel_tpsl_orders.assert_awaited_once_with("BTC-USDT")

    @pytest.mark.asyncio
    async def test_close_position_survives_tpsl_cancel_failure(
        self, executor, mock_exchange, sample_position
    ):
        mock_exchange.cancel_tpsl_orders.side_effect = RuntimeError("boom")
        order = await executor.close_position(sample_position)
        assert order.status == OrderStatus.FILLED


class TestEngineComputesTpsl:
    @pytest.mark.asyncio
    async def test_engine_passes_risk_manager_levels_to_executor(self):
        from src.engine.trading_engine import TradingEngine
        from src.core.enums import TimeFrame

        risk_manager = MagicMock()
        risk_manager.validate_signal.return_value = True
        risk_manager.calculate_position_size.return_value = 0.1
        risk_manager.get_stop_loss.return_value = 49000.0
        risk_manager.get_take_profit.return_value = 52000.0
        risk_manager.should_stop_out.return_value = False
        risk_manager.should_take_profit.return_value = False

        order_executor = AsyncMock()
        portfolio_manager = MagicMock()
        snapshot = MagicMock()
        snapshot.positions = []
        snapshot.total_equity = 50000.0
        portfolio_manager.get_snapshot.return_value = snapshot

        data_provider = AsyncMock()
        data_provider.get_candles.return_value = [MagicMock()]
        data_provider.get_current_price.return_value = 50000.0

        strategy = MagicMock()
        strategy.name = "s1"
        strategy.analyze.return_value = _signal()

        engine = TradingEngine(
            exchange=AsyncMock(),
            data_provider=data_provider,
            risk_manager=risk_manager,
            order_executor=order_executor,
            portfolio_manager=portfolio_manager,
            event_bus=EventBus(),
            symbols=["BTC-USDT"],
            timeframe=TimeFrame.M5,
        )
        await engine._process_strategy_symbol(strategy, "BTC-USDT")

        risk_manager.get_stop_loss.assert_called_once()
        risk_manager.get_take_profit.assert_called_once()
        kwargs = order_executor.execute_signal.call_args.kwargs
        assert kwargs["stop_loss"] == 49000.0
        assert kwargs["take_profit"] == 52000.0
