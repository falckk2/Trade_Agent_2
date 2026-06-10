"""Offline backtester replaying historical candles through a strategy (FABLE-010).

Deliberately simple and synchronous — no event loop, no exchange:
- Signals are generated on candle close; fills execute at the NEXT candle's
  open (no look-ahead), adjusted by slippage.
- One net position at a time (matching BloFin net mode). An opposite signal
  closes the open position and opens the new one at the same fill price.
- Stop-loss/take-profit are checked against each candle's high/low before
  new signals, exiting at the trigger price. When both could trigger within
  one candle, the stop is assumed to fire first (conservative).
- Fees are charged per side as fee_rate * notional, mirroring how live
  TradeRecord.pnl is net of fees.

Stats come from compute_performance_stats — the same function the live
dashboard uses — so backtest and live numbers are directly comparable.
"""

from dataclasses import dataclass, field

from src.core.enums import Side, SignalType
from src.core.models import Candle, TradeRecord
from src.portfolio.stats import compute_performance_stats
from src.strategies.interface import IStrategy


@dataclass
class _OpenPosition:
    side: Side
    entry_price: float
    quantity: float
    entry_fee: float
    opened_at: object  # datetime


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    trades: list[TradeRecord]
    stats: dict
    final_equity: float
    initial_equity: float
    equity_curve: list[tuple] = field(default_factory=list)  # (timestamp, equity)


class Backtester:
    """Replays candles through an IStrategy and simulates net-mode execution."""

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        position_pct: float = 0.05,
        fee_rate: float = 0.0006,        # BloFin taker fee per side
        slippage_bps: float = 0.0,       # adverse slippage on every fill
        stop_loss_pct: float | None = 0.02,
        take_profit_pct: float | None = 0.04,
        warmup: int = 30,                # candles before first signal
        window: int = 200,               # candles passed to analyze() per bar,
                                         # matching the live engine's candle_limit
    ) -> None:
        if warmup < 1:
            raise ValueError("warmup must be >= 1")
        if window < warmup:
            raise ValueError("window must be >= warmup")
        self._initial_equity = initial_equity
        self._position_pct = position_pct
        self._fee_rate = fee_rate
        self._slippage = slippage_bps / 10_000.0
        self._stop_loss_pct = stop_loss_pct
        self._take_profit_pct = take_profit_pct
        self._warmup = warmup
        self._window = window

    def run(
        self, strategy: IStrategy, candles: list[Candle], symbol: str = ""
    ) -> BacktestResult:
        equity = self._initial_equity
        position: _OpenPosition | None = None
        trades: list[TradeRecord] = []
        equity_curve: list[tuple] = []
        trade_seq = 0

        for i in range(self._warmup, len(candles) - 1):
            next_candle = candles[i + 1]

            # 1. Protective exits against the next candle's range
            if position is not None:
                exit_price = self._check_protective_exit(position, next_candle)
                if exit_price is not None:
                    trade_seq += 1
                    equity = self._close(
                        position, exit_price, next_candle.timestamp,
                        equity, trades, strategy.name, symbol, trade_seq,
                    )
                    position = None

            # 2. Strategy signal on the trailing window ending at bar i
            #    (mirrors the live engine's candle_limit), executed at bar
            #    i+1's open
            signal = strategy.analyze(candles[max(0, i + 1 - self._window): i + 1])
            fill_price = next_candle.open

            if signal.signal_type == SignalType.CLOSE and position is not None:
                trade_seq += 1
                equity = self._close(
                    position,
                    self._slip(fill_price, _close_side(position.side)),
                    next_candle.timestamp,
                    equity, trades, strategy.name, symbol, trade_seq,
                )
                position = None
            elif signal.signal_type in (SignalType.LONG, SignalType.SHORT):
                side = Side.BUY if signal.signal_type == SignalType.LONG else Side.SELL
                if position is not None and position.side != side:
                    trade_seq += 1
                    equity = self._close(
                        position,
                        self._slip(fill_price, _close_side(position.side)),
                        next_candle.timestamp,
                        equity, trades, strategy.name, symbol, trade_seq,
                    )
                    position = None
                if position is None and signal.strength > 0:
                    entry = self._slip(fill_price, side)
                    notional = equity * self._position_pct * signal.strength
                    if notional > 0 and entry > 0:
                        position = _OpenPosition(
                            side=side,
                            entry_price=entry,
                            quantity=notional / entry,
                            entry_fee=notional * self._fee_rate,
                            opened_at=next_candle.timestamp,
                        )

            equity_curve.append(
                (next_candle.timestamp, equity + self._unrealized(position, next_candle.close))
            )

        # Close any position left open at the end of the data
        if position is not None:
            last = candles[-1]
            trade_seq += 1
            equity = self._close(
                position,
                self._slip(last.close, _close_side(position.side)),
                last.timestamp,
                equity, trades, strategy.name, symbol, trade_seq,
            )

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            trades=trades,
            stats=compute_performance_stats(trades),
            final_equity=equity,
            initial_equity=self._initial_equity,
            equity_curve=equity_curve,
        )

    def _check_protective_exit(
        self, position: _OpenPosition, candle: Candle
    ) -> float | None:
        """Return the exit price if SL/TP triggers within this candle (stop first)."""
        if position.side == Side.BUY:
            if self._stop_loss_pct is not None:
                stop = position.entry_price * (1 - self._stop_loss_pct)
                if candle.low <= stop:
                    return stop
            if self._take_profit_pct is not None:
                tp = position.entry_price * (1 + self._take_profit_pct)
                if candle.high >= tp:
                    return tp
        else:
            if self._stop_loss_pct is not None:
                stop = position.entry_price * (1 + self._stop_loss_pct)
                if candle.high >= stop:
                    return stop
            if self._take_profit_pct is not None:
                tp = position.entry_price * (1 - self._take_profit_pct)
                if candle.low <= tp:
                    return tp
        return None

    def _close(
        self, position, exit_price, timestamp,
        equity, trades, strategy_name, symbol, seq,
    ) -> float:
        gross = _direction(position.side) * (
            exit_price - position.entry_price
        ) * position.quantity
        exit_fee = exit_price * position.quantity * self._fee_rate
        total_fee = position.entry_fee + exit_fee
        net = gross - total_fee
        trades.append(
            TradeRecord(
                id=f"bt_{seq}",
                symbol=symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                pnl=net,
                strategy_name=strategy_name,
                opened_at=position.opened_at,
                closed_at=timestamp,
                duration_seconds=(timestamp - position.opened_at).total_seconds(),
                fee=total_fee,
            )
        )
        return equity + net

    def _slip(self, price: float, side: Side) -> float:
        """Apply adverse slippage: buys fill higher, sells fill lower."""
        if side == Side.BUY:
            return price * (1 + self._slippage)
        return price * (1 - self._slippage)

    def _unrealized(self, position: _OpenPosition | None, price: float) -> float:
        if position is None:
            return 0.0
        return _direction(position.side) * (price - position.entry_price) * position.quantity


def _direction(side: Side) -> float:
    return 1.0 if side == Side.BUY else -1.0


def _close_side(side: Side) -> Side:
    return Side.SELL if side == Side.BUY else Side.BUY
