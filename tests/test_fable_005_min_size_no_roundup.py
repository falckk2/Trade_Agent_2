"""
FABLE-005: _to_contracts silently rounded orders UP to the exchange minimum.

`max(rounded, min_size)` meant a risk-approved quantity below the instrument
minimum was inflated — the placed order could exceed what the risk manager
authorized by a large multiple on a small account. The fix returns 0.0 for
below-minimum sizes (logged skip); OrderExecutor.execute_signal treats the
resulting ValueError from place_order as an expected skip, returning a FAILED
order without publishing ORDER_PLACED/ORDER_FILLED events.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.exchange.blofin_exchange as exchange_module
from src.core.enums import EventType, OrderStatus, OrderType, Side, SignalType
from src.core.events import EventBus
from src.core.models import Signal
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
    # BTC-USDT swap: 1 contract = 0.001 BTC, min 1 contract
    exc._instrument_specs["BTC-USDT"] = {
        "contract_value": 0.001,
        "lot_size": 1.0,
        "min_size": 1.0,
        "tick_size": 0.1,
    }
    return exc


class TestToContracts:
    def test_below_min_size_returns_zero(self, blofin):
        # 0.0004 BTC = 0.4 contracts → rounds to 0, below min 1 → skip
        assert blofin._to_contracts("BTC-USDT", 0.0004) == 0.0

    def test_rounds_to_zero_returns_zero_not_min(self, blofin):
        # Previously max(0, 1.0) returned 1.0 — a full min-size order
        assert blofin._to_contracts("BTC-USDT", 0.0001) == 0.0

    def test_at_min_size_passes_through(self, blofin):
        assert blofin._to_contracts("BTC-USDT", 0.001) == 1.0

    def test_above_min_size_unchanged(self, blofin):
        assert blofin._to_contracts("BTC-USDT", 0.0026) == 3.0

    @pytest.mark.asyncio
    async def test_place_order_below_min_raises_value_error(self, blofin):
        with pytest.raises(ValueError):
            await blofin.place_order(
                symbol="BTC-USDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=0.0001,
            )
        blofin._client.trading.place_order.assert_not_called()


class TestExecutorSkip:
    @pytest.mark.asyncio
    async def test_execute_signal_returns_failed_order_on_value_error(self):
        exchange = AsyncMock()
        exchange.place_order.side_effect = ValueError("below minimum")
        event_bus = EventBus()
        published = []
        event_bus.subscribe(EventType.ORDER_PLACED, lambda e: published.append(e))
        event_bus.subscribe(EventType.ORDER_FILLED, lambda e: published.append(e))

        executor = OrderExecutor(
            exchange=exchange, event_bus=event_bus,
            fill_poll_interval=0, fill_max_retries=1,
        )
        signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTC-USDT",
            strength=0.1,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
        )
        order = await executor.execute_signal(signal, 0.0001, "BTC-USDT")

        assert order.status == OrderStatus.FAILED
        assert order.strategy_name == "test"
        assert published == []  # no ORDER_PLACED / ORDER_FILLED events

    @pytest.mark.asyncio
    async def test_hold_signal_value_error_still_propagates(self):
        """The HOLD/CLOSE guard raises before place_order — must not be swallowed."""
        executor = OrderExecutor(exchange=AsyncMock(), event_bus=EventBus())
        signal = Signal(
            signal_type=SignalType.HOLD,
            symbol="BTC-USDT",
            strength=0.1,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
        )
        with pytest.raises(ValueError):
            await executor.execute_signal(signal, 0.1, "BTC-USDT")
