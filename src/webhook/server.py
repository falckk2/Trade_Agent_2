"""TradingView alert webhook receiver (FABLE-017).

A small aiohttp server that runs inside the bot's asyncio loop. TradingView
alerts POST a JSON payload here; after shared-secret authentication the
signal is injected into the matching WebhookSignalStrategy and consumed by
the engine's normal tick pipeline.

Expected payload (set as the TradingView alert "Message"):

    {
      "secret": "<WEBHOOK_SECRET>",
      "strategy": "tv_marketcipher_btc",
      "symbol": "BTC-USDT",
      "action": "long",        // long|buy / short|sell / close|exit
      "strength": 0.8           // optional, 0..1, default 0.8
    }

Security model:
- Shared secret in the body (TradingView cannot sign requests or set custom
  headers), compared constant-time. The server refuses to start without one.
- Optional source-IP allowlist for direct-exposure deployments. Behind a
  reverse proxy (the recommended VPS setup — TradingView only delivers to
  ports 80/443) `request.remote` is the proxy, so allowlist at the proxy
  layer instead and leave `allowed_ips` empty here.
- Body size capped well below aiohttp's default (alerts are tiny).
"""

import hmac
import logging

from aiohttp import web

from src.strategies.webhook import WebhookSignalStrategy, parse_action

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 8 * 1024


class TradingViewWebhookServer:
    """Receives TradingView alert POSTs and routes them to webhook strategies."""

    def __init__(
        self,
        secret: str,
        host: str = "0.0.0.0",
        port: int = 8080,
        path: str = "/webhook/tradingview",
        allowed_ips: list[str] | None = None,
    ) -> None:
        if not secret:
            raise ValueError(
                "Webhook server requires a non-empty shared secret "
                "(set WEBHOOK_SECRET)"
            )
        self._secret = secret
        self._host = host
        self._port = port
        self._path = path
        self._allowed_ips = set(allowed_ips or [])
        # strategy name -> (strategy instance, expected symbol)
        self._routes: dict[str, tuple[WebhookSignalStrategy, str]] = {}
        self._runner: web.AppRunner | None = None

    def register(self, strategy: WebhookSignalStrategy, symbol: str) -> None:
        """Make a webhook strategy addressable by name from alert payloads."""
        self._routes[strategy.name] = (strategy, symbol)
        logger.info(
            "Webhook route registered: strategy '%s' for %s", strategy.name, symbol
        )

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_BODY_BYTES)
        app.router.add_post(self._path, self._handle_alert)
        app.router.add_get("/health", self._handle_health)
        return app

    async def start(self) -> None:
        self._runner = web.AppRunner(self.build_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(
            "TradingView webhook server listening on %s:%d%s (%d strategies routed)",
            self._host,
            self._port,
            self._path,
            len(self._routes),
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("TradingView webhook server stopped")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_alert(self, request: web.Request) -> web.Response:
        remote = request.remote or "unknown"

        if self._allowed_ips and remote not in self._allowed_ips:
            logger.warning("Webhook rejected: source IP %s not allowlisted", remote)
            return web.json_response({"error": "forbidden"}, status=403)

        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except Exception:
            logger.warning("Webhook rejected: invalid JSON from %s", remote)
            return web.json_response({"error": "invalid JSON"}, status=400)

        if not hmac.compare_digest(str(payload.get("secret", "")), self._secret):
            logger.warning("Webhook rejected: bad secret from %s", remote)
            return web.json_response({"error": "unauthorized"}, status=401)

        strategy_name = str(payload.get("strategy", ""))
        route = self._routes.get(strategy_name)
        if route is None:
            logger.warning(
                "Webhook rejected: unknown strategy '%s' from %s",
                strategy_name,
                remote,
            )
            return web.json_response({"error": "unknown strategy"}, status=404)
        strategy, expected_symbol = route

        signal_type = parse_action(str(payload.get("action", "")))
        if signal_type is None:
            logger.warning(
                "Webhook rejected: unknown action %r for '%s'",
                payload.get("action"),
                strategy_name,
            )
            return web.json_response({"error": "unknown action"}, status=400)

        # The alert's symbol must match the strategy's configured symbol —
        # a misconfigured TradingView chart must not trade the wrong market.
        symbol = str(payload.get("symbol", "")).strip()
        if symbol and symbol.upper() != expected_symbol.upper():
            logger.warning(
                "Webhook rejected: symbol %s does not match %s for '%s'",
                symbol,
                expected_symbol,
                strategy_name,
            )
            return web.json_response({"error": "symbol mismatch"}, status=400)

        try:
            strength = float(payload.get("strength", 0.8))
        except (TypeError, ValueError):
            strength = 0.8

        metadata = {
            "source": "tradingview_webhook",
            "alert_symbol": symbol or expected_symbol,
        }
        # Pass through optional TradingView placeholders for the trade log
        for key in ("indicator", "condition", "price", "interval"):
            if key in payload:
                metadata[key] = payload[key]

        strategy.inject(signal_type, strength=strength, metadata=metadata)
        logger.info(
            "Webhook accepted: %s %s for '%s' (strength %.2f) from %s",
            signal_type.value,
            expected_symbol,
            strategy_name,
            strength,
            remote,
        )
        return web.json_response({"status": "ok"})
