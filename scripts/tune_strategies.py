"""Parameter sweep for the configured strategy types using the backtester.

Splits local historical candles into an in-sample tuning window and an
out-of-sample validation window, sweeps a small parameter grid per strategy
type, ranks by in-sample net P&L, and reports how the top candidates hold up
out-of-sample. Helps replace the unvalidated defaults in
config/strategies.yaml (see FABLE-010 fix history).

Usage:
    python scripts/tune_strategies.py
    python scripts/tune_strategies.py --symbol ETH-USDT --timeframe 5m
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import Backtester
from src.core.models import Candle
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.sma_crossover import SMACrossoverStrategy


def load_candles(symbol: str, timeframe: str, start: datetime) -> list[Candle]:
    path = Path("data/historical") / symbol / f"{timeframe}.csv"
    candles = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if ts < start:
                continue
            candles.append(
                Candle(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return candles


def sma_grid():
    # slow capped at 150: the live engine passes candle_limit=200 candles and
    # SMACrossoverStrategy needs slow_period+1 — larger values never trade.
    for fast, slow in product([5, 10, 20, 30], [20, 30, 50, 100, 150]):
        if fast < slow:
            yield (
                f"sma {fast}/{slow}",
                SMACrossoverStrategy(name=f"sma_{fast}_{slow}", fast_period=fast, slow_period=slow),
            )


def rsi_grid():
    for period, (ob, os_) in product([7, 14, 21], [(70, 30), (75, 25), (80, 20)]):
        yield (
            f"rsi {period} {ob}/{os_}",
            RSIStrategy(name=f"rsi_{period}_{ob}_{os_}", period=period, overbought=ob, oversold=os_),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTC-USDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--start", default="2026-01-01",
                        help="In-sample window start (UTC date)")
    parser.add_argument("--split", default="2026-05-01",
                        help="In-sample/out-of-sample boundary (UTC date)")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    split = datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc)

    candles = load_candles(args.symbol, args.timeframe, start)
    insample = [c for c in candles if c.timestamp < split]
    outsample = [c for c in candles if c.timestamp >= split]
    print(f"\n{args.symbol} {args.timeframe}: in-sample {len(insample)} candles "
          f"({args.start}..{args.split}), out-of-sample {len(outsample)} candles\n")

    bt = Backtester(
        initial_equity=10_000.0, position_pct=0.05, fee_rate=0.0006,
        slippage_bps=1.0, stop_loss_pct=0.02, take_profit_pct=0.04,
    )

    results = []
    for label, strategy in [*sma_grid(), *rsi_grid()]:
        r_in = bt.run(strategy, insample, symbol=args.symbol)
        if r_in.stats["trade_count"] > 0:  # a combo that never trades is not a result
            results.append((label, strategy, r_in.stats))

    results.sort(key=lambda r: r[2]["net_pnl"], reverse=True)

    header = (f"{'Params':<16} {'IS trades':>9} {'IS Win%':>8} {'IS PnL':>9} {'IS PF':>6} "
              f"{'| OOS trades':>12} {'OOS Win%':>9} {'OOS PnL':>9} {'OOS PF':>7}")
    print(header)
    print("-" * len(header))
    for label, strategy, s_in in results[: args.top]:
        s_out = bt.run(strategy, outsample, symbol=args.symbol).stats
        pf_in = "inf" if s_in["profit_factor"] == float("inf") else f"{s_in['profit_factor']:.2f}"
        pf_out = "inf" if s_out["profit_factor"] == float("inf") else f"{s_out['profit_factor']:.2f}"
        print(f"{label:<16} {s_in['trade_count']:>9} {s_in['win_rate']*100:>7.1f}% "
              f"{s_in['net_pnl']:>9.2f} {pf_in:>6} "
              f"{s_out['trade_count']:>12} {s_out['win_rate']*100:>8.1f}% "
              f"{s_out['net_pnl']:>9.2f} {pf_out:>7}")

    # Also show the currently configured params for comparison
    print("\nCurrent config for comparison:")
    for label, strategy in [
        ("sma 10/30", SMACrossoverStrategy(name="cur_sma", fast_period=10, slow_period=30)),
        ("rsi 14 70/30", RSIStrategy(name="cur_rsi", period=14, overbought=70, oversold=30)),
    ]:
        s_in = bt.run(strategy, insample, symbol=args.symbol).stats
        s_out = bt.run(strategy, outsample, symbol=args.symbol).stats
        pf_in = "inf" if s_in["profit_factor"] == float("inf") else f"{s_in['profit_factor']:.2f}"
        pf_out = "inf" if s_out["profit_factor"] == float("inf") else f"{s_out['profit_factor']:.2f}"
        print(f"{label:<16} {s_in['trade_count']:>9} {s_in['win_rate']*100:>7.1f}% "
              f"{s_in['net_pnl']:>9.2f} {pf_in:>6} "
              f"{s_out['trade_count']:>12} {s_out['win_rate']*100:>8.1f}% "
              f"{s_out['net_pnl']:>9.2f} {pf_out:>7}")
    print()


if __name__ == "__main__":
    main()
