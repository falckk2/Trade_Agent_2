"""
ISSUE-043: WebSocket dropped every ~30s on quiet channels.

Root cause (measured against the demo endpoint 2026-06-10): BloFin's server
closes any connection that goes ~30s without a client PING, and inbound data
does not reliably reset that timer (a connection receiving ticker pushes but
sending no pings was closed every ~31s; connections pinging every 15s/25s
never dropped). The old listen() only pinged after a 30s receive timeout —
exactly at/after the server's deadline on quiet channels like candle1H,
which reconnect-stormed every ~36s.

Fix: ping on a fixed _PING_INTERVAL=15s cadence at the top of the listen
loop, regardless of whether messages are arriving.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.core.events import EventBus
from src.exchange.blofin_websocket import BloFinWebSocket


def _text_msg(payload: dict):
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.TEXT
    msg.data = json.dumps(payload)
    return msg


@pytest.mark.asyncio
async def test_pings_even_while_messages_flow():
    """A busy channel must still get heartbeat pings every _PING_INTERVAL."""
    ws_client = BloFinWebSocket(event_bus=EventBus())
    ws_client._PING_INTERVAL = 0.02

    ws = AsyncMock()
    ws.closed = False
    received = 0

    async def receive():
        nonlocal received
        received += 1
        if received >= 8:
            ws_client._running = False
        await asyncio.sleep(0.01)  # messages arrive faster than nothing, slower than never
        return _text_msg({"op": "pong"})

    ws.receive = receive
    ws_client._ws = ws
    ws_client._running = True

    await ws_client.listen()

    ping_calls = [
        c for c in ws.send_json.call_args_list if c.args[0] == {"op": "ping"}
    ]
    assert ping_calls, (
        "listen() must send pings on a fixed cadence even when messages "
        "are flowing — inbound data does not reset BloFin's idle timer"
    )


@pytest.mark.asyncio
async def test_pings_on_quiet_channel_via_receive_timeout():
    """A silent channel gets pings from the timeout path."""
    ws_client = BloFinWebSocket(event_bus=EventBus())
    ws_client._PING_INTERVAL = 0.02

    ws = AsyncMock()
    ws.closed = False
    timeouts = 0

    async def receive():
        nonlocal timeouts
        timeouts += 1
        if timeouts >= 4:
            ws_client._running = False
        await asyncio.sleep(1)  # never returns within the ping interval
        return _text_msg({})

    ws.receive = receive
    ws_client._ws = ws
    ws_client._running = True

    await asyncio.wait_for(ws_client.listen(), timeout=5)

    ping_calls = [
        c for c in ws.send_json.call_args_list if c.args[0] == {"op": "ping"}
    ]
    assert len(ping_calls) >= 2
