"""Durable log of executed signals + per-condition attribution (FABLE-018).

`SignalLogger` subscribes to SIGNAL_GENERATED events and appends one CSV row
per non-HOLD signal to ``data/signal_log.csv``. Trade history records the
*outcome* of a trade; this log records the *reason* — including webhook alert
metadata (e.g. which MarketCipher condition fired), which would otherwise be
lost once the engine's composite aggregation produces the final signal.

The module-level helpers join this log back to closed trades so reports can
answer "which alert condition actually makes money?".
"""

import asyncio
import csv
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from src.core.enums import EventType
from src.core.events import Event, EventBus
from src.core.models import TradeRecord

logger = logging.getLogger(__name__)

_CSV_HEADER = ["timestamp", "strategy_name", "symbol", "signal_type", "strength", "metadata"]


class SignalLogger:
    """Appends every executed (non-HOLD) signal to a crash-safe CSV."""

    def __init__(self, data_dir: str = "data") -> None:
        self._filepath = Path(data_dir) / "signal_log.csv"
        self._lock = threading.Lock()

    def attach(self, event_bus: EventBus) -> None:
        event_bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal)

    def _on_signal(self, event: Event) -> None:
        """Sync callback — offloads file I/O to a thread to avoid blocking
        the async event loop (ISSUE-046).

        The CSV write is small (~1 row) but synchronous file I/O inside
        EventBus.publish stalls every subsequent subscriber and can delay
        WebSocket ping scheduling. We offload to a thread via
        asyncio.run_coroutine_threadsafe when a running loop exists; otherwise
        fall back to a direct write (e.g. in sync test contexts).
        """
        signal = (event.payload or {}).get("signal")
        if signal is None:
            return
        row = [
            signal.timestamp.isoformat(),
            signal.strategy_name,
            signal.symbol,
            signal.signal_type.value,
            f"{signal.strength:.4f}",
            json.dumps(signal.metadata, default=str),
        ]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — write directly (test context)
            self._write_row(row)
            return
        loop.run_in_executor(None, self._write_row, row)

    def _write_row(self, row: list) -> None:
        """Append a single row to signal_log.csv (thread-safe via _lock)."""
        try:
            with self._lock:
                self._filepath.parent.mkdir(parents=True, exist_ok=True)
                is_new = not self._filepath.exists()
                with open(self._filepath, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if is_new:
                        writer.writerow(_CSV_HEADER)
                    writer.writerow(row)
        except Exception:
            logger.exception("Failed to append signal to %s", self._filepath)


def load_signal_log(path: str | Path) -> list[dict]:
    """Parse signal_log.csv into dicts with datetime timestamp and metadata dict."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            try:
                rows.append(
                    {
                        "timestamp": datetime.fromisoformat(raw["timestamp"]),
                        "strategy_name": raw["strategy_name"],
                        "symbol": raw["symbol"],
                        "signal_type": raw["signal_type"],
                        "strength": float(raw["strength"]),
                        "metadata": json.loads(raw["metadata"] or "{}"),
                    }
                )
            except (KeyError, ValueError, json.JSONDecodeError):
                logger.warning("Skipping malformed signal_log row: %r", raw)
    return rows


def extract_condition_events(
    log_rows: list[dict], strategy_name: str
) -> list[dict]:
    """Pull (timestamp, symbol, condition) events for one webhook strategy.

    The strategy's metadata may sit at the top level (strategy traded alone)
    or nested under ``children_signals`` (engine composited it with others).
    """
    events = []
    for row in log_rows:
        metadata = None
        if row["strategy_name"] == strategy_name:
            metadata = row["metadata"]
        else:
            child = row["metadata"].get("children_signals", {}).get(strategy_name)
            if child:
                metadata = child.get("metadata", {})
        if metadata is None:
            continue
        events.append(
            {
                "timestamp": row["timestamp"],
                "symbol": row["symbol"],
                "condition": metadata.get("condition", "(no condition)"),
            }
        )
    return events


def split_trades_by_condition(
    trades: list[TradeRecord],
    condition_events: list[dict],
    max_lag: timedelta = timedelta(minutes=10),
) -> dict[str, list[TradeRecord]]:
    """Group trades by the condition of the closest preceding signal event.

    A trade matches the latest event for the same symbol that fired no more
    than ``max_lag`` before the trade opened (signals are consumed within one
    engine tick, so the true lag is seconds). Unmatched trades land in
    ``"(unmatched)"`` rather than being dropped — a growing unmatched bucket
    means the log and history have diverged and the report can say so.
    """
    groups: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        best = None
        for event in condition_events:
            if event["symbol"] != trade.symbol:
                continue
            lag = trade.opened_at - event["timestamp"]
            if timedelta(0) <= lag <= max_lag and (
                best is None or event["timestamp"] > best["timestamp"]
            ):
                best = event
        key = best["condition"] if best else "(unmatched)"
        groups.setdefault(key, []).append(trade)
    return groups
