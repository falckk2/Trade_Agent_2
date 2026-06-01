"""Integration tests for the dashboard app creation."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.enums import TimeFrame
from src.core.models import PortfolioSnapshot, Position
from src.dashboard.app import create_app
from src.portfolio.manager import PortfolioManager


@pytest.fixture
def mock_data_provider():
    provider = AsyncMock()
    provider.get_candles = AsyncMock(return_value=[])
    provider.get_current_price = AsyncMock(return_value=50000.0)
    return provider


@pytest.fixture
def portfolio_manager(tmp_path):
    return PortfolioManager(data_dir=str(tmp_path))


class TestDashboardCreation:
    def test_create_app_returns_dash_app(self, portfolio_manager, mock_data_provider):
        app = create_app(
            portfolio_manager=portfolio_manager,
            data_provider=mock_data_provider,
            symbols=["BTC-USDT", "ETH-USDT"],
            strategy_names=["sma", "rsi"],
            timeframe=TimeFrame.M5,
        )
        assert app is not None
        assert app.title == "Trade Agent 2"

    def test_layout_has_tabs(self, portfolio_manager, mock_data_provider):
        app = create_app(
            portfolio_manager=portfolio_manager,
            data_provider=mock_data_provider,
            symbols=["BTC-USDT"],
            strategy_names=["sma"],
        )
        # Layout should be set
        assert app.layout is not None
