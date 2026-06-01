"""Strategy control panel UI builders.

Separated from callbacks.py so that UI construction (SRP) is isolated
from callback wiring logic.
"""

from collections import defaultdict

from dash import html

from src.core.models import utcnow
from src.dashboard.callbacks import SIGNAL_COLORS


def _signal_display(signal) -> tuple[str, str, str]:
    """Return (color, strength_pct, age_str) for a signal."""
    color = SIGNAL_COLORS.get(signal.signal_type.value, "#aaa")
    strength_pct = f"{signal.strength * 100:.0f}%"
    try:
        age_s = int((utcnow() - signal.timestamp).total_seconds())
        age_str = f"{age_s}s ago"
    except Exception:
        age_str = ""
    return color, strength_pct, age_str


def build_strategy_control_panel(engine) -> list:
    status = engine.get_strategy_status()

    symbol_groups: dict[str, list[str]] = defaultdict(list)
    for name, info in status.items():
        for sym in info["symbols"]:
            symbol_groups[sym].append(name)

    cards = []
    for name, info in status.items():
        enabled = info["enabled"]
        weight = info["weight"]
        symbols = info["symbols"]
        color = "#00c853" if enabled else "#888"
        label = "Stop" if enabled else "Start"
        btn_color = "#c62828" if enabled else "#1565c0"

        peers = []
        for sym in symbols:
            for peer in symbol_groups[sym]:
                if peer != name and status[peer]["enabled"] and peer not in peers:
                    peers.append(peer)

        peer_note = (
            [html.Div(
                f"Composited with: {', '.join(peers)}",
                style={"color": "#ff9800", "fontSize": "11px"},
            )]
            if enabled and peers else []
        )

        last_signal = info.get("last_signal")
        signal_badge = build_signal_badge(last_signal)

        cards.append(
            html.Div(
                style={
                    "backgroundColor": "#16161d",
                    "border": f"1px solid {color}",
                    "borderRadius": "8px",
                    "padding": "16px",
                    "minWidth": "220px",
                    "display": "flex",
                    "flexDirection": "column",
                    "gap": "8px",
                },
                children=[
                    html.Div(name, style={"fontWeight": "bold", "fontSize": "14px"}),
                    html.Div(", ".join(symbols), style={"color": "#aaa", "fontSize": "12px"}),
                    html.Div(f"Weight: {weight:.2f}", style={"color": "#aaa", "fontSize": "12px"}),
                    html.Div(
                        "RUNNING" if enabled else "STOPPED",
                        style={"color": color, "fontSize": "12px", "fontWeight": "bold", "letterSpacing": "1px"},
                    ),
                    *peer_note,
                    signal_badge,
                    html.Button(
                        label,
                        id={"type": "strategy-toggle-btn", "index": name},
                        n_clicks=0,
                        style={
                            "backgroundColor": btn_color,
                            "color": "#fff",
                            "border": "none",
                            "borderRadius": "4px",
                            "padding": "6px 16px",
                            "cursor": "pointer",
                            "fontWeight": "bold",
                        },
                    ),
                ],
            )
        )
    return cards


def build_composite_signals_panel(engine) -> html.Div:
    composite_signals = engine.get_composite_signals()
    if not composite_signals:
        return html.Div()

    rows = []
    for symbol, signal in sorted(composite_signals.items()):
        sig_color, strength_pct, age_str = _signal_display(signal)

        rows.append(
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "12px",
                    "padding": "10px 16px",
                    "backgroundColor": "#1e1e2f",
                    "borderRadius": "6px",
                    "marginBottom": "8px",
                },
                children=[
                    html.Span(symbol, style={"fontWeight": "bold", "minWidth": "100px"}),
                    html.Span(
                        signal.signal_type.value.upper(),
                        style={
                            "backgroundColor": sig_color,
                            "color": "#fff",
                            "borderRadius": "3px",
                            "padding": "2px 10px",
                            "fontSize": "12px",
                            "fontWeight": "bold",
                            "letterSpacing": "1px",
                        },
                    ),
                    html.Span(f"strength {strength_pct}", style={"color": "#aaa", "fontSize": "12px"}),
                    html.Span(age_str, style={"color": "#555", "fontSize": "11px"}),
                ],
            )
        )

    return html.Div([
        html.H4(
            "Composite Output (per symbol)",
            style={"color": "#00bcd4", "marginBottom": "10px", "fontSize": "14px"},
        ),
        *rows,
    ])


def build_signal_badge(signal) -> html.Div:
    if signal is None:
        return html.Div(
            "No signal yet",
            style={"color": "#555", "fontSize": "12px", "fontStyle": "italic"},
        )

    sig_color, strength_pct, _age = _signal_display(signal)
    age_str = f"  ({_age})" if _age else ""

    return html.Div(
        style={"display": "flex", "alignItems": "center", "gap": "6px"},
        children=[
            html.Span(
                signal.signal_type.value.upper(),
                style={
                    "backgroundColor": sig_color,
                    "color": "#fff",
                    "borderRadius": "3px",
                    "padding": "2px 7px",
                    "fontSize": "11px",
                    "fontWeight": "bold",
                    "letterSpacing": "1px",
                },
            ),
            html.Span(
                f"strength {strength_pct}{age_str}",
                style={"color": "#aaa", "fontSize": "11px"},
            ),
        ],
    )
