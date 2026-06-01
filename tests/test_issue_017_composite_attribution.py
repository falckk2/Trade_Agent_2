"""
ISSUE-017: Targeted tests for the composite-naming attribution fix in
_update_portfolio (2026-06-01 fix by issue-resolver).

The fix pre-builds a `symbol_attribution` dict where:
- Single enabled strategy covering a symbol  → attribution = strategy name
- Multiple enabled strategies covering a symbol → attribution = "composite[s1,s2,...]"
  matching exactly the WeightedAggregatorFactory composite naming convention.

Tests verify:
1. Single strategy: correct attribution name assigned to unattributed position.
2. Multi-strategy: composite[...] name assigned, not the first-match strategy name.
3. Composite name matches the format produced by WeightedAggregatorFactory.build().
4. Already-attributed positions are not overwritten.
5. Disabled strategies are excluded from composite name; only enabled ones used.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import PositionStatus, Side, TimeFrame
from src.core.events import EventBus
from src.core.models import PortfolioSnapshot, Position
from src.engine.trading_engine import TradingEngine
from src.strategies.interface import IStrategy
from src.strategies.composite import WeightedAggregatorFactory
from src.core.models import Candle, Signal
from src.core.enums import SignalType


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
    exchange.get_balance = AsyncMock(return_value={"total_equity": 10000.0, "available": 9000.0})
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


class TestCompositeAttributionFix:
    @pytest.mark.asyncio
    async def test_single_strategy_attribution_is_strategy_name(self, engine):
        """
        ISSUE-017 fix: single enabled strategy covering a symbol gets that
        strategy's name as the attribution (not first-match from a list).
        """
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        assert positions_passed[0].strategy_name == "sma_btc", (
            "Single strategy should attribute to its own name"
        )

    @pytest.mark.asyncio
    async def test_multi_strategy_symbol_gets_composite_name(self, engine):
        """
        ISSUE-017 fix: when two enabled strategies cover the same symbol, the
        position is attributed to "composite[s1,s2]" — not first-match.
        """
        strat_a = StubStrategy("sma_btc")
        strat_b = StubStrategy("rsi_btc")
        engine.add_strategy(strat_a, symbols=["BTC-USDT"])
        engine.add_strategy(strat_b, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")
        engine.enable_strategy("rsi_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        attributed = positions_passed[0].strategy_name

        # Must not be first-match (either single name alone)
        assert attributed not in ("sma_btc", "rsi_btc"), (
            f"Multi-strategy symbol must not be attributed to a single strategy name; got '{attributed}'"
        )
        assert "composite[" in attributed, (
            f"Attribution should use composite[...] format; got '{attributed}'"
        )

    @pytest.mark.asyncio
    async def test_composite_name_matches_weighted_aggregator_factory_convention(self, engine):
        """
        ISSUE-017 fix: the composite attribution name must match exactly what
        WeightedAggregatorFactory.build() would produce: "composite[s1,s2,...]"
        in the order strategies were added.
        """
        strat_a = StubStrategy("sma_btc")
        strat_b = StubStrategy("rsi_btc")
        engine.add_strategy(strat_a, symbols=["BTC-USDT"])
        engine.add_strategy(strat_b, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")
        engine.enable_strategy("rsi_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        attributed = positions_passed[0].strategy_name

        # Verify it matches the factory's naming: "composite[sma_btc,rsi_btc]"
        factory = WeightedAggregatorFactory()
        composite = factory.build([(strat_a, 1.0), (strat_b, 1.0)])
        expected_name = composite.name  # "composite[sma_btc,rsi_btc]"

        assert attributed == expected_name, (
            f"Attribution '{attributed}' does not match WeightedAggregatorFactory "
            f"composite name '{expected_name}'"
        )

    @pytest.mark.asyncio
    async def test_pre_attributed_position_not_overwritten(self, engine):
        """Positions that already have a strategy_name must not be overwritten."""
        strat_a = StubStrategy("sma_btc")
        strat_b = StubStrategy("rsi_btc")
        engine.add_strategy(strat_a, symbols=["BTC-USDT"])
        engine.add_strategy(strat_b, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")
        engine.enable_strategy("rsi_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="original_strat")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        # Already attributed — must not be overwritten
        assert positions_passed[0].strategy_name == "original_strat", (
            "Pre-attributed positions must not be overwritten by _update_portfolio"
        )

    @pytest.mark.asyncio
    async def test_disabled_strategy_excluded_from_composite_name(self, engine):
        """
        When one of two strategies covering a symbol is disabled, only the
        enabled one should contribute to attribution (single name, not composite).
        """
        strat_a = StubStrategy("sma_btc")
        strat_b = StubStrategy("rsi_btc")
        engine.add_strategy(strat_a, symbols=["BTC-USDT"])
        engine.add_strategy(strat_b, symbols=["BTC-USDT"])

        # Enable only sma_btc; rsi_btc stays disabled
        engine.enable_strategy("sma_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        attributed = positions_passed[0].strategy_name

        # Only the enabled strategy contributes — no composite
        assert attributed == "sma_btc", (
            f"With only one enabled strategy, attribution should be 'sma_btc'; got '{attributed}'"
        )
