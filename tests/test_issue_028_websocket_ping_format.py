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
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.core.enums import EventType, TimeFrame
from src.core.events import Event, EventBus
from src.exchange.blofin_websocket import BloFinWebSocket


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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestWebSocketPingFormat:
    """Verify ping sends JSON and pong is silently discarded."""

    # ------------------------------------------------------------------
    # Test 1: TimeoutError handler sends JSON {"op": "ping"}, not a string
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_timeout_sends_json_ping_not_plain_string(self):
        """When a 30s timeout fires, send_json({"op": "ping"}) is called, not send_str."""
        ws_obj = _make_ws()

        mock_ws = AsyncMock()
        mock_ws.closed = False
        ws_obj._ws = mock_ws
        ws_obj._running = True

        # Simulate: first receive raises TimeoutError, then next returns CLOSED to stop loop
        import aiohttp

        closed_msg = MagicMock()
        closed_msg.type = aiohttp.WSMsgType.CLOSED

        # wait_for: first call → TimeoutError, second call → return closed msg to stop loop
        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return closed_msg

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            with patch.object(ws_obj, "_reconnect", new_callable=AsyncMock):
                await ws_obj.listen()

        # Must have called send_json with exactly {"op": "ping"}
        mock_ws.send_json.assert_called_once_with({"op": "ping"})
        # Must NOT have called send_str with any argument
        mock_ws.send_str.assert_not_called()

    # ------------------------------------------------------------------
    # Test 2: send_json receives a dict, not a plain string "ping"
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_ping_payload_is_dict_not_string(self):
        """The argument to send_json on timeout must be a dict (JSON object)."""
        ws_obj = _make_ws()

        mock_ws = AsyncMock()
        mock_ws.closed = False
        ws_obj._ws = mock_ws
        ws_obj._running = True

        import aiohttp

        closed_msg = MagicMock()
        closed_msg.type = aiohttp.WSMsgType.CLOSED

        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return closed_msg

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            with patch.object(ws_obj, "_reconnect", new_callable=AsyncMock):
                await ws_obj.listen()

        # The argument must be a dict (not a str)
        args, _ = mock_ws.send_json.call_args
        payload = args[0]
        assert isinstance(payload, dict), (
            f"send_json should receive a dict, got {type(payload).__name__}: {payload!r}"
        )
        assert payload == {"op": "ping"}, (
            f"Ping payload must be exactly {{'op': 'ping'}}, got {payload!r}"
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
    # Test 8: WS is closed when timeout fires — send_json is NOT called
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_timeout_skips_ping_when_ws_closed(self):
        """If _ws.closed is True when timeout fires, send_json must not be called."""
        ws_obj = _make_ws()

        mock_ws = AsyncMock()
        mock_ws.closed = True  # WS already closed
        ws_obj._ws = mock_ws
        ws_obj._running = True

        import aiohttp

        closed_msg = MagicMock()
        closed_msg.type = aiohttp.WSMsgType.CLOSED

        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return closed_msg

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            with patch.object(ws_obj, "_reconnect", new_callable=AsyncMock):
                await ws_obj.listen()

        # When ws.closed is True, the guard `if self._ws and not self._ws.closed`
        # should prevent send_json from being called
        mock_ws.send_json.assert_not_called()
