"""Reusable Dash component builders for the trading dashboard."""

import dash_ag_grid as dag
from dash import html
import plotly.graph_objects as go

from src.core.enums import SignalType
from src.core.models import Candle, PortfolioSnapshot, Position, Signal, TradeRecord

_DARK_BG = "#16161d"
_CARD_BG = "#1e1e2f"

# Shared ag-grid configuration (FABLE-016: dash_table.DataTable is deprecated
# in Dash 4; migrated to dash-ag-grid). AG Grid v33+ defaults to the JS
# Theming API — "legacy" opts back into the CSS-class themes, whose quartz
# stylesheet is loaded app-wide in app.py.
_GRID_THEME_CLASS = "ag-theme-quartz-dark"
_GRID_DEFAULT_COL = {"sortable": True, "resizable": True, "minWidth": 90}


def _grid(
    data: list[dict],
    columns: list[str],
    row_style_conditions: list[dict] | None = None,
    height: str | None = None,
) -> dag.AgGrid:
    """Build a dark-themed AgGrid from pre-formatted row dicts.

    height=None lets the grid grow with its rows (autoHeight); pass a CSS
    height for long scrollable tables.
    """
    grid_options: dict = {"theme": "legacy"}
    style = {"width": "100%"}
    if height is None:
        grid_options["domLayout"] = "autoHeight"
    else:
        style["height"] = height

    get_row_style = None
    if row_style_conditions:
        get_row_style = {"styleConditions": row_style_conditions}

    return dag.AgGrid(
        rowData=data,
        columnDefs=[{"field": c, "headerName": c} for c in columns],
        defaultColDef=_GRID_DEFAULT_COL,
        columnSize="responsiveSizeToFit",
        dashGridOptions=grid_options,
        getRowStyle=get_row_style,
        className=_GRID_THEME_CLASS,
        style=style,
    )


def _empty_figure(title: str = "") -> go.Figure:
    """Return a blank dark-themed Plotly figure."""
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        title=title,
        paper_bgcolor=_DARK_BG,
        plot_bgcolor=_DARK_BG,
    )
    return fig


def build_metric_card(
    label: str, value: str, change: str = "", positive: bool = True
) -> html.Div:
    color = "#00c853" if positive else "#ff1744"
    return html.Div(
        className="metric-card",
        children=[
            html.H4(label, style={"margin": "0", "color": "#888", "fontSize": "14px"}),
            html.H2(value, style={"margin": "4px 0", "color": "#fff"}),
            html.Span(
                change,
                style={"color": color, "fontSize": "14px"},
            )
            if change
            else None,
        ],
        style={
            "backgroundColor": "#1e1e2f",
            "borderRadius": "8px",
            "padding": "16px",
            "minWidth": "180px",
            "textAlign": "center",
        },
    )


def build_positions_table(positions: list[Position]) -> dag.AgGrid:
    data = []
    for p in positions:
        data.append(
            {
                "Symbol": p.symbol,
                "Side": p.side.value.upper(),
                "Size": f"{p.quantity:.4f}",
                "Entry": f"${p.entry_price:,.2f}",
                "Current": f"${p.current_price:,.2f}",
                "PnL": f"${p.unrealized_pnl:,.2f}",
                "Strategy": p.strategy_name,
            }
        )

    return _grid(
        data,
        ["Symbol", "Side", "Size", "Entry", "Current", "PnL", "Strategy"],
        row_style_conditions=[
            {
                "condition": "params.data.Side == 'BUY'",
                "style": {"color": "#00c853"},
            },
            {
                "condition": "params.data.Side == 'SELL'",
                "style": {"color": "#ff1744"},
            },
        ],
    )


def build_equity_chart(snapshots: list[PortfolioSnapshot]) -> go.Figure:
    if not snapshots:
        return _empty_figure("Equity Curve")

    timestamps = [s.timestamp for s in snapshots]
    equity = [s.total_equity for s in snapshots]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=equity,
            mode="lines",
            name="Equity",
            line={"color": "#00bcd4", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(0, 188, 212, 0.1)",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title="Equity Curve",
        xaxis_title="Time",
        yaxis_title="Equity ($)",
        paper_bgcolor="#16161d",
        plot_bgcolor="#16161d",
        margin={"l": 50, "r": 20, "t": 40, "b": 40},
    )
    return fig


def build_pnl_bar_chart(strategy_pnl: dict[str, float]) -> go.Figure:
    if not strategy_pnl:
        return _empty_figure("Strategy P&L")

    names = list(strategy_pnl.keys())
    values = list(strategy_pnl.values())
    colors = ["#00c853" if v >= 0 else "#ff1744" for v in values]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=names,
            y=values,
            marker_color=colors,
            text=[f"${v:,.2f}" for v in values],
            textposition="auto",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title="P&L by Strategy",
        xaxis_title="Strategy",
        yaxis_title="P&L ($)",
        paper_bgcolor="#16161d",
        plot_bgcolor="#16161d",
        margin={"l": 50, "r": 20, "t": 40, "b": 40},
    )
    return fig


def build_candlestick_chart(
    candles: list[Candle], signals: list[Signal] | None = None
) -> go.Figure:
    if not candles:
        return _empty_figure("Price Chart")

    timestamps = [c.timestamp for c in candles]
    fig = go.Figure()

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=timestamps,
            open=[c.open for c in candles],
            high=[c.high for c in candles],
            low=[c.low for c in candles],
            close=[c.close for c in candles],
            name="Price",
            increasing_line_color="#00c853",
            decreasing_line_color="#ff1744",
        )
    )

    # Volume as bar chart on secondary axis
    fig.add_trace(
        go.Bar(
            x=timestamps,
            y=[c.volume for c in candles],
            name="Volume",
            marker_color="rgba(100, 100, 255, 0.3)",
            yaxis="y2",
        )
    )

    # Overlay signals
    if signals:
        buy_signals = [s for s in signals if s.signal_type == SignalType.LONG]
        sell_signals = [s for s in signals if s.signal_type == SignalType.SHORT]

        if buy_signals:
            fig.add_trace(
                go.Scatter(
                    x=[s.timestamp for s in buy_signals],
                    y=[_find_price_at(candles, s.timestamp) for s in buy_signals],
                    mode="markers",
                    name="Buy",
                    marker={
                        "symbol": "triangle-up",
                        "size": 12,
                        "color": "#00c853",
                    },
                )
            )
        if sell_signals:
            fig.add_trace(
                go.Scatter(
                    x=[s.timestamp for s in sell_signals],
                    y=[_find_price_at(candles, s.timestamp) for s in sell_signals],
                    mode="markers",
                    name="Sell",
                    marker={
                        "symbol": "triangle-down",
                        "size": 12,
                        "color": "#ff1744",
                    },
                )
            )

    fig.update_layout(
        template="plotly_dark",
        title="Price Chart",
        xaxis_title="Time",
        yaxis_title="Price ($)",
        paper_bgcolor="#16161d",
        plot_bgcolor="#16161d",
        xaxis_rangeslider_visible=False,
        yaxis2={
            "title": "Volume",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "range": [0, max((c.volume for c in candles), default=1) * 4],
        },
        margin={"l": 50, "r": 50, "t": 40, "b": 40},
    )
    return fig


def build_trade_history_table(trades: list[TradeRecord]) -> dag.AgGrid:
    data = []
    for t in trades:
        data.append(
            {
                "Symbol": t.symbol,
                "Side": t.side.value.upper(),
                "Entry": f"${t.entry_price:,.2f}",
                "Exit": f"${t.exit_price:,.2f}",
                "Qty": f"{t.quantity:.4f}",
                "PnL": f"${t.pnl:,.2f}",
                "Fee": f"${t.fee:,.4f}",
                "Strategy": t.strategy_name,
                "Opened": t.opened_at.strftime("%Y-%m-%d %H:%M"),
                "Closed": t.closed_at.strftime("%Y-%m-%d %H:%M"),
            }
        )

    # Show newest trades first
    data = list(reversed(data))

    return _grid(
        data,
        ["Symbol", "Side", "Entry", "Exit", "Qty", "PnL", "Fee", "Strategy", "Opened", "Closed"],
        row_style_conditions=[
            # PnL color wins over side color: list order = priority
            {
                "condition": "params.data.PnL.includes('-')",
                "style": {"color": "#ff1744"},
            },
            {
                "condition": "params.data.Side == 'BUY'",
                "style": {"color": "#00c853"},
            },
        ],
        height="500px",
    )


def build_performance_stats_table(stats_rows: list[dict]) -> dag.AgGrid:
    """Per-strategy performance statistics (FABLE-012).

    stats_rows: list of dicts with a "name" key plus the output of
    PortfolioManager.get_performance_stats().
    """
    def _pf(value: float) -> str:
        return "∞" if value == float("inf") else f"{value:.2f}"

    data = []
    for row in stats_rows:
        data.append(
            {
                "Strategy": row["name"],
                "Trades": row["trade_count"],
                "Win Rate": f"{row['win_rate'] * 100:.1f}%",
                "Net PnL": f"${row['net_pnl']:,.2f}",
                "Fees": f"${row['total_fees']:,.4f}",
                "Profit Factor": _pf(row["profit_factor"]),
                "Avg Win": f"${row['avg_win']:,.2f}",
                "Avg Loss": f"${row['avg_loss']:,.2f}",
                "Expectancy": f"${row['expectancy']:,.2f}",
                "Max Loss Streak": row["max_consecutive_losses"],
            }
        )

    return _grid(
        data,
        [
            "Strategy", "Trades", "Win Rate", "Net PnL", "Fees",
            "Profit Factor", "Avg Win", "Avg Loss", "Expectancy",
            "Max Loss Streak",
        ],
        row_style_conditions=[
            # First match wins in ag-grid, so the combined case comes first
            {
                "condition": "params.data.Strategy == 'TOTAL' && params.data['Net PnL'].includes('-')",
                "style": {"fontWeight": "bold", "color": "#ff1744"},
            },
            {
                "condition": "params.data.Strategy == 'TOTAL'",
                "style": {"fontWeight": "bold"},
            },
            {
                "condition": "params.data['Net PnL'].includes('-')",
                "style": {"color": "#ff1744"},
            },
        ],
    )


def _find_price_at(candles: list[Candle], timestamp) -> float:
    """Find the closest candle price to a given timestamp."""
    closest = min(candles, key=lambda c: abs((c.timestamp - timestamp).total_seconds()))
    return closest.close
