import asyncio
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

from src.core.enums import EventType, Side, SignalType, TimeFrame
from src.core.events import Event, EventBus
from src.core.models import Signal
from src.data.interface import IDataProvider
from src.exchange.interface import IExchange
from src.execution.interface import IOrderExecutor
from src.portfolio.interface import IPortfolioManager
from src.risk.interface import IRiskManager
from src.strategies.composite import WeightedAggregatorFactory
from src.strategies.interface import IStrategy, IStrategyAggregatorFactory

logger = logging.getLogger(__name__)


class TradingEngine:
    """Main orchestrator that wires all components together.

    On each tick:
    1. Group all enabled strategies by symbol
    2. For each symbol, aggregate signals from all covering strategies
       using a weighted composite — one order per symbol per tick
    3. Validate the aggregated signal through the risk manager
    4. Execute approved signals
    5. Update portfolio
    """

    def __init__(
        self,
        exchange: IExchange,
        data_provider: IDataProvider,
        risk_manager: IRiskManager,
        order_executor: IOrderExecutor,
        portfolio_manager: IPortfolioManager,
        event_bus: EventBus,
        symbols: list[str],
        timeframe: TimeFrame = TimeFrame.M5,
        candle_limit: int = 200,
        aggregator_factory: IStrategyAggregatorFactory | None = None,
    ) -> None:
        self._exchange = exchange
        self._data_provider = data_provider
        self._risk_manager = risk_manager
        self._order_executor = order_executor
        self._portfolio_manager = portfolio_manager
        self._event_bus = event_bus
        self._symbols = symbols
        self._timeframe = timeframe
        self._candle_limit = candle_limit

        # Strategy registry
        self._strategy_symbols: dict[str, list[str]] = {}
        self._strategy_weights: dict[str, float] = {}
        self._strategies: list[IStrategy] = []
        self._running = False

        # Aggregator factory — defaults to weighted composite
        self._aggregator_factory: IStrategyAggregatorFactory = (
            aggregator_factory or WeightedAggregatorFactory()
        )

        # Per-strategy enable/disable (default: all disabled)
        self._enabled_strategies: set[str] = set()
        self._strategy_lock = threading.Lock()

        # Last signal per strategy name (updated every tick)
        self._last_signals: dict[str, Signal] = {}
        # Last composite signal per symbol (set when >1 strategies cover a symbol)
        self._last_composite_signals: dict[str, Signal] = {}

        # Shutdown coordination (FABLE-003): stop() must not run cleanup while
        # a tick is still in flight. start() owns the full lifecycle — cleanup
        # runs after the loop exits — and _drained signals completion to stop().
        self._stop_requested: asyncio.Event | None = None
        self._drained: asyncio.Event | None = None
        self._close_positions_on_stop = True

    def add_strategy(
        self,
        strategy: IStrategy,
        symbols: list[str] | None = None,
        weight: float = 1.0,
    ) -> None:
        self._strategies.append(strategy)
        self._strategy_symbols[strategy.name] = symbols or list(self._symbols)
        self._strategy_weights[strategy.name] = weight
        logger.info(
            "Added strategy '%s' (weight=%.2f) for symbols %s",
            strategy.name,
            weight,
            self._strategy_symbols[strategy.name],
        )

    def remove_strategy(self, strategy_name: str) -> None:
        self._strategies = [
            s for s in self._strategies if s.name != strategy_name
        ]
        self._strategy_symbols.pop(strategy_name, None)
        self._strategy_weights.pop(strategy_name, None)
        with self._strategy_lock:
            self._enabled_strategies.discard(strategy_name)
        logger.info("Removed strategy '%s'", strategy_name)

    def enable_strategy(self, strategy_name: str) -> None:
        with self._strategy_lock:
            self._enabled_strategies.add(strategy_name)
        logger.info("Enabled strategy '%s'", strategy_name)

    def disable_strategy(self, strategy_name: str) -> None:
        with self._strategy_lock:
            self._enabled_strategies.discard(strategy_name)
        logger.info("Disabled strategy '%s'", strategy_name)

    def get_strategy_status(self) -> dict[str, dict]:
        """Return per-strategy status including enabled flag, weight, and last signal."""
        with self._strategy_lock:
            enabled = set(self._enabled_strategies)
            last_signals = dict(self._last_signals)
        return {
            s.name: {
                "enabled": s.name in enabled,
                "weight": self._strategy_weights.get(s.name, 1.0),
                "symbols": self._strategy_symbols.get(s.name, []),
                "last_signal": last_signals.get(s.name),
            }
            for s in self._strategies
        }

    def get_composite_signals(self) -> dict[str, Signal]:
        """Return last composite signal per symbol (only symbols with >1 active strategy)."""
        with self._strategy_lock:
            return dict(self._last_composite_signals)

    async def start(self, interval_seconds: float = 60.0) -> None:
        logger.info("Trading engine starting...")
        await self._exchange.connect()
        self._running = True
        self._stop_requested = asyncio.Event()
        self._drained = asyncio.Event()
        self._close_positions_on_stop = True

        try:
            # Initial portfolio update — use opening balance as drawdown baseline
            await self._update_portfolio()
            opening_equity = self._portfolio_manager.get_snapshot().total_equity
            self._risk_manager.set_initial_equity(opening_equity)

            logger.info(
                "Trading engine running. %d strategies, %d symbols, %.0fs interval",
                len(self._strategies),
                len(self._symbols),
                interval_seconds,
            )

            while self._running:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("Error in trading tick")
                if not self._running:
                    break
                # Interruptible sleep: stop() sets _stop_requested so shutdown
                # does not wait out the remainder of the interval.
                try:
                    await asyncio.wait_for(
                        self._stop_requested.wait(), timeout=interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            # Cleanup runs here — strictly after the last tick has finished —
            # so a tick in flight can never place an order after positions
            # were closed (FABLE-003).
            self._running = False
            try:
                await self._shutdown_cleanup(self._close_positions_on_stop)
            finally:
                self._drained.set()

    async def stop(self, close_positions: bool = True) -> None:
        """Request shutdown and wait until the engine has fully drained.

        Returns only after the in-flight tick (if any) finished, positions
        were closed, the exchange disconnected, and trade history was saved.
        """
        logger.info("Trading engine stopping...")
        self._close_positions_on_stop = close_positions
        self._running = False
        if self._stop_requested is not None:
            self._stop_requested.set()
        if self._drained is not None:
            await self._drained.wait()
        else:
            # start() never ran — no loop to drain; clean up directly.
            await self._shutdown_cleanup(close_positions)
        logger.info("Trading engine stopped")

    async def _shutdown_cleanup(self, close_positions: bool) -> None:
        if close_positions:
            await self.close_all_positions()
        await self._exchange.disconnect()
        self._portfolio_manager.save_trade_history()

    async def close_all_positions(self) -> None:
        """Market-close every open position and record the trades.

        After closing each position on the exchange, calls
        portfolio_manager.update([], balance) with an empty position list so
        the closure is detected and _record_trade fires — including fee
        deduction — before save_trade_history() is called in stop().
        Without this step the trades are closed on the exchange but never
        appear in trade_history.csv.
        """
        try:
            positions = await self._exchange.get_positions()
        except Exception:
            logger.exception("Could not fetch positions for shutdown close")
            return
        if not positions:
            logger.info("No open positions to close on shutdown")
            return
        logger.info("Closing %d open position(s) before shutdown", len(positions))
        for position in positions:
            try:
                await self._order_executor.close_position(position)
                logger.info(
                    "Closed %s %s qty=%.4f on shutdown",
                    position.symbol, position.side.value, position.quantity,
                )
            except Exception:
                logger.exception(
                    "Failed to close %s %s on shutdown — manual close required",
                    position.symbol, position.side.value,
                )
                await self._event_bus.publish(
                    Event(
                        event_type=EventType.ALERT,
                        payload={
                            "level": "critical",
                            "message": (
                                f"Failed to close {position.symbol} "
                                f"{position.side.value} qty={position.quantity:.4f} "
                                f"on shutdown — MANUAL CLOSE REQUIRED on the exchange."
                            ),
                        },
                    )
                )

        # Force portfolio_manager to detect all closures and call _record_trade.
        # Passing an empty position list makes every position in _prev_positions
        # appear as closed, which triggers trade recording with the fill prices
        # and fees already cached by the ORDER_FILLED events above.
        try:
            balance = await self._exchange.get_balance()
        except Exception:
            snapshot = self._portfolio_manager.get_snapshot()
            balance = {
                "total_equity": snapshot.total_equity,
                "available": snapshot.total_equity,
            }
            logger.warning("Could not fetch balance for shutdown trade recording — using last snapshot")
        self._portfolio_manager.update([], balance)
        logger.info("Shutdown trade records committed for %d position(s)", len(positions))

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def strategies(self) -> list[IStrategy]:
        return list(self._strategies)

    async def _check_exits(self, portfolio) -> int:
        """Close positions that have hit their stop-loss or take-profit level.

        Returns the number of positions closed so the caller can refresh the
        portfolio snapshot before signal processing (FABLE-009).
        """
        closed = 0
        for position in portfolio.positions:
            stop_out = self._risk_manager.should_stop_out(position)
            take_profit = self._risk_manager.should_take_profit(position)
            if stop_out or take_profit:
                reason = "stop-loss" if stop_out else "take-profit"
                logger.info(
                    "%s triggered for %s %s (entry=%.4f current=%.4f)",
                    reason,
                    position.symbol,
                    position.side.value,
                    position.entry_price,
                    position.current_price,
                )
                await self._order_executor.close_position(position)
                closed += 1
        return closed

    async def _tick(self) -> None:
        with self._strategy_lock:
            enabled = set(self._enabled_strategies)

        # Refresh positions and prices FIRST so that exit checks and signal
        # evaluations operate on fresh market data (fixes ISSUE-015).
        await self._update_portfolio()

        # Exit positions that have breached stop-loss or take-profit before evaluating new signals
        closed = await self._check_exits(self._portfolio_manager.get_snapshot())
        if closed:
            # Refresh so signal processing sees the post-exit state — otherwise
            # the closed positions linger in the snapshot for the rest of the
            # tick: duplicate-side checks block legitimate re-entries and CLOSE
            # signals could re-close (i.e. reopen, in net mode) a dead position
            # (FABLE-009).
            await self._update_portfolio()

        # Group enabled strategies by symbol: symbol → [(strategy, weight), ...]
        symbol_strategies: dict[str, list[tuple[IStrategy, float]]] = defaultdict(list)
        for strategy in self._strategies:
            if strategy.name not in enabled:
                continue
            weight = self._strategy_weights.get(strategy.name, 1.0)
            for symbol in self._strategy_symbols.get(strategy.name, self._symbols):
                symbol_strategies[symbol].append((strategy, weight))

        for symbol, strat_weights in symbol_strategies.items():
            if len(strat_weights) == 1:
                strategy, _ = strat_weights[0]
                await self._process_strategy_symbol(strategy, symbol)
                # Capture individual signal (already stored inside _process_strategy_symbol)
            else:
                composite = self._build_composite(strat_weights)
                logger.debug(
                    "Aggregating %d strategies for %s: %s",
                    len(strat_weights),
                    symbol,
                    [s.name for s, _ in strat_weights],
                )
                await self._process_strategy_symbol(composite, symbol)
                # Propagate individual child signals and composite output to dashboard
                with self._strategy_lock:
                    for name, sig in composite.last_child_signals.items():
                        self._last_signals[name] = Signal(
                            signal_type=sig.signal_type,
                            symbol=symbol,
                            strength=sig.strength,
                            strategy_name=sig.strategy_name,
                            timestamp=sig.timestamp,
                            metadata=dict(sig.metadata),
                        )
                    if composite.name in self._last_signals:
                        self._last_composite_signals[symbol] = self._last_signals[composite.name]

    def _build_composite(
        self, strat_weights: list[tuple[IStrategy, float]]
    ) -> IStrategy:
        """Delegate composite construction to the injected aggregator factory."""
        return self._aggregator_factory.build(strat_weights)

    async def _process_strategy_symbol(
        self, strategy: IStrategy, symbol: str
    ) -> None:
        # Fetch candles
        candles = await self._data_provider.get_candles(
            symbol, self._timeframe, self._candle_limit
        )
        if not candles:
            return

        # Generate signal
        signal = strategy.analyze(candles)
        # Attach symbol to signal (strategies are symbol-agnostic).
        # Copy metadata to avoid sharing the mutable dict across Signal instances.
        signal = Signal(
            signal_type=signal.signal_type,
            symbol=symbol,
            strength=signal.strength,
            strategy_name=signal.strategy_name,
            timestamp=signal.timestamp,
            metadata=dict(signal.metadata),
        )

        # Record for dashboard visibility
        with self._strategy_lock:
            self._last_signals[strategy.name] = signal

        logger.debug(
            "Signal: strategy=%s symbol=%s type=%s strength=%.2f",
            strategy.name, symbol, signal.signal_type.value, signal.strength,
        )

        if signal.signal_type == SignalType.HOLD:
            return

        await self._event_bus.publish(
            Event(
                event_type=EventType.SIGNAL_GENERATED,
                payload={"signal": signal},
            )
        )

        # Risk check
        portfolio = self._portfolio_manager.get_snapshot()
        logger.debug(
            "Portfolio: equity=%.2f positions=%d",
            portfolio.total_equity, len(portfolio.positions),
        )
        if not self._risk_manager.validate_signal(signal, portfolio):
            logger.info(
                "Signal rejected by risk manager: %s %s %s",
                signal.strategy_name, signal.signal_type.value, symbol,
            )
            return

        # Handle CLOSE signals.
        # ISSUE-031: match by symbol only — net mode holds one position per
        # symbol regardless of which strategy/composite opened it.  Matching on
        # strategy.name would silently skip positions whose strategy_name tag is
        # a stale composite string (e.g. "composite[sma,rsi]") that no longer
        # matches the current single-strategy name after a UI toggle.
        if signal.signal_type == SignalType.CLOSE:
            for position in portfolio.positions:
                if position.symbol == symbol:
                    await self._order_executor.close_position(position)
            return

        # Skip if already in the same direction for this symbol
        expected_side = Side.BUY if signal.signal_type == SignalType.LONG else Side.SELL
        for pos in portfolio.positions:
            if pos.symbol == symbol and pos.side == expected_side:
                logger.debug(
                    "Already %s %s — skipping duplicate signal",
                    expected_side.value, symbol,
                )
                return

        # Calculate position size
        current_price = await self._data_provider.get_current_price(symbol)
        quantity = self._risk_manager.calculate_position_size(
            signal, portfolio, current_price
        )
        logger.debug(
            "Position size: price=%.2f quantity=%.6f",
            current_price, quantity,
        )
        if quantity <= 0:
            logger.info("Quantity is zero — skipping order")
            return

        # Exchange-side protective exits (FABLE-001): computed from the current
        # price (market orders fill near it) and attached to the entry order so
        # the exchange enforces them even if the bot dies. _check_exits remains
        # as a software backstop.
        stop_loss = self._risk_manager.get_stop_loss(signal, current_price)
        take_profit = self._risk_manager.get_take_profit(signal, current_price)

        # Execute
        await self._order_executor.execute_signal(
            signal, quantity, symbol,
            stop_loss=stop_loss, take_profit=take_profit,
        )

    async def _update_portfolio(self) -> None:
        positions = await self._exchange.get_positions()
        balance = await self._exchange.get_balance()

        # ISSUE-017 fix: build a symbol → composite_name map so positions are
        # attributed consistently with the composite name used during _tick.
        # When multiple strategies cover the same symbol, the composite name
        # "composite[s1,s2,...]" is used — matching WeightedAggregatorFactory.
        with self._strategy_lock:
            enabled = set(self._enabled_strategies)

        # Build per-symbol attribution name (prefer enabled strategies)
        symbol_attribution: dict[str, str] = {}
        for symbol in self._symbols:
            # Gather enabled strategies covering this symbol (preserving add order)
            covering_enabled = [
                strat.name
                for strat in self._strategies
                if strat.name in enabled
                and symbol in self._strategy_symbols.get(strat.name, [])
            ]
            if len(covering_enabled) > 1:
                symbol_attribution[symbol] = (
                    "composite[" + ",".join(covering_enabled) + "]"
                )
            elif len(covering_enabled) == 1:
                symbol_attribution[symbol] = covering_enabled[0]
            else:
                # Fall back: any strategy covering this symbol (disabled)
                covering_any = [
                    strat.name
                    for strat in self._strategies
                    if symbol in self._strategy_symbols.get(strat.name, [])
                ]
                if len(covering_any) > 1:
                    symbol_attribution[symbol] = (
                        "composite[" + ",".join(covering_any) + "]"
                    )
                elif len(covering_any) == 1:
                    symbol_attribution[symbol] = covering_any[0]
                # else: no strategy covers this symbol — leave unattributed

        for pos in positions:
            if not pos.strategy_name:
                # ISSUE-030: use `or ""` so an uncovered symbol never assigns
                # None to the str-typed strategy_name field.
                pos.strategy_name = symbol_attribution.get(pos.symbol) or ""

        self._portfolio_manager.update(positions, balance)

        await self._event_bus.publish(
            Event(
                event_type=EventType.PORTFOLIO_UPDATE,
                payload={
                    "snapshot": self._portfolio_manager.get_snapshot()
                },
            )
        )
