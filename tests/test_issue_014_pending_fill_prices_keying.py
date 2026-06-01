"""
ISSUE-014: _pending_fill_prices cache keyed by (symbol, side) was unsafe with
concurrent positions because back-to-back fills would overwrite each other.

Fix (2026-05-31): changed the value type from float to collections.deque[float]
(FIFO queue).  _on_order_filled now appends to the deque; _record_trade
pops the oldest price (popleft) — preserving all fill prices even when multiple
fills arrive for the same (symbol, side) key.

Tests verify:
1. _on_order_filled stores fill price in a deque keyed by (symbol, side)
2. _record_trade pops the oldest price (FIFO) and uses it as exit_price
3. Back-to-back fills are both preserved in the queue (no overwrite)
4. _record_trade correctly uses each price in order when two fills queue up

Note: Tests requiring live API data are marked skip.
"""

import collections

import pytest
from datetime import datetime, timezone

from src.core.enums import EventType, OrderStatus, OrderType, PositionStatus, Side
from src.core.events import Event, EventBus
from src.core.models import Order, Position
from src.portfolio.manager import PortfolioManager


def _order_filled_event(symbol: str, side: Side, price: float, order_id: str = "o1") -> Event:
    order = Order(
        id=order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=0.1,
        price=None,
        status=OrderStatus.FILLED,
        average_fill_price=price,
    )
    return Event(event_type=EventType.ORDER_FILLED, payload={"order": order})


def _pos(pos_id: str, symbol: str, side: Side, current_price: float = 51000.0) -> Position:
    return Position(
        id=pos_id,
        symbol=symbol,
        side=side,
        entry_price=50000.0,
        current_price=current_price,
        quantity=0.1,
        status=PositionStatus.OPEN,
    )


@pytest.fixture
def pm_with_bus(tmp_path):
    event_bus = EventBus()
    pm = PortfolioManager(data_dir=str(tmp_path), event_bus=event_bus)
    return pm, event_bus


class TestPendingFillPricesKeying:
    def test_on_order_filled_stores_price_in_deque(self, pm_with_bus):
        """ISSUE-014 fix: _on_order_filled stores fill price in a deque (not plain float)."""
        pm, event_bus = pm_with_bus
        event = _order_filled_event("BTC-USDT", Side.SELL, 51500.0)
        pm._on_order_filled(event)

        key = ("BTC-USDT", Side.SELL)
        assert key in pm._pending_fill_prices
        queue = pm._pending_fill_prices[key]
        assert isinstance(queue, collections.deque), "Should be a deque, not a plain float"
        assert len(queue) == 1
        assert queue[0] == pytest.approx(51500.0)

    def test_record_trade_uses_correct_close_side_key(self, pm_with_bus):
        """For a BUY position, the close order is a SELL — _record_trade pops (symbol, SELL)."""
        pm, _ = pm_with_bus
        # Pre-populate the deque as _on_order_filled would
        pm._pending_fill_prices[("BTC-USDT", Side.SELL)].append(51500.0)

        buy_pos = _pos("p1", "BTC-USDT", Side.BUY, current_price=51000.0)
        pm._record_trade(buy_pos)

        trades = pm.get_trade_history()
        assert len(trades) == 1
        assert trades[0].exit_price == pytest.approx(51500.0)

    def test_record_trade_falls_back_to_current_price_when_no_fill_price(self, tmp_path):
        """When no fill price is cached, exit_price falls back to current_price."""
        pm = PortfolioManager(data_dir=str(tmp_path))
        pos = _pos("p1", "BTC-USDT", Side.BUY, current_price=51234.0)
        pm._record_trade(pos)

        trades = pm.get_trade_history()
        assert trades[0].exit_price == pytest.approx(51234.0)

    def test_two_fills_same_key_both_preserved_in_queue(self, pm_with_bus):
        """
        ISSUE-014 fix: two SELL fills for BTC-USDT are both queued (not overwritten).
        Both prices are preserved; popleft() returns them in FIFO order.
        """
        pm, event_bus = pm_with_bus

        event1 = _order_filled_event("BTC-USDT", Side.SELL, 51000.0, order_id="o1")
        event2 = _order_filled_event("BTC-USDT", Side.SELL, 52000.0, order_id="o2")

        pm._on_order_filled(event1)
        pm._on_order_filled(event2)

        key = ("BTC-USDT", Side.SELL)
        queue = pm._pending_fill_prices[key]
        assert len(queue) == 2, "Both fill prices should be queued (not overwritten)"
        assert list(queue) == pytest.approx([51000.0, 52000.0])

    def test_record_trade_pops_oldest_price_first(self, pm_with_bus):
        """
        ISSUE-014 fix: when two fills are queued, the first close trade uses the
        oldest (first) fill price, and the second close trade uses the second price.
        """
        pm, _ = pm_with_bus
        pm._pending_fill_prices[("BTC-USDT", Side.SELL)].append(51000.0)
        pm._pending_fill_prices[("BTC-USDT", Side.SELL)].append(52000.0)

        pos1 = _pos("p1", "BTC-USDT", Side.BUY, current_price=50000.0)
        pos2 = _pos("p2", "BTC-USDT", Side.BUY, current_price=50000.0)

        pm._record_trade(pos1)
        pm._record_trade(pos2)

        trades = pm.get_trade_history()
        assert len(trades) == 2
        assert trades[0].exit_price == pytest.approx(51000.0), "Oldest fill price used first"
        assert trades[1].exit_price == pytest.approx(52000.0), "Second fill price used second"

    def test_empty_deque_removed_after_record_trade(self, pm_with_bus):
        """
        ISSUE-014 fix: after _record_trade pops the last price from a deque,
        the empty deque is removed from _pending_fill_prices to prevent memory
        growth from churned symbol/side pairs.
        """
        pm, _ = pm_with_bus

        # Pre-populate with a single fill price
        key = ("BTC-USDT", Side.SELL)
        pm._pending_fill_prices[key].append(51000.0)
        assert key in pm._pending_fill_prices

        pos = _pos("p1", "BTC-USDT", Side.BUY, current_price=50000.0)
        pm._record_trade(pos)

        # After popping the last price, the empty deque should be removed
        assert key not in pm._pending_fill_prices, (
            "Empty deque should be removed after last price is popped"
        )

    @pytest.mark.skip(reason="ISSUE-014 Inconclusive: verifying actual API field name "
                             "requires a live BloFin response — cannot test without credentials")
    def test_position_id_field_name_in_live_response(self):
        """Verify BloFin response uses 'positionId' field (requires live API call)."""
        pass
