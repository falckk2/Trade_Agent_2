import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp

from src.core.enums import EventType, TimeFrame
from src.core.events import Event, EventBus
from src.core.models import Candle
from src.exchange.blofin_exchange import _TIMEFRAME_MAP

_BAR_TO_TIMEFRAME = {v: k for k, v in _TIMEFRAME_MAP.items()}

logger = logging.getLogger(__name__)

_WS_PUBLIC_URL = "wss://openapi.blofin.com/ws/public"
_WS_DEMO_PUBLIC_URL = "wss://demo-trading-openapi.blofin.com/ws/public"


class BloFinWebSocket:
    """WebSocket client for real-time BloFin market data."""

    def __init__(
        self,
        event_bus: EventBus,
        demo_mode: bool = True,
    ) -> None:
        self._event_bus = event_bus
        self._demo_mode = demo_mode
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._subscriptions: list[dict] = []
        # FABLE-011: reconnect-storm detection — alert when reconnects pile up
        # within a window, at most once per window so the alert itself
        # cannot spam.
        self._reconnect_times: deque[float] = deque(maxlen=20)
        self._storm_threshold = 5
        self._storm_window_seconds = 600.0
        self._last_storm_alert = 0.0

    @property
    def _url(self) -> str:
        return _WS_DEMO_PUBLIC_URL if self._demo_mode else _WS_PUBLIC_URL

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)
        self._running = True
        logger.info("WebSocket connected to %s", self._url)

    async def disconnect(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("WebSocket disconnected")

    async def subscribe_candles(self, symbol: str, timeframe: TimeFrame) -> None:
        bar = _TIMEFRAME_MAP.get(timeframe, "5m")
        sub = {
            "op": "subscribe",
            "args": [{"channel": f"candle{bar}", "instId": symbol}],
        }
        self._subscriptions.append(sub)
        if self._ws and not self._ws.closed:
            await self._ws.send_json(sub)
            logger.info("Subscribed to %s candles for %s", bar, symbol)

    async def listen(self) -> None:
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        while self._running:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=30)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    logger.warning("WebSocket closed/error, reconnecting...")
                    await self._reconnect()
            except asyncio.TimeoutError:
                # Send BloFin heartbeat ping (JSON format required; plain "ping"
                # string causes the server to close the connection)
                if self._ws and not self._ws.closed:
                    await self._ws.send_json({"op": "ping"})
            except Exception:
                logger.exception("WebSocket listen error")
                if self._running:
                    await self._reconnect()

    async def _reconnect(self) -> None:
        delay = 5
        max_delay = 60
        attempt = 0
        while self._running:
            attempt += 1
            logger.info("WebSocket reconnect attempt %d (delay=%ds)", attempt, delay)
            await asyncio.sleep(delay)
            try:
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                if self._session and not self._session.closed:
                    self._ws = await self._session.ws_connect(self._url)
                else:
                    # Session was closed — open a new one
                    self._session = aiohttp.ClientSession()
                    self._ws = await self._session.ws_connect(self._url)
                for sub in self._subscriptions:
                    await self._ws.send_json(sub)
                logger.info("WebSocket reconnected after %d attempt(s)", attempt)
                await self._check_reconnect_storm()
                return
            except Exception:
                logger.exception("Reconnection attempt %d failed", attempt)
                delay = min(delay * 2, max_delay)

    async def _check_reconnect_storm(self) -> None:
        """Alert when reconnects exceed the threshold within the window (FABLE-011)."""
        now = time.monotonic()
        self._reconnect_times.append(now)
        recent = [
            t for t in self._reconnect_times
            if now - t <= self._storm_window_seconds
        ]
        if (
            len(recent) >= self._storm_threshold
            and now - self._last_storm_alert > self._storm_window_seconds
        ):
            self._last_storm_alert = now
            await self._event_bus.publish(
                Event(
                    event_type=EventType.ALERT,
                    payload={
                        "level": "warning",
                        "message": (
                            f"WebSocket reconnect storm: {len(recent)} reconnects "
                            f"in the last {int(self._storm_window_seconds / 60)} min — "
                            f"candle feed is unstable (see ISSUE-043)."
                        ),
                    },
                )
            )

    async def _handle_message(self, data: dict) -> None:
        # Heartbeat pong — acknowledge and return
        if data.get("op") == "pong":
            return

        if "data" not in data:
            return

        channel = data.get("arg", {}).get("channel", "")
        if channel.startswith("candle"):
            bar = channel[len("candle"):]
            timeframe = _BAR_TO_TIMEFRAME.get(bar)
            for item in data["data"]:
                candle = Candle(
                    timestamp=datetime.fromtimestamp(
                        int(item[0]) / 1000, tz=timezone.utc
                    ),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                )
                event = Event(
                    event_type=EventType.CANDLE_UPDATE,
                    payload={
                        "symbol": data["arg"]["instId"],
                        "candle": candle,
                        "timeframe": timeframe,
                    },
                )
                await self._event_bus.publish(event)
