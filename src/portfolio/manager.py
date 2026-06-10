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
from src.portfolio.stats import compute_performance_stats

logger = logging.getLogger(__name__)

_CSV_HEADER = [
    "id", "symbol", "side", "entry_price", "exit_price",
    "quantity", "pnl", "fee", "strategy_name", "opened_at",
    "closed_at", "duration_seconds",
]


def _trade_row(t: TradeRecord) -> list:
    return [
        t.id, t.symbol, t.side.value, t.entry_price,
        t.exit_price, t.quantity, t.pnl, t.fee,
        t.strategy_name or "",
        t.opened_at.isoformat(), t.closed_at.isoformat(),
        t.duration_seconds,
    ]


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
        # Parallel fee cache — same keying as _pending_fill_prices.
        # Stores the exchange fee for each fill so _record_trade can deduct
        # both entry and exit fees from the net P&L.
        self._pending_fees: dict[tuple[str, Side], collections.deque[float]] = (
            collections.defaultdict(collections.deque)
        )
        # FABLE-006: close fills carry the Position in the ORDER_FILLED payload,
        # so they can be matched by position id instead of positionally.
        # position_id -> (exit_price, exit_fee). The (symbol, side) queues
        # remain as fallback for fills without a position (entries, flips).
        self._pending_close_fills: dict[str, tuple[float, float]] = {}

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
                        # Pass the current pos's realized_pnl so the flip's
                        # realized delta is captured correctly (ISSUE-029).
                        # The old leg has no remaining unrealized PnL after
                        # the flip, so override_unrealized_pnl=0 is used.
                        self._record_trade(
                            prev,
                            realized_pnl_override=pos.realized_pnl,
                            override_unrealized_pnl=0.0,
                        )

            self._positions = list(current.values())
            self._prev_positions = current

            # Take snapshot
            snapshot = self._build_snapshot()
            self._snapshots.append(snapshot)

            # Keep snapshots manageable
            if len(self._snapshots) > 10000:
                self._snapshots = self._snapshots[-10000:]

            # Durable equity record for offline performance analysis
            # (FABLE-018) — the in-memory list above dies with the process.
            self._append_snapshot_to_csv(snapshot)

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

    def get_performance_stats(self, strategy_name: str | None = None) -> dict:
        """Compute performance statistics from recorded trades (FABLE-012).

        Delegates to compute_performance_stats — the same function the
        backtester uses (FABLE-010) — so live and backtest numbers are
        directly comparable.
        """
        with self._lock:
            trades = [
                t for t in self._trade_history
                if strategy_name is None or t.strategy_name == strategy_name
            ]
        return compute_performance_stats(trades)

    def save_trade_history(self) -> None:
        filepath = self._data_dir / "trade_history.csv"
        # Write to a temp file first, then atomically replace the real file so
        # that a process kill mid-write cannot corrupt the CSV (POSIX rename is
        # atomic on the same filesystem).
        tmp_path = filepath.with_suffix(".csv.tmp")
        with self._lock:
            trades_snapshot = list(self._trade_history)
            # Leftover cached fills mean a fill was never matched to a trade —
            # surface it so queue drift is visible instead of silent (FABLE-006).
            leftover = sum(len(q) for q in self._pending_fill_prices.values())
            leftover += len(self._pending_close_fills)
            if leftover:
                logger.warning(
                    "%d cached fill(s) were never matched to a recorded trade — "
                    "price/fee attribution may have drifted this session (FABLE-006)",
                    leftover,
                )
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(_CSV_HEADER)
                for t in trades_snapshot:
                    writer.writerow(_trade_row(t))
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
                            fee=float(row.get("fee") or 0),
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
        if not (order and order.average_fill_price is not None):
            return
        position = event.payload.get("position")
        with self._lock:
            if position is not None and position.id:
                # Close fill — match by position identity (FABLE-006), so a
                # missed or extra fill on the same symbol/side can never shift
                # this price/fee onto the wrong trade.
                self._pending_close_fills[position.id] = (
                    order.average_fill_price,
                    order.fee,
                )
                logger.debug(
                    "_on_order_filled cached close fill price=%.4f fee=%.6f "
                    "for position %s",
                    order.average_fill_price, order.fee, position.id,
                )
                return
            key = (order.symbol, order.side)
            self._pending_fill_prices[key].append(order.average_fill_price)
            self._pending_fees[key].append(order.fee)
            depth = len(self._pending_fill_prices[key])
            if depth > 4:
                logger.warning(
                    "_on_order_filled: fill queue for (%s, %s) has depth %d — "
                    "possible unmatched fills drifting (FABLE-006)",
                    order.symbol, order.side.value, depth,
                )
            logger.debug(
                "_on_order_filled queued price=%.4f fee=%.6f for key=(%s, %s) "
                "(queue depth=%d)",
                order.average_fill_price,
                order.fee,
                order.symbol,
                order.side.value,
                depth,
            )

    def _record_trade(
        self,
        position: Position,
        realized_pnl_override: float | None = None,
        override_unrealized_pnl: float | None = None,
    ) -> None:
        """Record a closed position as a trade. Must be called with _lock held.

        BloFin's ``realized_pnl`` field is cumulative since the position was
        first opened — not since the last flip.  We track the amount already
        recorded for each position ID so we can compute the *incremental*
        realized PnL for each trade record and avoid double-counting across
        consecutive flips.

        Parameters
        ----------
        position:
            The Position object whose metadata (symbol, side, entry_price, etc.)
            is recorded.  In the flip case this is the *previous-tick* object so
            the old side's metadata is preserved.
        realized_pnl_override:
            When provided (flip case), use this realized PnL value instead of
            ``position.realized_pnl``.  The flip deposits realized PnL into the
            *current* tick's position object, not the stale ``prev`` object
            (ISSUE-029).
        override_unrealized_pnl:
            When provided (flip case), use this value instead of
            ``position.unrealized_pnl``.  After a flip the old leg has no
            remaining unrealized PnL, so 0.0 is passed.
        """
        strategy_name = position.strategy_name or "unknown"
        now = datetime.now(tz=timezone.utc)
        duration = (now - position.opened_at).total_seconds()

        # Resolved realized PnL: use override (flip path) or position's own value
        realized_now = (
            realized_pnl_override
            if realized_pnl_override is not None
            else position.realized_pnl
        )
        unrealized_now = (
            override_unrealized_pnl
            if override_unrealized_pnl is not None
            else position.unrealized_pnl
        )

        # Incremental realized PnL since the last recorded flip (or since open)
        prev_realized = self._last_recorded_realized_pnl.get(position.id, 0.0)
        incremental_realized = realized_now - prev_realized
        pnl = incremental_realized + unrealized_now
        # Update the watermark so the next flip only records new realized PnL
        self._last_recorded_realized_pnl[position.id] = realized_now

        # Use the actual fill price from the close order when available.
        # The close order for a BUY is a SELL, and vice versa.
        # ISSUE-014 fix: popleft() takes the oldest fill price (FIFO) so
        # concurrent fills on the same symbol/side are consumed in order rather
        # than silently overwriting each other.
        close_side = Side.SELL if position.side == Side.BUY else Side.BUY
        close_key = (position.symbol, close_side)
        open_key = (position.symbol, position.side)

        # Prefer the identity-matched close fill (FABLE-006); fall back to the
        # positional (symbol, side) queue for fills that had no position
        # attached (flips arrive as entry orders for the opposite side).
        exit_fee = 0.0
        close_fill = self._pending_close_fills.pop(position.id, None) if position.id else None
        if close_fill is not None:
            exit_price, exit_fee = close_fill
        else:
            price_queue = self._pending_fill_prices.get(close_key)
            if price_queue:
                exit_price = price_queue.popleft()
                if not price_queue:
                    del self._pending_fill_prices[close_key]
            else:
                exit_price = position.current_price

            # Deduct exchange fees from net P&L (entry fee + exit fee).
            # Fees are cached on ORDER_FILLED events — if unavailable default to 0.
            fee_queue = self._pending_fees.get(close_key)
            if fee_queue:
                exit_fee = fee_queue.popleft()
                if not fee_queue:
                    del self._pending_fees[close_key]

        entry_fee = 0.0
        fee_queue = self._pending_fees.get(open_key)
        if fee_queue:
            entry_fee = fee_queue.popleft()
            if not fee_queue:
                del self._pending_fees[open_key]

        total_fee = entry_fee + exit_fee
        net_pnl = pnl - total_fee

        trade = TradeRecord(
            id=position.id,
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            pnl=net_pnl,
            strategy_name=strategy_name,
            opened_at=position.opened_at,
            closed_at=now,
            duration_seconds=duration,
            fee=total_fee,
        )
        self._trade_history.append(trade)
        self._strategy_realized_pnl[strategy_name] = (
            self._strategy_realized_pnl.get(strategy_name, 0.0) + net_pnl
        )
        # Persist immediately (FABLE-004): a crash must not lose the session's
        # trades — _load_trade_history rebuilds realized P&L from this file.
        self._append_trade_to_csv(trade)
        logger.info(
            "Trade closed: %s %s gross_pnl=%.4f fee=%.6f net_pnl=%.4f",
            position.symbol, position.side.value, pnl, total_fee, net_pnl,
        )

    def _append_snapshot_to_csv(self, snapshot: PortfolioSnapshot) -> None:
        """Append one equity-curve row to equity_curve.csv (crash-safe record).

        One row per engine tick (~30s) is ~1 MB/month — cheap insurance for
        performance analysis across restarts. I/O failures are logged and
        never abort the update.
        """
        filepath = self._data_dir / "equity_curve.csv"
        try:
            write_header = not filepath.exists()
            with open(filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(
                        ["timestamp", "total_equity", "unrealized_pnl",
                         "realized_pnl", "open_positions"]
                    )
                writer.writerow([
                    snapshot.timestamp.isoformat(),
                    snapshot.total_equity,
                    snapshot.unrealized_pnl,
                    snapshot.realized_pnl,
                    len(snapshot.positions),
                ])
        except Exception:
            logger.exception("Failed to append equity snapshot to %s", filepath)

    def _append_trade_to_csv(self, trade: TradeRecord) -> None:
        """Append a single trade to trade_history.csv (crash-safe persistence).

        save_trade_history() still does an atomic full rewrite at shutdown,
        which compacts/normalizes the file; this append only protects trades
        recorded between startup and an unclean exit. I/O failures are logged
        and never abort trade recording.
        """
        filepath = self._data_dir / "trade_history.csv"
        try:
            write_header = not filepath.exists()
            with open(filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(_CSV_HEADER)
                writer.writerow(_trade_row(trade))
        except Exception:
            logger.exception("Failed to append trade to %s", filepath)

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
