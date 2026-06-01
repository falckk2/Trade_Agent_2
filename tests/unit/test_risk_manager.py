"""Tests for RiskManager."""

from datetime import datetime, timezone

from src.core.enums import SignalType, Side
from src.core.models import PortfolioSnapshot, Position, Signal
from src.risk.manager import RiskManager


def _signal(signal_type=SignalType.LONG, strength=0.8):
    return Signal(
        signal_type=signal_type,
        symbol="BTC-USDT",
        strength=strength,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _portfolio(equity=50000.0, positions=None):
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=equity,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=positions or [],
    )


class TestValidateSignal:
    def test_hold_rejected(self):
        rm = RiskManager()
        assert rm.validate_signal(_signal(SignalType.HOLD), _portfolio()) is False

    def test_close_always_allowed(self):
        rm = RiskManager()
        assert rm.validate_signal(_signal(SignalType.CLOSE), _portfolio()) is True

    def test_long_accepted_normally(self):
        rm = RiskManager()
        assert rm.validate_signal(_signal(SignalType.LONG), _portfolio()) is True

    def test_short_accepted_normally(self):
        rm = RiskManager()
        assert rm.validate_signal(_signal(SignalType.SHORT), _portfolio()) is True

    def test_rejected_at_max_drawdown(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        # First call sets initial equity
        rm.validate_signal(_signal(), _portfolio(equity=50000.0))
        # Now equity dropped 15%
        result = rm.validate_signal(_signal(), _portfolio(equity=42000.0))
        assert result is False

    def test_rejected_at_max_exposure(self):
        rm = RiskManager(max_exposure_pct=0.20)
        pos = Position(
            id="p1",
            symbol="BTC-USDT",
            side=Side.BUY,
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.5,  # 25000 value
        )
        # 25000 / 50000 = 50% exposure > 20%
        portfolio = _portfolio(equity=50000.0, positions=[pos])
        result = rm.validate_signal(_signal(), portfolio)
        assert result is False


class TestPositionSize:
    def test_basic_sizing(self):
        rm = RiskManager(max_position_pct=0.05)
        size = rm.calculate_position_size(
            _signal(strength=1.0), _portfolio(equity=50000.0), current_price=50000.0
        )
        # 5% of 50000 = 2500, at 50000/coin = 0.05
        assert abs(size - 0.05) < 0.001

    def test_scales_with_strength(self):
        rm = RiskManager(max_position_pct=0.10)
        full = rm.calculate_position_size(
            _signal(strength=1.0), _portfolio(), current_price=50000.0
        )
        half = rm.calculate_position_size(
            _signal(strength=0.5), _portfolio(), current_price=50000.0
        )
        assert abs(half - full * 0.5) < 0.001

    def test_zero_price_returns_zero(self):
        rm = RiskManager()
        size = rm.calculate_position_size(_signal(), _portfolio(), current_price=0.0)
        assert size == 0.0

    def test_zero_equity_returns_zero(self):
        rm = RiskManager()
        size = rm.calculate_position_size(
            _signal(), _portfolio(equity=0.0), current_price=50000.0
        )
        assert size == 0.0


class TestShouldStopOut:
    def _position(self, side, entry, current):
        return Position(
            id="p1", symbol="BTC-USDT", side=side,
            entry_price=entry, current_price=current, quantity=0.1,
        )

    def test_long_below_stop_triggers(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        pos = self._position(Side.BUY, 50000.0, 48999.0)  # below 49000
        assert rm.should_stop_out(pos) is True

    def test_long_above_stop_no_trigger(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        pos = self._position(Side.BUY, 50000.0, 49500.0)
        assert rm.should_stop_out(pos) is False

    def test_short_above_stop_triggers(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        pos = self._position(Side.SELL, 50000.0, 51001.0)  # above 51000
        assert rm.should_stop_out(pos) is True

    def test_short_below_stop_no_trigger(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        pos = self._position(Side.SELL, 50000.0, 50500.0)
        assert rm.should_stop_out(pos) is False


class TestShouldTakeProfit:
    def _position(self, side, entry, current):
        return Position(
            id="p1", symbol="BTC-USDT", side=side,
            entry_price=entry, current_price=current, quantity=0.1,
        )

    def test_long_above_tp_triggers(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        pos = self._position(Side.BUY, 50000.0, 52001.0)  # above 52000
        assert rm.should_take_profit(pos) is True

    def test_long_below_tp_no_trigger(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        pos = self._position(Side.BUY, 50000.0, 51000.0)
        assert rm.should_take_profit(pos) is False

    def test_short_below_tp_triggers(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        pos = self._position(Side.SELL, 50000.0, 47999.0)  # below 48000
        assert rm.should_take_profit(pos) is True

    def test_short_above_tp_no_trigger(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        pos = self._position(Side.SELL, 50000.0, 49000.0)
        assert rm.should_take_profit(pos) is False


class TestStopLossTakeProfit:
    def test_long_stop_loss(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        sl = rm.get_stop_loss(_signal(SignalType.LONG), 50000.0)
        assert sl == 49000.0

    def test_short_stop_loss(self):
        rm = RiskManager(default_stop_loss_pct=0.02)
        sl = rm.get_stop_loss(_signal(SignalType.SHORT), 50000.0)
        assert sl == 51000.0

    def test_long_take_profit(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        tp = rm.get_take_profit(_signal(SignalType.LONG), 50000.0)
        assert tp == 52000.0

    def test_short_take_profit(self):
        rm = RiskManager(default_take_profit_pct=0.04)
        tp = rm.get_take_profit(_signal(SignalType.SHORT), 50000.0)
        assert tp == 48000.0
