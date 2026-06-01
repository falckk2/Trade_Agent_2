"""
ISSUE-006: BloFinWebSocket is now constructed and wired in build_components() in main.py.

Tests verify:
1. build_components() returns a 'ws_client' key (BloFinWebSocket is constructed)
2. The ws_client is a BloFinWebSocket instance
3. BloFinWebSocket imports correctly and is accessible

Note: Full WebSocket connection tests require live credentials and are skipped.
The intent here is to verify the wiring is in place structurally.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestWebSocketWiredInMain:
    def test_blofin_websocket_importable(self):
        """BloFinWebSocket must be importable (not dead code)."""
        from src.exchange.blofin_websocket import BloFinWebSocket
        assert BloFinWebSocket is not None

    def test_blofin_websocket_constructible(self):
        """BloFinWebSocket can be constructed with event_bus and demo_mode."""
        from src.exchange.blofin_websocket import BloFinWebSocket
        from src.core.events import EventBus

        event_bus = EventBus()
        ws = BloFinWebSocket(event_bus=event_bus, demo_mode=True)
        assert ws is not None

    def test_main_imports_blofin_websocket(self):
        """Verify main.py imports BloFinWebSocket (regression guard for re-removal)."""
        import importlib
        import importlib.util
        import ast

        with open("/home/rehan/Trade_Agent_2/main.py") as f:
            source = f.read()

        tree = ast.parse(source)
        imports_ws = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if "blofin_websocket" in (node.module or ""):
                    imports_ws = True
                    break
        assert imports_ws, "main.py no longer imports BloFinWebSocket — ISSUE-006 may have regressed"

    def test_build_components_returns_ws_client(self):
        """build_components() must include 'ws_client' in its return dict."""
        import src.exchange.blofin_exchange as exc_module
        exc_module._active_instances = 0
        try:
            with patch("src.exchange.blofin_exchange.BloFinExchange.connect", new_callable=AsyncMock), \
                 patch("src.exchange.blofin_exchange.BloFinExchange._load_instrument_specs"):
                import main as main_mod
                # Minimal config that exercises the wiring path
                config = {
                    "exchange": {"demo_mode": True, "symbols": ["BTC-USDT"]},
                    "risk": {},
                    "engine": {"timeframe": "5m", "candle_limit": 200},
                    "data": {"data_dir": "/tmp/test_data"},
                }
                with patch.dict("os.environ", {
                    "BLOFIN_API_KEY": "k", "BLOFIN_SECRET": "s", "BLOFIN_PASSPHRASE": "p"
                }):
                    components = main_mod.build_components(config)

                assert "ws_client" in components, "build_components() did not return 'ws_client'"
                from src.exchange.blofin_websocket import BloFinWebSocket
                assert isinstance(components["ws_client"], BloFinWebSocket)
        finally:
            exc_module._active_instances = 0
