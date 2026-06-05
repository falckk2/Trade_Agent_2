"""
ISSUE-030: _update_portfolio called `symbol_attribution.get(pos.symbol)` which
returns None for a position on a symbol not covered by any strategy.
Position.strategy_name is declared `str = ""`, so assigning None violates the type.

Fix: changed to `symbol_attribution.get(pos.symbol) or ""` so uncovered symbols
get the declared default ("") rather than None.

Tests verify:
1. Position on an uncovered symbol gets strategy_name="" (not None)
2. Position on an attributed symbol still gets the correct strategy name
3. Already-attributed positions are not overwritten (still applies)
4. Source inspection confirms `or ""` is present at the assignment line
"""

import ast
import inspect
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import PositionStatus, Side, TimeFrame, SignalType
from src.core.events import EventBus
from src.core.models import Candle, PortfolioSnapshot, Position, Signal
from src.engine.trading_engine import TradingEngine
from src.strategies.interface import IStrategy


class StubStrategy(IStrategy):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze(self, candles: list[Candle]) -> Signal:
        return self._make_signal(SignalType.HOLD)

    def configure(self, params: dict) -> None:
        pass


def _pos(pos_id: str, symbol: str, strategy_name: str = "") -> Position:
    return Position(
        id=pos_id,
        symbol=symbol,
        side=Side.BUY,
        entry_price=50000.0,
        current_price=51000.0,
        quantity=0.1,
        strategy_name=strategy_name,
    )


@pytest.fixture
def engine():
    exchange = AsyncMock()
    exchange.get_positions = AsyncMock(return_value=[])
    exchange.get_balance = AsyncMock(
        return_value={"total_equity": 10000.0, "available": 9000.0}
    )
    data_provider = AsyncMock()
    risk_manager = MagicMock()
    order_executor = AsyncMock()
    portfolio_manager = MagicMock()
    portfolio_manager.update = MagicMock()
    portfolio_manager.get_snapshot = MagicMock(
        return_value=PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            total_equity=10000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
    )
    event_bus = EventBus()

    eng = TradingEngine(
        exchange=exchange,
        data_provider=data_provider,
        risk_manager=risk_manager,
        order_executor=order_executor,
        portfolio_manager=portfolio_manager,
        event_bus=event_bus,
        symbols=["BTC-USDT"],
        timeframe=TimeFrame.M5,
    )
    return eng


class TestStrategyNameNoneFallback:
    @pytest.mark.asyncio
    async def test_uncovered_symbol_gets_empty_string_not_none(self, engine):
        """
        ISSUE-030 core test: a position on a symbol not covered by any strategy
        must receive strategy_name="" rather than None.

        Without fix: symbol_attribution.get("ETH-USDT") → None → pos.strategy_name = None
        With fix:    symbol_attribution.get("ETH-USDT") or "" → "" → pos.strategy_name = ""
        """
        # Engine only tracks BTC-USDT; ETH-USDT has no covering strategy
        pos_uncovered = _pos("p_eth", "ETH-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos_uncovered])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]

        assert len(positions_passed) == 1
        attributed_name = positions_passed[0].strategy_name

        assert attributed_name is not None, (
            "strategy_name must never be None; got None for uncovered symbol (ISSUE-030)"
        )
        assert attributed_name == "", (
            f"Uncovered symbol should get strategy_name=''; got {attributed_name!r}"
        )

    @pytest.mark.asyncio
    async def test_covered_symbol_still_gets_correct_attribution(self, engine):
        """
        The fix must not break attribution for symbols that ARE covered.
        A single enabled strategy should still produce the strategy name.
        """
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos = _pos("p_btc", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]

        assert positions_passed[0].strategy_name == "sma_btc", (
            "Covered symbol must still receive correct strategy attribution"
        )

    @pytest.mark.asyncio
    async def test_pre_attributed_position_not_overwritten(self, engine):
        """
        Already-attributed positions must not have their strategy_name cleared
        to "" by the fix. The `if not pos.strategy_name:` guard must still hold.
        """
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos = _pos("p_btc", "BTC-USDT", strategy_name="old_composite_name")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]

        assert positions_passed[0].strategy_name == "old_composite_name", (
            "Pre-attributed positions must not be overwritten by _update_portfolio"
        )

    @pytest.mark.asyncio
    async def test_mixed_covered_and_uncovered_positions(self, engine):
        """
        When both covered and uncovered positions exist simultaneously,
        covered positions get their attribution and uncovered get "".
        No None values appear anywhere.
        """
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos_btc = _pos("p_btc", "BTC-USDT", strategy_name="")
        pos_eth = _pos("p_eth", "ETH-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos_btc, pos_eth])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]

        by_symbol = {p.symbol: p.strategy_name for p in positions_passed}

        assert by_symbol["BTC-USDT"] == "sma_btc", "Covered symbol attributed correctly"
        assert by_symbol["ETH-USDT"] == "", "Uncovered symbol gets empty string"
        assert all(v is not None for v in by_symbol.values()), (
            "No strategy_name must be None"
        )

    def test_source_contains_or_empty_string_at_attribution_assignment(self):
        """
        Source inspection: the attribution assignment line in _update_portfolio
        must use `or ""` to prevent None from being assigned.

        We verify the specific pattern: `symbol_attribution.get(pos.symbol) or ""`
        is present in trading_engine.py.
        """
        engine_path = (
            Path(__file__).parent.parent / "src" / "engine" / "trading_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")

        # The fix must include `or ""` in the attribution assignment
        assert 'symbol_attribution.get(pos.symbol) or ""' in source, (
            "trading_engine.py must contain `symbol_attribution.get(pos.symbol) or \"\"`"
            " to prevent None assignment (ISSUE-030 fix)"
        )

    def test_source_does_not_assign_bare_dict_get_to_strategy_name(self):
        """
        Verify the old buggy pattern `pos.strategy_name = symbol_attribution.get(pos.symbol)`
        (without `or ""`) is no longer present in _update_portfolio.

        This catches any accidental reversion of the fix.
        """
        engine_path = (
            Path(__file__).parent.parent / "src" / "engine" / "trading_engine.py"
        )
        source = engine_path.read_text(encoding="utf-8")

        # The old bare .get() without `or ""` must be gone
        # We check the exact pattern that would produce None
        assert "pos.strategy_name = symbol_attribution.get(pos.symbol)\n" not in source, (
            "trading_engine.py must not assign symbol_attribution.get(pos.symbol) "
            "directly without `or \"\"` (would assign None for uncovered symbols)"
        )

    @pytest.mark.asyncio
    async def test_strategy_name_type_preserved_as_str(self, engine):
        """
        The type declared on Position.strategy_name is `str = ""`.
        After _update_portfolio, all positions must have a str strategy_name.
        """
        pos_uncovered = _pos("p_eth", "ETH-USDT", strategy_name="")
        pos_btc = _pos("p_btc", "BTC-USDT", strategy_name="")

        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        engine._exchange.get_positions = AsyncMock(return_value=[pos_uncovered, pos_btc])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]

        for pos in positions_passed:
            assert isinstance(pos.strategy_name, str), (
                f"Position {pos.id} strategy_name must be str; "
                f"got {type(pos.strategy_name).__name__!r} = {pos.strategy_name!r}"
            )
