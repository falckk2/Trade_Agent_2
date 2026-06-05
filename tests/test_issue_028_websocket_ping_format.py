"""
ISSUE-028: Targeted tests for the WebSocket ping-format and pong-handling fix.

Before the fix (2026-06-01):
- asyncio.TimeoutError handler called `await self._ws.send_str("ping")` — a plain
  text frame rejected by BloFin, causing the server to close the connection every ~30s.
- `_handle_message` had no explicit branch for `{"op": "pong"}` — pong responses
  fell through the `if "data" not in data: return` guard silently.

After the fix:
1. The TimeoutError handler calls `await self._ws.send_json({"op": "ping"})`.
2. `_handle_message` returns early when `data.get("op") == "pong"`.
3. Pong messages do NOT publish any event to the EventBus.
4. Normal candle messages still flow through correctly (regression guard).

Tests 1, 2, 8 (listen()-loop behaviour) use AST/source inspection rather than running
the async loop — patching asyncio.wait_for globally hangs under pytest-asyncio on WSL.
Tests 3-7 call _handle_message() directly and are safe to run as normal async tests.
"""

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.enums import EventType
from src.core.events import Event, EventBus
from src.exchange.blofin_websocket import BloFinWebSocket

_WS_SRC = Path("/home/rehan/Trade_Agent_2/src/exchange/blofin_websocket.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws(demo_mode: bool = True) -> BloFinWebSocket:
    """Construct a BloFinWebSocket with a real EventBus."""
    bus = EventBus()
    ws = BloFinWebSocket(event_bus=bus, demo_mode=demo_mode)
    return ws


def _make_candle_message(symbol: str = "BTC-USDT", bar: str = "5m") -> dict:
    """Return a minimal BloFin candle WebSocket message."""
    ts_ms = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    return {
        "arg": {"channel": f"candle{bar}", "instId": symbol},
        "data": [
            [str(ts_ms), "67000", "67500", "66800", "67200", "10.5", "0"],
        ],
    }


def _ws_source() -> str:
    return _WS_SRC.read_text()


def _ws_ast():
    return ast.parse(_ws_source())


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestWebSocketPingFormat:
    """Verify ping sends JSON and pong is silently discarded."""

    # ------------------------------------------------------------------
    # Test 1 (source inspection): TimeoutError handler uses send_json, not send_str
    # ------------------------------------------------------------------

    def test_timeout_sends_json_ping_not_plain_string(self):
        """listen() source must call send_json({"op": "ping"}), never send_str("ping")."""
        source = _ws_source()

        # The fix must be present
        assert 'send_json({"op": "ping"})' in source, (
            'blofin_websocket.py does not contain send_json({"op": "ping"}) — '
            "ISSUE-028 fix missing in TimeoutError handler"
        )
        # The old broken form must be gone
        assert 'send_str("ping")' not in source, (
            'blofin_websocket.py still contains send_str("ping") — '
            "ISSUE-028 fix was not applied or was reverted"
        )

    # ------------------------------------------------------------------
    # Test 2 (AST inspection): send_json call's argument is a dict {"op": "ping"}
    # ------------------------------------------------------------------

    def test_ping_payload_is_dict_not_string(self):
        """AST confirms the send_json call in listen() receives a dict, not a bare string."""
        tree = _ws_ast()

        found_dict_ping = False
        for node in ast.walk(tree):
            # Looking for: self._ws.send_json({"op": "ping"})
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "send_json"):
                continue
            if not node.args:
                continue
            arg = node.args[0]
            # Must be a dict literal with exactly one key "op" → value "ping"
            if not isinstance(arg, ast.Dict):
                continue
            if len(arg.keys) == 1:
                key = arg.keys[0]
                val = arg.values[0]
                if (
                    isinstance(key, ast.Constant) and key.value == "op"
                    and isinstance(val, ast.Constant) and val.value == "ping"
                ):
                    found_dict_ping = True
                    break

        assert found_dict_ping, (
            'No send_json({"op": "ping"}) call found in blofin_websocket.py — '
            "ISSUE-028 fix not present"
        )

    # ------------------------------------------------------------------
    # Test 3: _handle_message with pong returns cleanly without error
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_message_pong_returns_cleanly(self):
        """_handle_message({"op": "pong"}) must return without raising."""
        ws_obj = _make_ws()
        # Should not raise under any circumstances
        await ws_obj._handle_message({"op": "pong"})

    # ------------------------------------------------------------------
    # Test 4: _handle_message with pong does NOT publish any event
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_message_pong_does_not_publish_event(self):
        """A pong message must not publish any event to the EventBus."""
        bus = EventBus()
        ws_obj = BloFinWebSocket(event_bus=bus, demo_mode=True)

        published_events: list[Event] = []

        async def capture(event: Event) -> None:
            published_events.append(event)

        bus.subscribe(EventType.CANDLE_UPDATE, capture)

        await ws_obj._handle_message({"op": "pong"})

        # Give event loop a chance to run any scheduled tasks
        await asyncio.sleep(0)

        assert published_events == [], (
            f"Pong message must not publish any events; got: {published_events}"
        )

    # ------------------------------------------------------------------
    # Test 5: _handle_message with pong does not attempt candle processing
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_message_pong_no_candle_processing(self):
        """Pong message must not attempt to parse candle data or access 'data' key."""
        ws_obj = _make_ws()

        # A pong with no "data" key — must not raise KeyError or similar
        await ws_obj._handle_message({"op": "pong"})

        # Also a pong that happens to carry a "data" field — must still be discarded
        # (early return before any data processing)
        await ws_obj._handle_message({"op": "pong", "data": [["bad", "data"]]})

    # ------------------------------------------------------------------
    # Test 6 (regression guard): Normal candle message still flows through
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_normal_candle_message_still_processed(self):
        """A normal candle WS message must publish a CANDLE_UPDATE event (regression guard)."""
        bus = EventBus()
        ws_obj = BloFinWebSocket(event_bus=bus, demo_mode=True)

        published_events: list[Event] = []

        async def capture(event: Event) -> None:
            published_events.append(event)

        bus.subscribe(EventType.CANDLE_UPDATE, capture)

        msg = _make_candle_message(symbol="BTC-USDT", bar="5m")
        await ws_obj._handle_message(msg)

        # Give event loop a chance to deliver the event
        await asyncio.sleep(0)

        assert len(published_events) == 1, (
            f"Expected 1 CANDLE_UPDATE event from candle message, got {len(published_events)}"
        )
        event = published_events[0]
        assert event.event_type == EventType.CANDLE_UPDATE
        assert event.payload["symbol"] == "BTC-USDT"
        candle = event.payload["candle"]
        assert candle.close == 67200.0
        assert candle.open == 67000.0

    # ------------------------------------------------------------------
    # Test 7: Non-pong op messages (e.g. subscribe ack) are also not
    #         processed as candles — just silently dropped (existing guard)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_message_subscribe_ack_no_event(self):
        """Subscribe acknowledgement (no 'data' key) must not publish any event."""
        bus = EventBus()
        ws_obj = BloFinWebSocket(event_bus=bus, demo_mode=True)

        published_events: list[Event] = []

        async def capture(event: Event) -> None:
            published_events.append(event)

        bus.subscribe(EventType.CANDLE_UPDATE, capture)

        # Typical BloFin subscribe ack (no "data" key)
        await ws_obj._handle_message({
            "event": "subscribe",
            "arg": {"channel": "candle5m", "instId": "BTC-USDT"},
        })

        await asyncio.sleep(0)

        assert published_events == [], (
            "Subscribe-ack message must not publish any events"
        )

    # ------------------------------------------------------------------
    # Test 8 (source inspection): closed-guard precedes send_json in listen()
    # ------------------------------------------------------------------

    def test_timeout_skips_ping_when_ws_closed(self):
        """Source confirms the closed-guard `if self._ws and not self._ws.closed`
        wraps the send_json call so a closed socket is never pinged."""
        source = _ws_source()

        # Both the guard and the send_json must appear in the TimeoutError block.
        # We check they coexist in the source — the relative ordering is confirmed
        # by the guard appearing before send_json.
        guard = "if self._ws and not self._ws.closed:"
        send = 'send_json({"op": "ping"})'

        assert guard in source, (
            f"Closed-socket guard '{guard}' not found in blofin_websocket.py"
        )
        guard_pos = source.index(guard)
        send_pos = source.index(send)
        assert guard_pos < send_pos, (
            "Closed-socket guard must appear before send_json in source — "
            f"guard at char {guard_pos}, send_json at char {send_pos}"
        )
