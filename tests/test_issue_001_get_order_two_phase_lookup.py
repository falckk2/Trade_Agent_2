"""
ISSUE-001: BloFinExchange.get_order() uses two-phase lookup (active orders → history fallback)
instead of the nonexistent client.trading.get_order().

Tests verify:
1. Active orders are searched first by orderId
2. Order history is searched as fallback when not found in active orders
3. None is returned when the order is not found in either list
4. SDK exceptions in either phase are caught and do not propagate
"""

import pytest
from unittest.mock import MagicMock, patch

import blofin.constants
import blofin.utils

from src.core.enums import OrderStatus, OrderType, Side
from src.exchange.blofin_exchange import BloFinExchange, _active_instances
import src.exchange.blofin_exchange as exchange_module


@pytest.fixture(autouse=True)
def reset_active_instances():
    """Reset the global singleton counter before each test."""
    original = exchange_module._active_instances
    exchange_module._active_instances = 0
    yield
    exchange_module._active_instances = 0


@pytest.fixture
def exchange():
    """Construct a BloFinExchange with a mock SDK client (no real network)."""
    exc = BloFinExchange(api_key="k", secret="s", passphrase="p", demo_mode=False)
    # Inject a mock client directly so we don't need connect()
    exc._client = MagicMock()
    return exc


_ACTIVE_ORDER_ITEM = {
    "orderId": "ord_active",
    "instId": "BTC-USDT",
    "side": "buy",
    "orderType": "market",
    "size": "0.1",
    "state": "live",
    "filledSize": "0",
}

_HISTORY_ORDER_ITEM = {
    "orderId": "ord_history",
    "instId": "BTC-USDT",
    "side": "sell",
    "orderType": "market",
    "size": "0.2",
    "state": "filled",
    "filledSize": "0.2",
    "averagePrice": "51000.0",
}


class TestGetOrderTwoPhaseLooup:
    @pytest.mark.asyncio
    async def test_returns_order_from_active_orders(self, exchange):
        """Fast path: order found in get_active_orders response."""
        exchange.client.trading.get_active_orders.return_value = {
            "data": [_ACTIVE_ORDER_ITEM]
        }
        exchange.client.trading.get_order_history.return_value = {"data": []}

        order = await exchange.get_order("ord_active", "BTC-USDT")

        assert order is not None
        assert order.id == "ord_active"
        assert order.side == Side.BUY
        # get_order_history should NOT be called — active orders hit first
        exchange.client.trading.get_order_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_order_from_history_when_not_in_active(self, exchange):
        """Fallback path: order not in active orders, found in get_order_history."""
        exchange.client.trading.get_active_orders.return_value = {"data": []}
        exchange.client.trading.get_order_history.return_value = {
            "data": [_HISTORY_ORDER_ITEM]
        }

        order = await exchange.get_order("ord_history", "BTC-USDT")

        assert order is not None
        assert order.id == "ord_history"
        assert order.status == OrderStatus.FILLED
        assert order.average_fill_price == 51000.0

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found_in_either(self, exchange):
        """Returns None when the order does not appear in active or history."""
        exchange.client.trading.get_active_orders.return_value = {"data": []}
        exchange.client.trading.get_order_history.return_value = {"data": []}

        order = await exchange.get_order("ord_missing", "BTC-USDT")

        assert order is None

    @pytest.mark.asyncio
    async def test_active_orders_exception_falls_through_to_history(self, exchange):
        """An exception from get_active_orders does not crash; history is still tried."""
        exchange.client.trading.get_active_orders.side_effect = RuntimeError("SDK error")
        exchange.client.trading.get_order_history.return_value = {
            "data": [_HISTORY_ORDER_ITEM]
        }

        order = await exchange.get_order("ord_history", "BTC-USDT")

        assert order is not None
        assert order.id == "ord_history"

    @pytest.mark.asyncio
    async def test_both_phases_exception_returns_none(self, exchange):
        """Exceptions from both SDK calls result in None, not a crash."""
        exchange.client.trading.get_active_orders.side_effect = RuntimeError("active fail")
        exchange.client.trading.get_order_history.side_effect = RuntimeError("history fail")

        order = await exchange.get_order("ord_x", "BTC-USDT")

        assert order is None

    @pytest.mark.asyncio
    async def test_active_order_id_matching_is_exact(self, exchange):
        """Only the order whose orderId matches is returned, not a different one."""
        other_item = dict(_ACTIVE_ORDER_ITEM)
        other_item["orderId"] = "ord_different"
        exchange.client.trading.get_active_orders.return_value = {"data": [other_item]}
        exchange.client.trading.get_order_history.return_value = {"data": []}

        order = await exchange.get_order("ord_active", "BTC-USDT")

        assert order is None

    @pytest.mark.asyncio
    async def test_parse_order_helper_used_by_get_order(self, exchange):
        """_parse_order builds correct Order dataclass from raw dict."""
        exchange.client.trading.get_active_orders.return_value = {
            "data": [_ACTIVE_ORDER_ITEM]
        }
        exchange.client.trading.get_order_history.return_value = {"data": []}

        order = await exchange.get_order("ord_active", "BTC-USDT")

        assert order.symbol == "BTC-USDT"
        assert order.order_type == OrderType.MARKET
        assert order.quantity == 0.1
        assert order.status == OrderStatus.PENDING  # "live" maps to PENDING
