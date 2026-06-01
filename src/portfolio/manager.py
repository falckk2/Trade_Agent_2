import collections
import csv
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.core.enums import EventType, PositionStatus, Side
from src.core.events import Event, EventBus
from src.core.models import PortfolioSnapshot, Position, TradeRecord
from src.portfolio.interface import IPortfolioManager

logger = logging.getLogger(__name__)


class PortfolioManager(IPortfolioManager):
    """Tracks positions, P&L per strategy, and trade history."""

    def __init__(self, data_dir: str = "data", event_bus: EventBus | None = None) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()  # Reentrant: _build_snapshot calls get_all_strategy_names
        self._positions: list[Position] = []
        self._balance: dict[str, float] = {"total_equity": 0.0, "available": 0.0}
        self._trade_history: list[TradeRecord] = []
        self._snapshots: list[PortfolioSnapshot] = []
        self._strategy_realized_pnl: dict[str, float] = {}

        # Previous positions for detecting closes (id -> Position)
        self._prev_positions: dict[str, Position] = {}

        # ISSUE-014 fix: changed from dict[(symbol, side), float] to
        # dict[(symbol, side), deque[float]] so that back-to-back fills for the
        # same symbol/side are queued rather than overwriting each other.
        # _record_trade pops the oldest price (FIFO) — matching the order in
        # which positions are recorded.  The close order for a BUY position
        # arrives as a SELL, and vice versa.
        self._pending_fill_prices: dict[tuple[str, Side], collections.deque[float]] = (
            collections.defaultdict(collections.deque)
        )

        # Tracks the cumulative realized_pnl already recorded for each position.
        # Used to compute the *incremental* realized PnL on each flip so that
        # BloFin's cumulative realized_pnl field is not double-counted across flips.
        self._last_recorded_realized_pnl: dict[str, float] = {}

        if event_bus is not None:
            event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)

        self._load_trade_history()

    def update(
        self, positions: list[Position], balance: dict[str, float]
    ) -> None:
        with self._lock:
            self._balance = balance

            current = {p.id: p for p in positions}

            # Detect fully closed positions (ID disappeared)
            for pos_id, prev_pos in self._prev_positions.items():
                if pos_id not in current:
                    logger.debug(
                        "Detected CLOSE: position id=%s symbol=%s side=%s — recording trade",
                        pos_id, prev_pos.symbol, prev_pos.side.value,
                    )
                    self._record_trade(prev_pos)
                    # Clean up the PnL watermark — position is gone
                    self._last_recorded_realized_pnl.pop(pos_id, None)

            # Detect side-flipped positions (same ID, BloFin net mode)
            for pos_id, pos in current.items():
                if pos_id in self._prev_positions:
                    prev = self._prev_positions[pos_id]
                    if prev.side != pos.side:
                        logger.debug(
                            "Detected FLIP: position id=%s symbol=%s %s->%s "
                            "prev.realized=%.4f curr.realized=%.4f",
                            pos_id, pos.symbol, prev.side.value, pos.side.value,
                            prev.realized_pnl, pos.realized_pnl,
                        )
                        self._record_trade(prev)

            self._positions = list(current.values())
            self._prev_positions = current

            # Take snapshot
            snapshot = self._build_snapshot()
            self._snapshots.append(snapshot)

            # Keep snapshots manageable
            if len(self._snapshots) > 10000:
                self._snapshots = self._snapshots[-10000:]

    def get_snapshot(self) -> PortfolioSnapshot:
        with self._lock:
            if self._snapshots:
                return self._snapshots[-1]
            return self._build_snapshot()

    def get_strategy_pnl(self, strategy_name: str) -> float:
        with self._lock:
            realized = self._strategy_realized_pnl.get(strategy_name, 0.0)
            unrealized = sum(
                p.unrealized_pnl
                for p in self._positions
                if p.strategy_name == strategy_name
            )
            return realized + unrealized

    def get_trade_history(
        self, strategy_name: str | None = None
    ) -> list[TradeRecord]:
        with self._lock:
            if strategy_name is None:
                return list(self._trade_history)
            return [
                t for t in self._trade_history if t.strategy_name == strategy_name
            ]

    def get_snapshots(self) -> list[PortfolioSnapshot]:
        with self._lock:
            return list(self._snapshots)

    def get_all_strategy_names(self) -> list[str]:
        with self._lock:
            names: set[str] = set(self._strategy_realized_pnl.keys())
            for p in self._positions:
                if p.strategy_name:
                    names.add(p.strategy_name)
            return sorted(names)

    def save_trade_history(self) -> None:
        filepath = self._data_dir / "trade_history.csv"
        # Write to a temp file first, then atomically replace the real file so
        # that a process kill mid-write cannot corrupt the CSV (POSIX rename is
        # atomic on the same filesystem).
        tmp_path = filepath.with_suffix(".csv.tmp")
        with self._lock:
            trades_snapshot = list(self._trade_history)
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "id", "symbol", "side", "entry_price", "exit_price",
                    "quantity", "pnl", "strategy_name", "opened_at",
                    "closed_at", "duration_seconds",
                ])
                for t in trades_snapshot:
                    writer.writerow([
                        t.id, t.symbol, t.side.value, t.entry_price,
                        t.exit_price, t.quantity, t.pnl, t.strategy_name or "",
                        t.opened_at.isoformat(), t.closed_at.isoformat(),
                        t.duration_seconds,
                    ])
            os.replace(tmp_path, filepath)
            logger.info("Saved %d trades to %s", len(trades_snapshot), filepath)
        except Exception:
            logger.exception("Failed to save trade history to %s", filepath)
            tmp_path.unlink(missing_ok=True)

    def _load_trade_history(self) -> None:
        filepath = self._data_dir / "trade_history.csv"
        if not filepath.exists():
            return

        # Parse into local buffers first; only commit to instance state on success
        # so that a corrupt file cannot leave _trade_history and
        # _strategy_realized_pnl in an inconsistent partial state.
        loaded_trades: list[TradeRecord] = []
        loaded_pnl: dict[str, float] = {}
        skipped = 0

        try:
            with open(filepath, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for line_no, row in enumerate(reader, start=2):
                    try:
                        trade = TradeRecord(
                            id=row["id"],
                            symbol=row["symbol"],
                            side=Side(row["side"]),
                            entry_price=float(row["entry_price"]),
                            exit_price=float(row["exit_price"]),
                            quantity=float(row["quantity"]),
                            pnl=float(row["pnl"]),
                            strategy_name=row["strategy_name"],
                            opened_at=datetime.fromisoformat(row["opened_at"]),
                            closed_at=datetime.fromisoformat(row["closed_at"]),
                            duration_seconds=float(row["duration_seconds"]),
                        )
                    except Exception:
                        logger.error(
                            "Skipping malformed row %d in %s", line_no, filepath
                        )
                        skipped += 1
                        continue
                    loaded_trades.append(trade)
                    loaded_pnl[trade.strategy_name] = (
                        loaded_pnl.get(trade.strategy_name, 0.0) + trade.pnl
                    )
        except Exception:
            logger.exception(
                "Failed to open trade history file %s — starting with empty history",
                filepath,
            )
            return

        self._trade_history.extend(loaded_trades)
        for name, pnl in loaded_pnl.items():
            self._strategy_realized_pnl[name] = (
                self._strategy_realized_pnl.get(name, 0.0) + pnl
            )
        logger.info(
            "Loaded %d trades from %s (%d rows skipped)",
            len(loaded_trades), filepath, skipped,
        )

    def _on_order_filled(self, event: Event) -> None:
        """Cache the fill price from ORDER_FILLED events for use in trade recording.

        ISSUE-014 fix: prices are now stored in a FIFO deque per (symbol, side)
        key so that back-to-back fills on the same symbol do not overwrite each
        other.  _record_trade pops the oldest price from the front of the queue.
        """
        order = event.payload.get("order")
        if order and order.average_fill_price is not None:
            with self._lock:
                self._pending_fill_prices[(order.symbol, order.side)].append(
                    order.average_fill_price
                )
                logger.debug(
                    "_on_order_filled queued price %.4f for key=(%s, %s) "
                    "(queue depth=%d)",
                    order.average_fill_price,
                    order.symbol,
                    order.side.value,
                    len(self._pending_fill_prices[(order.symbol, order.side)]),
                )

    def _record_trade(self, position: Position) -> None:
        """Record a closed position as a trade. Must be called with _lock held.

        BloFin's ``realized_pnl`` field is cumulative since the position was
        first opened — not since the last flip.  We track the amount already
        recorded for each position ID so we can compute the *incremental*
        realized PnL for each trade record and avoid double-counting across
        consecutive flips.
        """
        strategy_name = position.strategy_name or "unknown"
        now = datetime.now(tz=timezone.utc)
        duration = (now - position.opened_at).total_seconds()

        # Incremental realized PnL since the last recorded flip (or since open)
        prev_realized = self._last_recorded_realized_pnl.get(position.id, 0.0)
        incremental_realized = position.realized_pnl - prev_realized
        pnl = incremental_realized + position.unrealized_pnl
        # Update the watermark so the next flip only records new realized PnL
        self._last_recorded_realized_pnl[position.id] = position.realized_pnl

        # Use the actual fill price from the close order when available.
        # The close order for a BUY is a SELL, and vice versa.
        # ISSUE-014 fix: popleft() takes the oldest fill price (FIFO) so
        # concurrent fills on the same symbol/side are consumed in order rather
        # than silently overwriting each other.
        close_side = Side.SELL if position.side == Side.BUY else Side.BUY
        key = (position.symbol, close_side)
        queue = self._pending_fill_prices.get(key)
        if queue:
            exit_price = queue.popleft()
            # Remove empty deques to keep the dict tidy
            if not queue:
                del self._pending_fill_prices[key]
        else:
            exit_price = position.current_price

        trade = TradeRecord(
            id=position.id,
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            pnl=pnl,
            strategy_name=strategy_name,
            opened_at=position.opened_at,
            closed_at=now,
            duration_seconds=duration,
        )
        self._trade_history.append(trade)
        self._strategy_realized_pnl[strategy_name] = (
            self._strategy_realized_pnl.get(strategy_name, 0.0) + pnl
        )
        logger.info("Trade closed: %s %s PnL=%.4f", position.symbol, position.side.value, pnl)

    def _build_snapshot(self) -> PortfolioSnapshot:
        total_equity = self._balance.get("total_equity", 0.0)
        unrealized = sum(p.unrealized_pnl for p in self._positions)
        realized = sum(self._strategy_realized_pnl.values())

        # ISSUE-022: build separate realized and unrealized dicts per strategy
        # so the dashboard can plot a stable realized equity curve and overlay
        # the unrealized component as a dotted line.
        strategy_pnl_realized: dict[str, float] = {}
        strategy_pnl_unrealized: dict[str, float] = {}
        strategy_pnl: dict[str, float] = {}

        for name in self.get_all_strategy_names():
            r = self._strategy_realized_pnl.get(name, 0.0)
            u = sum(
                p.unrealized_pnl
                for p in self._positions
                if p.strategy_name == name
            )
            strategy_pnl_realized[name] = r
            strategy_pnl_unrealized[name] = u
            strategy_pnl[name] = r + u

        return PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            total_equity=total_equity,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            positions=list(self._positions),
            strategy_pnl=strategy_pnl,
            strategy_pnl_realized=strategy_pnl_realized,
            strategy_pnl_unrealized=strategy_pnl_unrealized,
        )
