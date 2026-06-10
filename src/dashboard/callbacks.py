"""Dash callbacks for real-time dashboard updates."""

from typing import TYPE_CHECKING

import plotly.graph_objects as go
from dash import Input, Output, State, ctx

from src.core.enums import TimeFrame
from src.dashboard.components import (
    _empty_figure,
    build_candlestick_chart,
    build_equity_chart,
    build_metric_card,
    build_performance_stats_table,
    build_pnl_bar_chart,
    build_positions_table,
    build_trade_history_table,
)
from src.core.enums import SignalType
from src.data.interface import IDataProvider
from src.portfolio.interface import IPortfolioManager

# Maps every SignalType to a display colour — extend here when new types are added
SIGNAL_COLORS: dict[str, str] = {
    SignalType.LONG.value: "#00c853",
    SignalType.SHORT.value: "#f44336",
    SignalType.CLOSE.value: "#ff9800",
    SignalType.HOLD.value: "#555",
}

if TYPE_CHECKING:
    from src.engine.trading_engine import TradingEngine


def register_callbacks(
    app,
    portfolio_manager: IPortfolioManager,
    data_provider: IDataProvider,
    timeframe: TimeFrame = TimeFrame.M5,
    engine: "TradingEngine | None" = None,
) -> None:
    """Register all Dash callbacks with injected dependencies."""

    @app.callback(
        [
            Output("metric-cards", "children"),
            Output("equity-chart", "figure"),
            Output("positions-table", "children"),
        ],
        [Input("refresh-interval", "n_intervals")],
    )
    def update_overview(_n):
        snapshot = portfolio_manager.get_snapshot()

        cards = [
            build_metric_card(
                "Total Equity",
                f"${snapshot.total_equity:,.2f}",
                f"${snapshot.unrealized_pnl:,.2f} unrealized",
                snapshot.unrealized_pnl >= 0,
            ),
            build_metric_card(
                "Realized P&L",
                f"${snapshot.realized_pnl:,.2f}",
                positive=snapshot.realized_pnl >= 0,
            ),
            build_metric_card(
                "Unrealized P&L",
                f"${snapshot.unrealized_pnl:,.2f}",
                positive=snapshot.unrealized_pnl >= 0,
            ),
            build_metric_card(
                "Open Positions",
                str(len(snapshot.positions)),
            ),
        ]

        equity_fig = build_equity_chart(portfolio_manager.get_snapshots())
        positions_tbl = build_positions_table(snapshot.positions)

        return cards, equity_fig, positions_tbl

    @app.callback(
        [
            Output("pnl-bar-chart", "figure"),
            Output("strategy-equity-chart", "figure"),
            Output("performance-stats-table", "children"),
            Output("trade-history-table", "children"),
        ],
        [
            Input("refresh-interval", "n_intervals"),
            Input("strategy-selector", "value"),
        ],
    )
    def update_strategy_performance(_n, selected_strategy):
        snapshot = portfolio_manager.get_snapshot()
        pnl_fig = build_pnl_bar_chart(snapshot.strategy_pnl)

        snapshots = portfolio_manager.get_snapshots()
        strat_fig = _build_strategy_equity_curves(snapshots, selected_strategy)

        # Per-strategy performance stats + a TOTAL row (FABLE-012)
        stats_rows = [
            {"name": name, **portfolio_manager.get_performance_stats(name)}
            for name in portfolio_manager.get_all_strategy_names()
        ]
        stats_rows.append({"name": "TOTAL", **portfolio_manager.get_performance_stats()})
        stats_tbl = build_performance_stats_table(stats_rows)

        filter_name = None if selected_strategy == "all" else selected_strategy
        trades = portfolio_manager.get_trade_history(filter_name)
        trade_tbl = build_trade_history_table(trades)

        return pnl_fig, strat_fig, stats_tbl, trade_tbl

    @app.callback(
        Output("candlestick-chart", "figure"),
        [
            Input("refresh-interval", "n_intervals"),
            Input("symbol-selector", "value"),
        ],
    )
    def update_market_chart(_n, symbol):
        if not symbol:
            return _empty_figure()

        # Use sync cache — populated by the trading engine's ticks
        candles = data_provider.get_cached_candles(symbol, timeframe, 200)
        return build_candlestick_chart(candles)

    if engine is not None:
        _register_strategy_control_callbacks(app, engine)


def _register_strategy_control_callbacks(app, engine) -> None:
    """Register callbacks for per-strategy start/stop buttons.

    UI construction is delegated to strategy_panel (SRP).
    """
    from dash import ALL
    from src.dashboard.strategy_panel import (
        build_composite_signals_panel,
        build_strategy_control_panel,
    )

    @app.callback(
        Output("strategy-control-panel", "children"),
        [
            Input("refresh-interval", "n_intervals"),
            Input({"type": "strategy-toggle-btn", "index": ALL}, "n_clicks"),
        ],
        State({"type": "strategy-toggle-btn", "index": ALL}, "id"),
        prevent_initial_call=False,
    )
    def update_strategy_controls(_n, btn_clicks, btn_ids):
        triggered = ctx.triggered_id
        if isinstance(triggered, dict) and triggered.get("type") == "strategy-toggle-btn":
            name = triggered["index"]
            status = engine.get_strategy_status()
            if status[name]["enabled"]:
                engine.disable_strategy(name)
            else:
                engine.enable_strategy(name)

        composite_panel = build_composite_signals_panel(engine)
        strategy_cards = build_strategy_control_panel(engine)
        return [composite_panel, *strategy_cards]


def _build_strategy_equity_curves(
    snapshots, selected_strategy: str
) -> go.Figure:
    """Build per-strategy equity curves.

    ISSUE-022 fix: plot realized P&L as a solid line (the true equity curve —
    stable, unaffected by mark-price noise) and unrealized P&L as a dotted
    overlay so users can distinguish closed-trade performance from open
    floating exposure.  Falls back to strategy_pnl (combined) for snapshots
    taken before the split fields were added.
    """
    if not snapshots:
        return _empty_figure("Strategy Performance Over Time")

    fig = go.Figure()

    all_names: set[str] = set()
    for s in snapshots:
        all_names.update(s.strategy_pnl.keys())

    timestamps = [s.timestamp for s in snapshots]

    for name in sorted(all_names):
        if selected_strategy != "all" and name != selected_strategy:
            continue

        # Use split fields when available (post-ISSUE-022 snapshots); fall back
        # to combined strategy_pnl for any legacy snapshot that lacks the fields.
        realized_vals = [
            s.strategy_pnl_realized.get(name, 0.0)
            if hasattr(s, "strategy_pnl_realized")
            else s.strategy_pnl.get(name, 0.0)
            for s in snapshots
        ]
        unrealized_vals = [
            s.strategy_pnl_unrealized.get(name, 0.0)
            if hasattr(s, "strategy_pnl_unrealized")
            else 0.0
            for s in snapshots
        ]

        # Solid line: realized P&L (true equity curve)
        fig.add_trace(go.Scatter(
            x=timestamps,
            y=realized_vals,
            mode="lines",
            name=f"{name} (realized)",
            line={"width": 2},
        ))

        # Dotted overlay: unrealized P&L (open position floating value)
        fig.add_trace(go.Scatter(
            x=timestamps,
            y=unrealized_vals,
            mode="lines",
            name=f"{name} (unrealized)",
            line={"dash": "dot", "width": 1},
        ))

    fig.update_layout(
        template="plotly_dark",
        title="Strategy P&L Over Time",
        xaxis_title="Time",
        yaxis_title="P&L ($)",
        paper_bgcolor="#16161d",
        plot_bgcolor="#16161d",
        margin={"l": 50, "r": 20, "t": 40, "b": 40},
    )
    return fig
