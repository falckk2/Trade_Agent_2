"""
FABLE-006: Fee/fill-price deques keyed by (symbol, side) drifted permanently
after any missed match.

The pairing was positional: nothing tied a queued fill to the order/position
it belonged to, so one missed close detection shifted every later trade's
exit price and fee onto the wrong record — silently and forever.

Fix: close fills (ORDER_FILLED events that carry a `position` in the
payload, as published by OrderExecutor.close_position) are now cached by
position id and consumed by identity in _record_trade. The positional
queues remain only as fallback for fills without a position (entry orders;
flips arrive as opposite-side entries). Hardening: queue depth > 4 logs a
warning, and save_trade_history warns about leftover unmatched fills.
"""

import logging

import pytest
from datetime import datetime, timezone

from src.core.enums import EventType, OrderStatus, OrderType, PositionStatus, Side
from src.core.events import Event, EventBus
from src.core.models import Order, Position
from src.portfolio.manager import PortfolioManager


def _order(symbol="BTC-USDT", side=Side.SELL, price=51500.0, fee=0.5, order_id="o1"):
    return Order(
        id=order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=0.1,
        price=None,
        status=OrderStatus.FILLED,
        average_fill_price=price,
        fee=fee,
    )


def _pos(pos_id="p1", symbol="BTC-USDT", side=Side.BUY, current_price=51000.0):
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
def pm(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestIdentityMatching:
    def test_close_fill_with_position_cached_by_id(self, pm):
        position = _pos("p1")
        event = Event(
            event_type=EventType.ORDER_FILLED,
            payload={"order": _order(price=51500.0, fee=0.5), "position": position},
        )
        pm._on_order_filled(event)

        assert pm._pending_close_fills["p1"] == (51500.0, 0.5)
        # Identity-cached fills must NOT also enter the positional queue
        assert ("BTC-USDT", Side.SELL) not in pm._pending_fill_prices

    def test_record_trade_consumes_identity_fill(self, pm):
        pm._pending_close_fills["p1"] = (51500.0, 0.5)
        pm._record_trade(_pos("p1"))

        trade = pm.get_trade_history()[0]
        assert trade.exit_price == pytest.approx(51500.0)
        assert trade.fee == pytest.approx(0.5)
        assert "p1" not in pm._pending_close_fills

    def test_wrong_position_cannot_steal_identity_fill(self, pm):
        """The core FABLE-006 failure: a fill for one position must never be
        consumed by a different position's trade record."""
        pm._pending_close_fills["p1"] = (51500.0, 0.5)

        # A DIFFERENT position on the same symbol/side closes first
        pm._record_trade(_pos("p2", current_price=49000.0))
        other_trade = pm.get_trade_history()[0]
        assert other_trade.exit_price == pytest.approx(49000.0)  # fallback, not 51500

        # p1's fill is still intact for p1
        pm._record_trade(_pos("p1"))
        p1_trade = pm.get_trade_history()[1]
        assert p1_trade.exit_price == pytest.approx(51500.0)

    def test_fill_without_position_still_uses_queue(self, pm):
        """Entry/flip fills (no position in payload) keep the FIFO queue path."""
        event = Event(
            event_type=EventType.ORDER_FILLED,
            payload={"order": _order(price=52000.0)},
        )
        pm._on_order_filled(event)
        assert list(pm._pending_fill_prices[("BTC-USDT", Side.SELL)]) == [52000.0]

        pm._record_trade(_pos("p1"))
        assert pm.get_trade_history()[0].exit_price == pytest.approx(52000.0)

    def test_position_with_empty_id_falls_back_to_queue(self, pm):
        """positionId missing from the API (ISSUE-019) → no identity collisions."""
        position = _pos(pos_id="")
        event = Event(
            event_type=EventType.ORDER_FILLED,
            payload={"order": _order(price=51500.0), "position": position},
        )
        pm._on_order_filled(event)
        assert "" not in pm._pending_close_fills
        assert list(pm._pending_fill_prices[("BTC-USDT", Side.SELL)]) == [51500.0]


class TestDriftVisibility:
    def test_deep_queue_logs_warning(self, pm, caplog):
        with caplog.at_level(logging.WARNING):
            for i in range(5):
                pm._on_order_filled(
                    Event(
                        event_type=EventType.ORDER_FILLED,
                        payload={"order": _order(order_id=f"o{i}")},
                    )
                )
        assert any("depth 5" in r.message for r in caplog.records)

    def test_leftover_fills_warned_at_save(self, pm, caplog):
        pm._pending_close_fills["p_orphan"] = (50000.0, 0.1)
        pm._pending_fill_prices[("BTC-USDT", Side.SELL)].append(51000.0)
        with caplog.at_level(logging.WARNING):
            pm.save_trade_history()
        assert any("never matched" in r.message for r in caplog.records)

    def test_no_warning_when_caches_empty(self, pm, caplog):
        with caplog.at_level(logging.WARNING):
            pm.save_trade_history()
        assert not any("never matched" in r.message for r in caplog.records)
