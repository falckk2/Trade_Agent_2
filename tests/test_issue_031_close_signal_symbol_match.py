"""
ISSUE-031: The CLOSE handler in _process_strategy_symbol matched by both
`position.symbol == symbol` AND `position.strategy_name == strategy.name`.

If a position was tagged with a composite name (e.g. "composite[sma,rsi]") when two
strategies were enabled, and later only one strategy ("rsi") is enabled, the CLOSE
signal from "rsi" would never match the stale composite tag — leaving the position
open despite a valid exit signal.

Fix: removed the `position.strategy_name == strategy.name` condition; the CLOSE
handler now only matches on `position.symbol == symbol`, which is correct for
BloFin net-mode (one net position per symbol regardless of originating strategy).

Tests verify:
1. CLOSE closes a position tagged with a composite name when strategy.name is a child
2. CLOSE does not close a position for a different symbol
3. Source inspection confirms the old strategy_name equality check is absent
4. Source inspection confirms symbol-only matching is present
5. CLOSE on correct symbol, regardless of strategy_name tag, always fires close_position
"""

import ast
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

from src.core.enums import PositionStatus, Side, SignalType, TimeFrame
from src.core.events import EventBus
from src.core.models import Candle, PortfolioSnapshot, Position, Signal
from src.engine.trading_engine import TradingEngine
from src.strategies.interface import IStrategy


class CloseSignalStrategy(IStrategy):
    """Strategy that always emits a CLOSE signal."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze(self, candles: list[Candle]) -> Signal:
        return self._make_signal(SignalType.CLOSE, strength=1.0)

    def configure(self, params: dict) -> None:
        pass


class HoldStrategy(IStrategy):
    """Strategy that always emits HOLD — used for symbols that should not be closed."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze(self, candles: list[Candle]) -> Signal:
        return self._make_signal(SignalType.HOLD)

    def configure(self, params: dict) -> None:
        pass


def _pos(
    pos_id: str,
    symbol: str,
    strategy_name: str,
    side: Side = Side.BUY,
) -> Position:
    return Position(
        id=pos_id,
        symbol=symbol,
        side=side,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        strategy_name=strategy_name,
        status=PositionStatus.OPEN,
    )


def _snapshot_with_positions(positions: list[Position]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=10000.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=positions,
    )


def _make_engine(symbols: list[str]) -> TradingEngine:
    exchange = AsyncMock()
    exchange.get_positions = AsyncMock(return_value=[])
    exchange.get_balance = AsyncMock(
        return_value={"total_equity": 10000.0, "available": 9000.0}
    )
    data_provider = AsyncMock()
    data_provider.get_candles = AsyncMock(
        return_value=[
            Candle(
                timestamp=datetime.now(tz=timezone.utc),
                open=50000.0,
                high=51000.0,
                low=49000.0,
                close=50500.0,
                volume=100.0,
            )
            for _ in range(50)
        ]
    )
    risk_manager = MagicMock()
    risk_manager.validate_signal = MagicMock(return_value=True)
    order_executor = AsyncMock()
    order_executor.close_position = AsyncMock(return_value=None)

    portfolio_manager = MagicMock()
    portfolio_manager.update = MagicMock()

    event_bus = EventBus()

    eng = TradingEngine(
        exchange=exchange,
        data_provider=data_provider,
        risk_manager=risk_manager,
        order_executor=order_executor,
        portfolio_manager=portfolio_manager,
        event_bus=event_bus,
        symbols=symbols,
        timeframe=TimeFrame.M5,
    )
    return eng


class TestCloseSignalSymbolMatch:
    @pytest.mark.asyncio
    async def test_close_fires_on_position_with_composite_tag(self):
        """
        ISSUE-031 core test: a CLOSE signal from strategy "rsi" must close a
        position tagged "composite[sma,rsi]" even though strategy.name != tag.

        Before fix: position.strategy_name "composite[sma,rsi]" != "rsi" → no close.
        After fix:  position.symbol == symbol → close_position called.
        """
        eng = _make_engine(["BTC-USDT"])

        # Set up a single "rsi" strategy that emits CLOSE
        rsi_strat = CloseSignalStrategy("rsi")
        eng.add_strategy(rsi_strat, symbols=["BTC-USDT"])
        eng.enable_strategy("rsi")

        # Position is tagged with old composite name from when both strategies were enabled
        pos = _pos("p1", "BTC-USDT", strategy_name="composite[sma,rsi]")
        snapshot = _snapshot_with_positions([pos])
        eng._portfolio_manager.get_snapshot = MagicMock(return_value=snapshot)

        await eng._process_strategy_symbol(rsi_strat, "BTC-USDT")

        eng._order_executor.close_position.assert_called_once_with(pos), (
            "close_position must be called for the BTC-USDT position regardless of "
            "strategy_name tag (ISSUE-031 fix)"
        )

    @pytest.mark.asyncio
    async def test_close_does_not_close_different_symbol(self):
        """
        A CLOSE signal for BTC-USDT must not close a position on ETH-USDT.
        Symbol-only matching must still be symbol-specific.
        """
        eng = _make_engine(["BTC-USDT", "ETH-USDT"])

        btc_strat = CloseSignalStrategy("sma_btc")
        eng.add_strategy(btc_strat, symbols=["BTC-USDT"])
        eng.enable_strategy("sma_btc")

        # ETH position — should NOT be closed by a BTC CLOSE signal
        pos_eth = _pos("p_eth", "ETH-USDT", strategy_name="sma_btc")
        snapshot = _snapshot_with_positions([pos_eth])
        eng._portfolio_manager.get_snapshot = MagicMock(return_value=snapshot)

        await eng._process_strategy_symbol(btc_strat, "BTC-USDT")

        eng._order_executor.close_position.assert_not_called(), (
            "CLOSE for BTC-USDT must not close an ETH-USDT position"
        )

    @pytest.mark.asyncio
    async def test_close_fires_on_single_strategy_tagged_position(self):
        """
        The common case (no composite tag, strategy_name matches) must still work.
        A CLOSE from "sma" with a position tagged "sma" → close_position called.
        """
        eng = _make_engine(["BTC-USDT"])

        strat = CloseSignalStrategy("sma")
        eng.add_strategy(strat, symbols=["BTC-USDT"])
        eng.enable_strategy("sma")

        pos = _pos("p1", "BTC-USDT", strategy_name="sma")
        snapshot = _snapshot_with_positions([pos])
        eng._portfolio_manager.get_snapshot = MagicMock(return_value=snapshot)

        await eng._process_strategy_symbol(strat, "BTC-USDT")

        eng._order_executor.close_position.assert_called_once_with(pos)

    @pytest.mark.asyncio
    async def test_close_fires_on_position_with_empty_strategy_name(self):
        """
        A position with no strategy tag (strategy_name="") must still be closed
        by a CLOSE signal for the matching symbol.
        """
        eng = _make_engine(["BTC-USDT"])

        strat = CloseSignalStrategy("any_strategy")
        eng.add_strategy(strat, symbols=["BTC-USDT"])
        eng.enable_strategy("any_strategy")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        snapshot = _snapshot_with_positions([pos])
        eng._portfolio_manager.get_snapshot = MagicMock(return_value=snapshot)

        await eng._process_strategy_symbol(strat, "BTC-USDT")

        eng._order_executor.close_position.assert_called_once_with(pos), (
            "CLOSE must work even when position has no strategy_name tag"
        )

    @pytest.mark.asyncio
    async def test_close_closes_correct_position_when_two_symbols_in_portfolio(self):
        """
        When portfolio has positions for both BTC-USDT and ETH-USDT, a CLOSE
        signal for BTC-USDT closes only the BTC-USDT position.
        """
        eng = _make_engine(["BTC-USDT", "ETH-USDT"])

        btc_strat = CloseSignalStrategy("sma_btc")
        eng.add_strategy(btc_strat, symbols=["BTC-USDT"])
        eng.enable_strategy("sma_btc")

        pos_btc = _pos("p_btc", "BTC-USDT", strategy_name="sma_btc")
        pos_eth = _pos("p_eth", "ETH-USDT", strategy_name="sma_btc")
        snapshot = _snapshot_with_positions([pos_btc, pos_eth])
        eng._portfolio_manager.get_snapshot = MagicMock(return_value=snapshot)

        await eng._process_strategy_symbol(btc_strat, "BTC-USDT")

        # Only the BTC position should be closed
        eng._order_executor.close_position.assert_called_once_with(pos_btc)

    def test_source_close_handler_has_no_strategy_name_equality_check(self):
        """
        Source inspection: the CLOSE handler in _process_strategy_symbol must NOT
        contain `position.strategy_name == strategy.name` (the old buggy condition).
        """
        engine_path = (
            Path(__file__).parent.parent / "src" / "engine" / "trading_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")

        # The old condition must be absent
        assert "position.strategy_name == strategy.name" not in source, (
            "trading_engine.py must not contain `position.strategy_name == strategy.name` "
            "in the CLOSE handler — that pattern was the ISSUE-031 bug"
        )

    def test_source_close_handler_uses_symbol_only_match(self):
        """
        Source inspection: the CLOSE handler must contain a symbol-only condition
        `position.symbol == symbol` without the strategy_name restriction.
        """
        engine_path = (
            Path(__file__).parent.parent / "src" / "engine" / "trading_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")

        # The fix: symbol-only matching in the CLOSE block
        assert "position.symbol == symbol" in source, (
            "trading_engine.py must contain `position.symbol == symbol` in the CLOSE handler"
        )

    def test_source_close_signal_comment_references_issue_031(self):
        """
        The fix includes a comment explaining the ISSUE-031 rationale.
        Verify the comment is present so future developers understand why
        strategy_name matching was deliberately removed.
        """
        engine_path = (
            Path(__file__).parent.parent / "src" / "engine" / "trading_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")

        assert "ISSUE-031" in source, (
            "trading_engine.py should reference ISSUE-031 in a comment near "
            "the CLOSE handler to document why symbol-only matching is used"
        )
