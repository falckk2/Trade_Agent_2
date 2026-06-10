"""
ISSUE-042: targeted regression tests for the fixes ISSUE-032 through ISSUE-040
that previously had no dedicated coverage.

Covered here:
- ISSUE-032: engine.close_all_positions() closes every open position and
  commits trade records via portfolio_manager.update([], balance)
- ISSUE-033: _await_fill position-check fallback marks a propagation-lagged
  market order FILLED instead of cancelling it
- ISSUE-035: _parse_order reads BloFin's actual field names
  (averagePrice / filledSize)
- ISSUE-037: shutdown-closed positions appear in trade history
- ISSUE-038: market orders with price='0' parse as price=None
- ISSUE-039/040: size and filledSize (contracts) are both converted to base
  units via contract_value

Covered elsewhere (no duplication):
- ISSUE-034: drawdown baseline persistence → tests/test_fable_007_drawdown_high_watermark.py
- ISSUE-036: fee tracking through to TradeRecord → tests/test_fable_006_close_fill_identity_matching.py
  and tests/test_fable_004_incremental_trade_persistence.py
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.exchange.blofin_exchange as exchange_module
from src.core.enums import OrderStatus, OrderType, Side, TimeFrame
from src.core.events import EventBus
from src.core.models import Order, Position
from src.engine.trading_engine import TradingEngine
from src.exchange.blofin_exchange import BloFinExchange
from src.execution.executor import OrderExecutor
from src.portfolio.manager import PortfolioManager


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
    return exc


def _position(pos_id="p1", symbol="BTC-USDT", side=Side.BUY):
    return Position(
        id=pos_id,
        symbol=symbol,
        side=side,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        realized_pnl=100.0,
        strategy_name="s1",
    )


_ORDER_ITEM = {
    "orderId": "o1",
    "instId": "BTC-USDT",
    "side": "buy",
    "orderType": "market",
    "size": "3",                 # contracts
    "filledSize": "3",           # contracts
    "price": "0",                # market order convention
    "averagePrice": "61889.7",
    "state": "filled",
    "fee": "-0.037",
    "createTime": "1760000000000",
}


class TestIssue035And038And039And040ParseOrder:
    def test_average_price_field_parsed(self, blofin):
        order = blofin._parse_order(_ORDER_ITEM)
        assert order.average_fill_price == pytest.approx(61889.7)

    def test_market_order_price_zero_parses_as_none(self, blofin):
        order = blofin._parse_order(_ORDER_ITEM)
        assert order.price is None

    def test_limit_price_preserved_when_nonzero(self, blofin):
        item = dict(_ORDER_ITEM, price="61000", orderType="limit")
        order = blofin._parse_order(item)
        assert order.price == pytest.approx(61000.0)

    def test_filled_quantity_converted_to_base_units(self, blofin):
        order = blofin._parse_order(_ORDER_ITEM)
        # 3 contracts * 0.001 BTC/contract
        assert order.filled_quantity == pytest.approx(0.003)

    def test_quantity_converted_to_base_units_consistent_with_filled(self, blofin):
        """ISSUE-040: quantity and filled_quantity must be in the same units."""
        order = blofin._parse_order(_ORDER_ITEM)
        assert order.quantity == pytest.approx(0.003)
        assert order.quantity == pytest.approx(order.filled_quantity)

    def test_fee_parsed_as_absolute_value(self, blofin):
        order = blofin._parse_order(_ORDER_ITEM)
        assert order.fee == pytest.approx(0.037)


class TestIssue033AwaitFillPositionFallback:
    @pytest.mark.asyncio
    async def test_position_existing_after_retries_marks_filled(self):
        """get_order returns None throughout, but a matching position exists —
        the market order filled before history propagated; must NOT cancel."""
        exchange = AsyncMock()
        exchange.get_order.return_value = None
        exchange.get_positions.return_value = [_position(side=Side.BUY)]

        executor = OrderExecutor(
            exchange=exchange, event_bus=EventBus(),
            fill_poll_interval=0, fill_max_retries=2,
        )
        order = Order(
            id="o1", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.PENDING,
        )
        result = await executor._await_fill(order, "BTC-USDT")

        assert result.status == OrderStatus.FILLED
        exchange.cancel_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_position_after_retries_cancels(self):
        exchange = AsyncMock()
        exchange.get_order.return_value = None
        exchange.get_positions.return_value = []
        exchange.cancel_order.return_value = True

        executor = OrderExecutor(
            exchange=exchange, event_bus=EventBus(),
            fill_poll_interval=0, fill_max_retries=2,
        )
        order = Order(
            id="o1", symbol="BTC-USDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=0.1, price=None,
            status=OrderStatus.PENDING,
        )
        result = await executor._await_fill(order, "BTC-USDT")

        assert result.status == OrderStatus.CANCELLED
        exchange.cancel_order.assert_awaited_once()


class TestIssue032And037ShutdownClose:
    @pytest.mark.asyncio
    async def test_closes_every_position_and_commits_trades(self, tmp_path):
        positions = [_position("p1"), _position("p2", symbol="ETH-USDT")]
        exchange = AsyncMock()
        exchange.get_positions.return_value = positions
        exchange.get_balance.return_value = {"total_equity": 50000.0, "available": 50000.0}

        order_executor = AsyncMock()
        portfolio_manager = PortfolioManager(data_dir=str(tmp_path))
        # Positions are known to the manager from a prior tick
        portfolio_manager.update(positions, {"total_equity": 50000.0, "available": 50000.0})

        engine = TradingEngine(
            exchange=exchange,
            data_provider=AsyncMock(),
            risk_manager=MagicMock(),
            order_executor=order_executor,
            portfolio_manager=portfolio_manager,
            event_bus=EventBus(),
            symbols=["BTC-USDT", "ETH-USDT"],
            timeframe=TimeFrame.H1,
        )
        await engine.close_all_positions()

        # ISSUE-032: every position close attempted
        assert order_executor.close_position.await_count == 2
        # ISSUE-037: closures recorded as trades (update([], balance) ran)
        trades = portfolio_manager.get_trade_history()
        assert {t.id for t in trades} == {"p1", "p2"}

    @pytest.mark.asyncio
    async def test_one_close_failure_does_not_block_others(self, tmp_path):
        positions = [_position("p1"), _position("p2", symbol="ETH-USDT")]
        exchange = AsyncMock()
        exchange.get_positions.return_value = positions
        exchange.get_balance.return_value = {"total_equity": 50000.0, "available": 50000.0}

        order_executor = AsyncMock()
        order_executor.close_position.side_effect = [RuntimeError("rejected"), MagicMock()]

        engine = TradingEngine(
            exchange=exchange,
            data_provider=AsyncMock(),
            risk_manager=MagicMock(),
            order_executor=order_executor,
            portfolio_manager=PortfolioManager(data_dir=str(tmp_path)),
            event_bus=EventBus(),
            symbols=["BTC-USDT", "ETH-USDT"],
            timeframe=TimeFrame.H1,
        )
        await engine.close_all_positions()
        assert order_executor.close_position.await_count == 2
