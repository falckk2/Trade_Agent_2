"""Per-strategy performance report: live results vs backtest expectation.

The learning loop's measurement step (FABLE-018). For each strategy in
config/strategies.yaml this prints:
- live stats from data/trade_history.csv (all time / last 30d / last 7d)
- a backtest of the SAME parameters over the SAME recent window, so live
  execution can be compared against simulated expectation

Divergence is the signal to act: live well below sim → check execution
(slippage, fills, fees); both negative → retune via scripts/tune_strategies.py
or disable the strategy in strategies.yaml.

Usage:
    python scripts/performance_report.py
    python scripts/performance_report.py --days 14   # comparison window
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.backtest.engine import Backtester
from src.core.models import Candle
from src.portfolio.manager import PortfolioManager
from src.portfolio.signal_log import (
    extract_condition_events,
    load_signal_log,
    split_trades_by_condition,
)
from src.portfolio.stats import compute_performance_stats
from src.strategies.factory import StrategyFactory


def _fmt(stats: dict) -> str:
    pf = stats["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"{stats['trade_count']:>6} {stats['win_rate']*100:>6.1f}% "
            f"{stats['net_pnl']:>10.2f} {stats['total_fees']:>8.2f} {pf_s:>6}")


def load_candles(symbol: str, timeframe: str, start: datetime) -> list[Candle]:
    path = Path("data/historical") / symbol / f"{timeframe}.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if ts >= start:
                out.append(Candle(
                    timestamp=ts,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--strategies", default="config/strategies.yaml")
    parser.add_argument("--days", type=int, default=30,
                        help="live-vs-backtest comparison window")
    args = parser.parse_args()

    with open(args.config) as f:
        timeframe = yaml.safe_load(f).get("engine", {}).get("timeframe", "1H")
    with open(args.strategies) as f:
        entries = yaml.safe_load(f).get("strategies", [])

    pm = PortfolioManager(data_dir=args.data_dir)
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(days=args.days)

    factory = StrategyFactory()
    factory.create_from_config(entries)
    backtester = Backtester(
        initial_equity=10_000.0, position_pct=0.05, fee_rate=0.0006,
        slippage_bps=1.0, stop_loss_pct=0.02, take_profit_pct=0.04,
    )

    header = f"{'':<28} {'trades':>6} {'win%':>7} {'net pnl':>10} {'fees':>8} {'PF':>6}"
    print(f"\nPerformance report — {now:%Y-%m-%d %H:%M} UTC, timeframe {timeframe}\n")

    for entry in entries:
        name = entry["name"]
        # Substring match so trades attributed to "composite[a,b]" count for
        # every contributing strategy (one symbol covered by >1 strategies).
        trades_all = [
            t for t in pm.get_trade_history() if name in (t.strategy_name or "")
        ]
        print(f"=== {name} ({entry['type']}, "
              f"{'ENABLED' if entry.get('enabled') else 'disabled'}) ===")
        print(header)
        for label, cutoff_days in (("live: all time", None), ("live: 30d", 30), ("live: 7d", 7)):
            trades = trades_all if cutoff_days is None else [
                t for t in trades_all
                if t.closed_at >= now - timedelta(days=cutoff_days)
            ]
            print(f"{label:<28} {_fmt(compute_performance_stats(trades))}")

        if entry.get("type") == "webhook":
            # Webhook strategies cannot be simulated; instead break live
            # results down by the alert condition that triggered each trade
            # (FABLE-018 — "which MarketCipher signal earns its keep?").
            events = extract_condition_events(
                load_signal_log(Path(args.data_dir) / "signal_log.csv"), name
            )
            if not trades_all:
                print(f"{'by condition':<28} no trades attributed yet")
            else:
                for condition, group in sorted(
                    split_trades_by_condition(trades_all, events).items()
                ):
                    print(f"{f'condition: {condition}':<28} "
                          f"{_fmt(compute_performance_stats(group))}")
        else:
            strategy = factory.get_instance(name)
            for symbol in entry.get("symbols", []):
                candles = load_candles(symbol, timeframe, window_start)
                if strategy and len(candles) > 60:
                    result = backtester.run(strategy, candles, symbol=symbol)
                    print(f"{f'sim: {args.days}d {symbol}':<28} {_fmt(result.stats)}")
                else:
                    print(f"{f'sim: {symbol}':<28} insufficient candle data")
        print()

    total = compute_performance_stats(pm.get_trade_history())
    print(f"{'TOTAL (live, all time)':<28} {_fmt(total)}\n")


if __name__ == "__main__":
    main()
