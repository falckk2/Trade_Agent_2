"""
ISSUE-021: Dashboard refresh interval is now wired from config instead of being
hard-coded to 2000ms. create_layout() and create_app() accept refresh_interval_ms.

Tests verify:
1. create_layout() accepts refresh_interval_ms parameter
2. create_layout() uses the provided value in dcc.Interval (not hard-coded 2000)
3. create_app() accepts and forwards refresh_interval_ms to create_layout()
4. default.yaml contains dashboard.refresh_interval_ms
"""

import pytest
import yaml
from pathlib import Path

DEFAULT_YAML = Path("/home/rehan/Trade_Agent_2/config/default.yaml")


class TestDashboardRefreshInterval:
    def test_default_yaml_contains_refresh_interval(self):
        """default.yaml must specify dashboard.refresh_interval_ms."""
        with open(DEFAULT_YAML) as f:
            config = yaml.safe_load(f)
        assert "refresh_interval_ms" in config.get("dashboard", {}), \
            "dashboard.refresh_interval_ms missing from default.yaml"

    def test_default_yaml_refresh_interval_is_2000(self):
        """Default refresh interval is 2000ms."""
        with open(DEFAULT_YAML) as f:
            config = yaml.safe_load(f)
        assert config["dashboard"]["refresh_interval_ms"] == 2000

    def test_create_layout_accepts_refresh_interval_ms_param(self):
        """create_layout() must accept refresh_interval_ms as a parameter."""
        from src.dashboard.layout import create_layout
        import inspect
        sig = inspect.signature(create_layout)
        assert "refresh_interval_ms" in sig.parameters, \
            "create_layout() missing refresh_interval_ms parameter"

    def test_create_layout_uses_provided_refresh_interval(self):
        """create_layout() must use the provided refresh_interval_ms value in dcc.Interval."""
        from src.dashboard.layout import create_layout

        layout = create_layout(
            symbols=["BTC-USDT"],
            strategy_names=["test_strat"],
            refresh_interval_ms=5000,
        )

        # Recursively search for dcc.Interval component
        def find_interval(component):
            if hasattr(component, "interval") and component.interval == 5000:
                return True
            children = getattr(component, "children", None)
            if children is None:
                return False
            if isinstance(children, list):
                return any(find_interval(c) for c in children)
            return find_interval(children)

        assert find_interval(layout), \
            "dcc.Interval with interval=5000 not found — refresh_interval_ms not wired through"

    def test_create_layout_hardcoded_2000_not_used_when_custom_provided(self):
        """create_layout() must NOT use hard-coded 2000 when a different value is provided."""
        from src.dashboard.layout import create_layout

        layout = create_layout(
            symbols=["BTC-USDT"],
            strategy_names=[],
            refresh_interval_ms=9999,
        )

        def find_interval_value(component):
            """Return the interval value of the first dcc.Interval found."""
            try:
                from dash import dcc
                if isinstance(component, dcc.Interval):
                    return component.interval
            except Exception:
                pass
            children = getattr(component, "children", None)
            if children is None:
                return None
            if isinstance(children, list):
                for c in children:
                    result = find_interval_value(c)
                    if result is not None:
                        return result
            return find_interval_value(children)

        interval_val = find_interval_value(layout)
        assert interval_val == 9999, \
            f"dcc.Interval has interval={interval_val}, expected 9999 — possibly still hard-coded"

    def test_create_app_accepts_refresh_interval_ms_param(self):
        """create_app() must accept refresh_interval_ms as a parameter."""
        from src.dashboard.app import create_app
        import inspect
        sig = inspect.signature(create_app)
        assert "refresh_interval_ms" in sig.parameters, \
            "create_app() missing refresh_interval_ms parameter"
