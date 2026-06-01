"""Tests for core domain models."""

from datetime import datetime, timezone

from src.core.enums import (
    OrderStatus,
    OrderType,
    PositionStatus,
    Side,
    SignalType,
)
from src.core.models import (
    Candle,
    Order,
    PortfolioSnapshot,
    Position,
    Signal,
    TradeRecord,
)


class TestCandle:
    def test_candle_is_frozen(self):
        c = Candle(
            timestamp=datetime.now(tz=timezone.utc),
            open=100.0, high=110.0, low=90.0, close=105.0, volume=1000.0,
        )
        assert c.close == 105.0
        try:
            c.close = 200.0  # type: ignore
            assert False, "Should not allow mutation"
        except AttributeError:
            pass

    def test_candle_fields(self):
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        c = Candle(timestamp=ts, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        assert c.timestamp == ts
        assert c.open == 1.0
        assert c.high == 2.0
        assert c.low == 0.5
        assert c.close == 1.5
        assert c.volume == 100.0


class TestSignal:
    def test_signal_is_frozen(self):
        s = Signal(
            signal_type=SignalType.LONG,
            symbol="BTC-USDT",
            strength=0.8,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
        )
        assert s.strength == 0.8

    def test_signal_metadata_default(self):
        s = Signal(
            signal_type=SignalType.HOLD,
            symbol="ETH-USDT",
            strength=0.0,
            strategy_name="test",
            timestamp=datetime.now(tz=timezone.utc),
        )
        assert s.metadata == {}


class TestOrder:
    def test_order_mutable(self):
        o = Order(
            id="1",
            symbol="BTC-USDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            status=OrderStatus.PENDING,
        )
        o.status = OrderStatus.FILLED
        assert o.status == OrderStatus.FILLED

    def test_order_defaults(self):
        o = Order(
            id="2",
            symbol="BTC-USDT",
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=50000.0,
            status=OrderStatus.PENDING,
        )
        assert o.strategy_name == ""
        assert o.filled_quantity == 0.0
        assert o.filled_at is None
        assert o.average_fill_price is None


class TestPosition:
    def test_position_defaults(self):
        p = Position(
            id="p1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            current_price=51000.0,
            quantity=0.5,
        )
        assert p.unrealized_pnl == 0.0
        assert p.realized_pnl == 0.0
        assert p.status == PositionStatus.OPEN
        assert p.closed_at is None


class TestTradeRecord:
    def test_trade_record_frozen(self):
        now = datetime.now(tz=timezone.utc)
        t = TradeRecord(
            id="t1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            exit_price=51000.0,
            quantity=0.1,
            pnl=100.0,
            strategy_name="test",
            opened_at=now,
            closed_at=now,
            duration_seconds=3600.0,
        )
        assert t.pnl == 100.0


class TestPortfolioSnapshot:
    def test_snapshot_defaults(self):
        s = PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            total_equity=50000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
        assert s.positions == []
        assert s.strategy_pnl == {}
