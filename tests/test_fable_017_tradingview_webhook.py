"""
FABLE-017: TradingView/MarketCipher webhook signal bridge.

Covers WebhookSignalStrategy (consume-once, staleness expiry, thread-safe
inject) and TradingViewWebhookServer (shared-secret auth, strategy routing,
symbol-mismatch rejection, IP allowlist) plus the main.py builder's fail-safe
behaviour (no secret / no routable strategies → server not started).
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.core.enums import SignalType
from src.core.models import utcnow
from src.strategies.factory import StrategyFactory
from src.strategies.webhook import WebhookSignalStrategy, parse_action
from src.webhook.server import TradingViewWebhookServer

SECRET = "test-secret-token"


class TestParseAction:
    @pytest.mark.parametrize(
        "action,expected",
        [
            ("long", SignalType.LONG),
            ("BUY", SignalType.LONG),
            ("short", SignalType.SHORT),
            ("sell", SignalType.SHORT),
            ("close", SignalType.CLOSE),
            (" Exit ", SignalType.CLOSE),
        ],
    )
    def test_known_actions(self, action, expected):
        assert parse_action(action) == expected

    def test_unknown_action_is_none(self):
        assert parse_action("hodl") is None


class TestWebhookSignalStrategy:
    def test_analyze_without_pending_is_hold(self):
        strategy = WebhookSignalStrategy(name="wh")
        assert strategy.analyze([]).signal_type == SignalType.HOLD

    def test_inject_then_analyze_consumes_once(self):
        strategy = WebhookSignalStrategy(name="wh")
        strategy.inject(SignalType.LONG, strength=0.9, metadata={"condition": "mc_green_dot"})

        signal = strategy.analyze([])
        assert signal.signal_type == SignalType.LONG
        assert signal.strength == 0.9
        assert signal.strategy_name == "wh"
        assert signal.metadata["condition"] == "mc_green_dot"
        assert "signal_age_seconds" in signal.metadata

        # consumed — next tick holds
        assert strategy.analyze([]).signal_type == SignalType.HOLD

    def test_newer_signal_replaces_unconsumed_older(self):
        strategy = WebhookSignalStrategy(name="wh")
        strategy.inject(SignalType.LONG)
        strategy.inject(SignalType.CLOSE)
        assert strategy.analyze([]).signal_type == SignalType.CLOSE

    def test_strength_clamped_to_unit_interval(self):
        strategy = WebhookSignalStrategy(name="wh")
        strategy.inject(SignalType.LONG, strength=5.0)
        assert strategy.analyze([]).strength == 1.0

    def test_stale_signal_dropped(self):
        strategy = WebhookSignalStrategy(name="wh", max_age_seconds=300)
        strategy.inject(SignalType.LONG)
        with patch(
            "src.strategies.webhook.utcnow",
            return_value=utcnow() + timedelta(seconds=301),
        ):
            assert strategy.analyze([]).signal_type == SignalType.HOLD
        # the stale signal was consumed, not left pending
        assert strategy.analyze([]).signal_type == SignalType.HOLD

    def test_configure_max_age(self):
        strategy = WebhookSignalStrategy(name="wh")
        strategy.configure({"max_age_seconds": 60})
        assert strategy._max_age_seconds == 60.0

    def test_factory_constructs_webhook_type(self):
        factory = StrategyFactory()
        factory.create_from_config(
            [{"name": "tv_btc", "type": "webhook", "params": {"max_age_seconds": 120}}]
        )
        instance = factory.get_instance("tv_btc")
        assert isinstance(instance, WebhookSignalStrategy)
        assert instance._max_age_seconds == 120.0


@pytest.fixture
def strategy():
    return WebhookSignalStrategy(name="tv_btc")


def _make_server(strategy, **kwargs):
    server = TradingViewWebhookServer(secret=SECRET, **kwargs)
    server.register(strategy, "BTC-USDT")
    return server


async def _client(server):
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    return client


def _payload(**overrides):
    payload = {
        "secret": SECRET,
        "strategy": "tv_btc",
        "symbol": "BTC-USDT",
        "action": "long",
        "strength": 0.7,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
class TestWebhookServer:
    async def test_requires_secret_at_construction(self):
        with pytest.raises(ValueError):
            TradingViewWebhookServer(secret="")

    async def test_valid_alert_injects_signal(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post("/webhook/tradingview", json=_payload())
            assert resp.status == 200

            signal = strategy.analyze([])
            assert signal.signal_type == SignalType.LONG
            assert signal.strength == 0.7
            assert signal.metadata["source"] == "tradingview_webhook"
        finally:
            await client.close()

    async def test_wrong_secret_rejected_401(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post(
                "/webhook/tradingview", json=_payload(secret="wrong")
            )
            assert resp.status == 401
            assert strategy.analyze([]).signal_type == SignalType.HOLD
        finally:
            await client.close()

    async def test_missing_secret_rejected_401(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            payload = _payload()
            del payload["secret"]
            resp = await client.post("/webhook/tradingview", json=payload)
            assert resp.status == 401
        finally:
            await client.close()

    async def test_invalid_json_rejected_400(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post("/webhook/tradingview", data=b"not json {{")
            assert resp.status == 400
        finally:
            await client.close()

    async def test_unknown_strategy_rejected_404(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post(
                "/webhook/tradingview", json=_payload(strategy="nope")
            )
            assert resp.status == 404
        finally:
            await client.close()

    async def test_unknown_action_rejected_400(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post(
                "/webhook/tradingview", json=_payload(action="hodl")
            )
            assert resp.status == 400
            assert strategy.analyze([]).signal_type == SignalType.HOLD
        finally:
            await client.close()

    async def test_symbol_mismatch_rejected_400(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.post(
                "/webhook/tradingview", json=_payload(symbol="ETH-USDT")
            )
            assert resp.status == 400
            assert strategy.analyze([]).signal_type == SignalType.HOLD
        finally:
            await client.close()

    async def test_missing_symbol_accepted(self, strategy):
        """The strategy is bound to one symbol, so the field is optional."""
        client = await _client(_make_server(strategy))
        try:
            payload = _payload()
            del payload["symbol"]
            resp = await client.post("/webhook/tradingview", json=payload)
            assert resp.status == 200
            assert strategy.analyze([]).signal_type == SignalType.LONG
        finally:
            await client.close()

    async def test_ip_allowlist_blocks_unlisted_source(self, strategy):
        client = await _client(
            _make_server(strategy, allowed_ips=["52.89.214.238"])
        )
        try:
            resp = await client.post("/webhook/tradingview", json=_payload())
            assert resp.status == 403
        finally:
            await client.close()

    async def test_ip_allowlist_permits_listed_source(self, strategy):
        # The aiohttp test client connects from 127.0.0.1
        client = await _client(_make_server(strategy, allowed_ips=["127.0.0.1"]))
        try:
            resp = await client.post("/webhook/tradingview", json=_payload())
            assert resp.status == 200
        finally:
            await client.close()

    async def test_health_endpoint(self, strategy):
        client = await _client(_make_server(strategy))
        try:
            resp = await client.get("/health")
            assert resp.status == 200
        finally:
            await client.close()

    async def test_start_stop_real_listener(self, strategy):
        """Server binds, serves one request, and shuts down cleanly."""
        import aiohttp

        server = _make_server(strategy, host="127.0.0.1", port=18099)
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    "http://127.0.0.1:18099/webhook/tradingview", json=_payload()
                )
                assert resp.status == 200
        finally:
            await server.stop()
        assert strategy.analyze([]).signal_type == SignalType.LONG


class TestMainBuilder:
    def _config(self, enabled=True):
        return {"webhook": {"enabled": enabled, "port": 18100}}

    def _strategies_config(self):
        return [
            {"name": "tv_btc", "type": "webhook", "symbols": ["BTC-USDT"]},
            {"name": "sma", "type": "sma_crossover", "symbols": ["BTC-USDT"]},
        ]

    def _factory(self):
        factory = StrategyFactory()
        factory.create_from_config(self._strategies_config())
        return factory

    def test_disabled_returns_none(self, monkeypatch):
        from main import build_webhook_server

        monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
        assert (
            build_webhook_server(
                self._config(enabled=False), self._factory(), self._strategies_config()
            )
            is None
        )

    def test_enabled_without_secret_returns_none(self, monkeypatch):
        from main import build_webhook_server

        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        assert (
            build_webhook_server(
                self._config(), self._factory(), self._strategies_config()
            )
            is None
        )

    def test_enabled_with_secret_routes_webhook_strategies(self, monkeypatch):
        from main import build_webhook_server

        monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
        server = build_webhook_server(
            self._config(), self._factory(), self._strategies_config()
        )
        assert server is not None
        assert "tv_btc" in server._routes
        assert "sma" not in server._routes

    def test_no_webhook_strategies_returns_none(self, monkeypatch):
        from main import build_webhook_server

        monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
        config = [{"name": "sma", "type": "sma_crossover", "symbols": ["BTC-USDT"]}]
        factory = StrategyFactory()
        factory.create_from_config(config)
        assert build_webhook_server(self._config(), factory, config) is None

    def test_multi_symbol_webhook_entry_not_routed(self, monkeypatch):
        from main import build_webhook_server

        monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
        config = [
            {"name": "tv_multi", "type": "webhook", "symbols": ["BTC-USDT", "ETH-USDT"]}
        ]
        factory = StrategyFactory()
        factory.create_from_config(config)
        assert build_webhook_server(self._config(), factory, config) is None
