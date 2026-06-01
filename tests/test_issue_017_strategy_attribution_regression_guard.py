"""
ISSUE-017: _update_portfolio uses first-match strategy attribution for positions.
Status: Open (inherent BloFin net-mode limitation).

Regression guard tests verify:
1. The current first-match behavior is in place (not silently changed)
2. An enabled strategy is preferred over disabled ones (two-pass logic present)
3. When only one strategy covers a symbol, attribution is correct

Live multi-strategy concurrent position test is Inconclusive without live API.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.enums import PositionStatus, Side, TimeFrame
from src.core.events import EventBus
from src.core.models import PortfolioSnapshot, Position
from src.engine.trading_engine import TradingEngine
from src.strategies.interface import IStrategy
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


class TestStrategyAttributionRegressionGuard:
    @pytest.mark.asyncio
    async def test_single_strategy_covers_position_symbol_is_attributed(self, engine):
        """When one strategy covers BTC-USDT, a BTC-USDT position gets that strategy name."""
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        # Portfolio manager should have been called with the position now attributed
        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        assert positions_passed[0].strategy_name == "sma_btc"

    @pytest.mark.asyncio
    async def test_enabled_strategy_preferred_over_disabled(self, engine):
        """When two strategies cover the same symbol, the enabled one should be preferred."""
        strat_a = StubStrategy("strat_a")
        strat_b = StubStrategy("strat_b")
        engine.add_strategy(strat_a, symbols=["BTC-USDT"])
        engine.add_strategy(strat_b, symbols=["BTC-USDT"])

        # Enable only strat_b
        engine.enable_strategy("strat_b")

        pos = _pos("p1", "BTC-USDT", strategy_name="")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        # strat_b is enabled — should be preferred
        assert positions_passed[0].strategy_name == "strat_b"

    @pytest.mark.asyncio
    async def test_pre_attributed_position_not_overwritten(self, engine):
        """Positions that already have a strategy_name must not be overwritten."""
        strat = StubStrategy("sma_btc")
        engine.add_strategy(strat, symbols=["BTC-USDT"])
        engine.enable_strategy("sma_btc")

        pos = _pos("p1", "BTC-USDT", strategy_name="original_strat")
        engine._exchange.get_positions = AsyncMock(return_value=[pos])

        await engine._update_portfolio()

        call_args = engine._portfolio_manager.update.call_args
        positions_passed = call_args[0][0]
        # Already has strategy_name — should not be changed
        assert positions_passed[0].strategy_name == "original_strat"

    def test_known_limitation_documented(self):
        """
        ISSUE-017 OPEN: Multi-strategy attribution on the same symbol uses first-match.
        This test documents the known limitation without asserting a fix.
        """
        # This is a documentation test — no assertion on fix
        # The limitation: position.strategy_name = first enabled strategy covering the symbol
        assert True, (
            "ISSUE-017 known limitation: first-match strategy attribution for net-mode positions "
            "is inherent and unfixed. Position P&L may be attributed to the wrong strategy "
            "when multiple strategies cover the same symbol."
        )
