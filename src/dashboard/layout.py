"""Dashboard layout with three tabs: Overview, Strategy Performance, Market."""

from dash import dcc, html


def create_layout(
    symbols: list[str],
    strategy_names: list[str],
    refresh_interval_ms: int = 2000,
) -> html.Div:
    return html.Div(
        style={
            "backgroundColor": "#0d0d1a",
            "minHeight": "100vh",
            "color": "#fff",
            "fontFamily": "'Segoe UI', sans-serif",
        },
        children=[
            # Header
            html.Div(
                style={
                    "padding": "16px 24px",
                    "backgroundColor": "#16161d",
                    "borderBottom": "1px solid #333",
                    "display": "flex",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                },
                children=[
                    html.H1(
                        "Trade Agent 2",
                        style={"margin": "0", "fontSize": "24px"},
                    ),
                    html.Div(
                        id="connection-status",
                        children="Connected",
                        style={
                            "color": "#00c853",
                            "fontSize": "14px",
                        },
                    ),
                ],
            ),
            # Auto-refresh interval (driven by config dashboard.refresh_interval_ms)
            dcc.Interval(
                id="refresh-interval",
                interval=refresh_interval_ms,
                n_intervals=0,
            ),
            # Tabs
            dcc.Tabs(
                id="main-tabs",
                value="overview",
                style={"backgroundColor": "#16161d"},
                colors={
                    "border": "#333",
                    "primary": "#00bcd4",
                    "background": "#1e1e2f",
                },
                children=[
                    # Tab 1: Overview
                    dcc.Tab(
                        label="Overview",
                        value="overview",
                        style=_tab_style(),
                        selected_style=_tab_selected_style(),
                        children=[
                            html.Div(
                                style={"padding": "24px"},
                                children=[
                                    # Metric cards row
                                    html.Div(
                                        id="metric-cards",
                                        style={
                                            "display": "flex",
                                            "gap": "16px",
                                            "flexWrap": "wrap",
                                            "marginBottom": "24px",
                                        },
                                    ),
                                    # Equity chart
                                    dcc.Graph(
                                        id="equity-chart",
                                        style={"marginBottom": "24px"},
                                    ),
                                    # Positions table
                                    html.H3(
                                        "Active Positions",
                                        style={"marginBottom": "12px"},
                                    ),
                                    html.Div(id="positions-table"),
                                ],
                            ),
                        ],
                    ),
                    # Tab 2: Strategy Performance
                    dcc.Tab(
                        label="Strategy Performance",
                        value="strategies",
                        style=_tab_style(),
                        selected_style=_tab_selected_style(),
                        children=[
                            html.Div(
                                style={"padding": "24px"},
                                children=[
                                    # Strategy selector
                                    html.Div(
                                        style={
                                            "display": "flex",
                                            "gap": "16px",
                                            "alignItems": "center",
                                            "marginBottom": "24px",
                                        },
                                        children=[
                                            html.Label("Strategy:"),
                                            dcc.Dropdown(
                                                id="strategy-selector",
                                                options=[
                                                    {"label": "All", "value": "all"}
                                                ]
                                                + [
                                                    {"label": n, "value": n}
                                                    for n in strategy_names
                                                ],
                                                value="all",
                                                style={
                                                    "width": "300px",
                                                    "backgroundColor": "#1e1e2f",
                                                    "color": "#000",
                                                },
                                            ),
                                        ],
                                    ),
                                    # P&L bar chart
                                    dcc.Graph(
                                        id="pnl-bar-chart",
                                        style={"marginBottom": "24px"},
                                    ),
                                    # Strategy equity curves
                                    dcc.Graph(
                                        id="strategy-equity-chart",
                                        style={"marginBottom": "24px"},
                                    ),
                                    # Trade history
                                    html.H3(
                                        "Trade History",
                                        style={"marginBottom": "12px"},
                                    ),
                                    html.Div(id="trade-history-table"),
                                ],
                            ),
                        ],
                    ),
                    # Tab 3: Strategies
                    dcc.Tab(
                        label="Strategies",
                        value="strategy-control",
                        style=_tab_style(),
                        selected_style=_tab_selected_style(),
                        children=[
                            html.Div(
                                style={"padding": "24px"},
                                children=[
                                    html.H3(
                                        "Strategy Control",
                                        style={"marginBottom": "8px"},
                                    ),
                                    html.P(
                                        "All strategies are disabled by default. "
                                        "Click Start to enable a strategy.",
                                        style={
                                            "color": "#888",
                                            "fontSize": "13px",
                                            "marginBottom": "24px",
                                        },
                                    ),
                                    html.Div(
                                        id="strategy-control-panel",
                                        style={
                                            "display": "flex",
                                            "flexWrap": "wrap",
                                            "gap": "16px",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    # Tab 4: Market
                    dcc.Tab(
                        label="Market",
                        value="market",
                        style=_tab_style(),
                        selected_style=_tab_selected_style(),
                        children=[
                            html.Div(
                                style={"padding": "24px"},
                                children=[
                                    # Symbol selector
                                    html.Div(
                                        style={
                                            "display": "flex",
                                            "gap": "16px",
                                            "alignItems": "center",
                                            "marginBottom": "24px",
                                        },
                                        children=[
                                            html.Label("Symbol:"),
                                            dcc.Dropdown(
                                                id="symbol-selector",
                                                options=[
                                                    {"label": s, "value": s}
                                                    for s in symbols
                                                ],
                                                value=symbols[0] if symbols else None,
                                                style={
                                                    "width": "300px",
                                                    "backgroundColor": "#1e1e2f",
                                                    "color": "#000",
                                                },
                                            ),
                                        ],
                                    ),
                                    # Candlestick chart
                                    dcc.Graph(
                                        id="candlestick-chart",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _tab_style() -> dict:
    return {
        "backgroundColor": "#1e1e2f",
        "color": "#888",
        "border": "1px solid #333",
        "padding": "12px",
    }


def _tab_selected_style() -> dict:
    return {
        "backgroundColor": "#16161d",
        "color": "#fff",
        "border": "1px solid #00bcd4",
        "borderTop": "2px solid #00bcd4",
        "padding": "12px",
    }
