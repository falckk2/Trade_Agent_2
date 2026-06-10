"""Run the configured strategies against local historical candles (FABLE-010).

Reads candles from data/historical/<symbol>/<timeframe>.csv (produced by
scripts/fetch_historical_data.py), builds strategies from
config/strategies.yaml, replays each through src/backtest/engine.Backtester,
and prints a per-strategy performance table using the same stats function as
the live dashboard.

Usage
-----
    python scripts/run_backtest.py                          # all configured strategies
    python scripts/run_backtest.py --timeframe 5m --days 90
    python scripts/run_backtest.py --strategy rsi_btc
    python scripts/run_backtest.py --fee-rate 0.0006 --slippage-bps 2
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as `python scripts/run_backtest.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.backtest.engine import Backtester
from src.core.models import Candle
from src.strategies.factory import StrategyFactory


def load_candles(symbol: str, timeframe: str, days: int | None) -> list[Candle]:
    path = Path("data/historical") / symbol / f"{timeframe}.csv"
    if not path.exists():
        print(f"  [skip] no historical data at {path} — run scripts/fetch_historical_data.py")
        return []
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=days) if days else None
    )
    candles = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if cutoff and ts < cutoff:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/strategies.yaml")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--days", type=int, default=90,
                        help="Backtest window in days (0 = all data)")
    parser.add_argument("--strategy", default=None,
                        help="Only run the named strategy")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--position-pct", type=float, default=0.05)
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--stop-loss-pct", type=float, default=0.02)
    parser.add_argument("--take-profit-pct", type=float, default=0.04)
    args = parser.parse_args()

    with open(args.config) as f:
        entries = yaml.safe_load(f).get("strategies", [])
    if args.strategy:
        entries = [e for e in entries if e["name"] == args.strategy]
        if not entries:
            sys.exit(f"Strategy '{args.strategy}' not found in {args.config}")

    factory = StrategyFactory()
    factory.create_from_config(entries)

    backtester = Backtester(
        initial_equity=args.initial_equity,
        position_pct=args.position_pct,
        fee_rate=args.fee_rate,
        slippage_bps=args.slippage_bps,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
    )

    header = (
        f"{'Strategy':<22} {'Symbol':<10} {'Trades':>6} {'Win%':>6} "
        f"{'Net PnL':>12} {'Fees':>10} {'PF':>6} {'MaxLossStreak':>14}"
    )
    print(f"\nBacktest: {args.timeframe} candles, last {args.days or 'all'} days, "
          f"fee {args.fee_rate*100:.3f}%/side, slippage {args.slippage_bps} bps\n")
    print(header)
    print("-" * len(header))

    for entry in entries:
        strategy = factory.get_instance(entry["name"])
        if strategy is None:
            continue
        for symbol in entry.get("symbols", []):
            candles = load_candles(symbol, args.timeframe, args.days or None)
            if len(candles) < 50:
                print(f"{entry['name']:<22} {symbol:<10} insufficient data ({len(candles)} candles)")
                continue
            result = backtester.run(strategy, candles, symbol=symbol)
            s = result.stats
            pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
            print(
                f"{entry['name']:<22} {symbol:<10} {s['trade_count']:>6} "
                f"{s['win_rate']*100:>5.1f}% {s['net_pnl']:>11,.2f} "
                f"{s['total_fees']:>10,.2f} {pf:>6} {s['max_consecutive_losses']:>14}"
            )
    print()


if __name__ == "__main__":
    main()
