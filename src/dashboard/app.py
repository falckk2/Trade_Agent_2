"""Dash application entry point."""

import logging
from typing import TYPE_CHECKING

import dash_ag_grid as dag
from dash import Dash

from src.core.enums import TimeFrame
from src.dashboard.callbacks import register_callbacks
from src.dashboard.layout import create_layout
from src.data.interface import IDataProvider  # abstraction, not concrete provider
from src.portfolio.interface import IPortfolioManager

if TYPE_CHECKING:
    from src.engine.trading_engine import TradingEngine

logger = logging.getLogger(__name__)


def create_app(
    portfolio_manager: IPortfolioManager,
    data_provider: IDataProvider,
    symbols: list[str],
    strategy_names: list[str],
    timeframe: TimeFrame = TimeFrame.M5,
    engine: "TradingEngine | None" = None,
    refresh_interval_ms: int = 2000,
) -> Dash:
    """Create and configure the Dash application."""

    app = Dash(
        __name__,
        title="Trade Agent 2",
        update_title=None,  # Disable "Updating..." title
        # Legacy CSS theme for the ag-grid tables (FABLE-016): the quartz
        # stylesheet also contains the -dark variant used in components.py.
        external_stylesheets=[dag.themes.QUARTZ],
    )

    app.layout = create_layout(symbols, strategy_names, refresh_interval_ms=refresh_interval_ms)

    register_callbacks(app, portfolio_manager, data_provider, timeframe, engine)

    return app


def run_dashboard(
    app: Dash,
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
) -> None:
    """Run the Dash server."""
    logger.info("Starting dashboard at http://%s:%d", host, port)
    app.run(host=host, port=port, debug=debug)
