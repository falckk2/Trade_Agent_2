"""
FABLE-010: No backtesting capability.

Tests the Backtester's execution semantics with a scripted stub strategy and
deterministic candles:
- fills at next candle open (no look-ahead)
- fees charged per side, pnl net of fees (matching live TradeRecord)
- stop-loss exits at the trigger price when the next candle's range crosses it
- opposite signal closes then reopens (net mode)
- end-of-data force close
- stats come from the shared compute_performance_stats
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.engine import Backtester
from src.core.enums import Side, SignalType
from src.core.models import Candle, Signal
from src.strategies.interface import IStrategy


def _candles(prices: list[float], spread: float = 0.0) -> list[Candle]:
    """Flat candles at given open prices; high/low extended by ``spread``."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            timestamp=start + timedelta(minutes=5 * i),
            open=p,
            high=p + spread,
            low=p - spread,
            close=p,
            volume=100.0,
        )
        for i, p in enumerate(prices)
    ]


class ScriptedStrategy(IStrategy):
    """Emits a scripted signal per bar index (index into candles given to analyze)."""

    def __init__(self, script: dict[int, SignalType], strength: float = 1.0):
        self._script = script
        self._strength = strength

    @property
    def name(self) -> str:
        return "scripted"

    def analyze(self, candles: list[Candle]) -> Signal:
        bar = len(candles) - 1
        signal_type = self._script.get(bar, SignalType.HOLD)
        return Signal(
            signal_type=signal_type,
            symbol="",
            strength=self._strength if signal_type != SignalType.HOLD else 0.0,
            strategy_name=self.name,
            timestamp=candles[-1].timestamp,
        )

    def configure(self, params: dict) -> None:
        pass


def _bt(**kwargs):
    defaults = dict(
        initial_equity=10_000.0,
        position_pct=0.10,
        fee_rate=0.0,
        slippage_bps=0.0,
        stop_loss_pct=None,
        take_profit_pct=None,
        warmup=1,
    )
    defaults.update(kwargs)
    return Backtester(**defaults)


class TestExecutionSemantics:
    def test_long_close_round_trip_pnl(self):
        # LONG at bar 1 → fills at bar 2 open (100); CLOSE at bar 3 → fills
        # at bar 4 open (110). Notional 1000 @ 100 = 10 units → pnl 100.
        prices = [100, 100, 100, 105, 110, 110]
        strategy = ScriptedStrategy({1: SignalType.LONG, 3: SignalType.CLOSE})
        result = _bt().run(strategy, _candles(prices), symbol="TEST")

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.side == Side.BUY
        assert trade.entry_price == pytest.approx(100.0)
        assert trade.exit_price == pytest.approx(110.0)
        assert trade.pnl == pytest.approx(100.0)
        assert result.final_equity == pytest.approx(10_100.0)

    def test_fees_deducted_per_side(self):
        prices = [100, 100, 100, 100, 100, 100]
        strategy = ScriptedStrategy({1: SignalType.LONG, 3: SignalType.CLOSE})
        result = _bt(fee_rate=0.001).run(strategy, _candles(prices))

        trade = result.trades[0]
        # Flat price: gross 0; entry fee 1000*0.001=1, exit fee 1000*0.001=1
        assert trade.fee == pytest.approx(2.0)
        assert trade.pnl == pytest.approx(-2.0)

    def test_short_profits_when_price_falls(self):
        prices = [100, 100, 100, 95, 90, 90]
        strategy = ScriptedStrategy({1: SignalType.SHORT, 3: SignalType.CLOSE})
        result = _bt().run(strategy, _candles(prices))

        trade = result.trades[0]
        assert trade.side == Side.SELL
        assert trade.pnl == pytest.approx(0.10 * 10_000 / 100 * 10)  # 10 units * 10

    def test_opposite_signal_flips_position(self):
        prices = [100, 100, 100, 100, 100, 100, 100]
        strategy = ScriptedStrategy({1: SignalType.LONG, 3: SignalType.SHORT})
        result = _bt().run(strategy, _candles(prices))

        # LONG closed by the SHORT signal, SHORT force-closed at data end
        assert len(result.trades) == 2
        assert result.trades[0].side == Side.BUY
        assert result.trades[1].side == Side.SELL

    def test_stop_loss_exits_at_trigger_price(self):
        # LONG fills at 100 (bar 2 open). Bar 3 low touches 97 (spread 3) —
        # stop at 98 triggers, exit at exactly 98.
        prices = [100, 100, 100, 100, 100]
        candles = _candles(prices, spread=3.0)
        strategy = ScriptedStrategy({1: SignalType.LONG})
        result = _bt(stop_loss_pct=0.02).run(strategy, candles)

        trade = result.trades[0]
        assert trade.exit_price == pytest.approx(98.0)
        assert trade.pnl == pytest.approx(-2.0 * 10)  # 10 units, -2 each

    def test_no_lookahead_fill_uses_next_open(self):
        # Signal on bar 1; bar 1 close is 100 but bar 2 opens at 200 —
        # the fill must be 200, not 100.
        prices = [100, 100, 200, 200, 200]
        strategy = ScriptedStrategy({1: SignalType.LONG, 2: SignalType.CLOSE})
        result = _bt().run(strategy, _candles(prices))

        assert result.trades[0].entry_price == pytest.approx(200.0)

    def test_adverse_slippage_applied(self):
        prices = [100, 100, 100, 100, 100]
        strategy = ScriptedStrategy({1: SignalType.LONG, 2: SignalType.CLOSE})
        result = _bt(slippage_bps=10.0).run(strategy, _candles(prices))

        trade = result.trades[0]
        assert trade.entry_price == pytest.approx(100.1)  # buy fills higher
        assert trade.exit_price == pytest.approx(99.9)    # sell fills lower

    def test_open_position_closed_at_end_of_data(self):
        prices = [100, 100, 100, 100, 100]
        strategy = ScriptedStrategy({1: SignalType.LONG})
        result = _bt().run(strategy, _candles(prices))
        assert len(result.trades) == 1


class TestStats:
    def test_stats_match_shared_function(self):
        from src.portfolio.stats import compute_performance_stats

        prices = [100, 100, 100, 105, 110, 110]
        strategy = ScriptedStrategy({1: SignalType.LONG, 3: SignalType.CLOSE})
        result = _bt().run(strategy, _candles(prices))

        assert result.stats == compute_performance_stats(result.trades)
        assert result.stats["trade_count"] == 1
        assert result.stats["win_rate"] == 1.0

    def test_equity_curve_tracks_unrealized(self):
        prices = [100, 100, 100, 110, 120, 120]
        strategy = ScriptedStrategy({1: SignalType.LONG})
        result = _bt().run(strategy, _candles(prices))
        # Curve must rise while the long position gains
        values = [v for _, v in result.equity_curve]
        assert values[-1] > values[0]
