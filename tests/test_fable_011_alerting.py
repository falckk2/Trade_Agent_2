"""
FABLE-011: No alerting — drawdown halt and other critical states were silent.

Adds EventType.ALERT + INotifier/TelegramNotifier and publishers at the three
critical sites:
1. RiskManager drawdown halt (once per halt onset, resets on recovery)
2. TradingEngine.close_all_positions failure ("manual close required")
3. BloFinWebSocket reconnect storm (>= 5 reconnects per 10 min, alert at
   most once per window)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.enums import EventType, Side, SignalType, TimeFrame
from src.core.events import Event, EventBus
from src.core.models import PortfolioSnapshot, Position, Signal
from src.engine.trading_engine import TradingEngine
from src.exchange.blofin_websocket import BloFinWebSocket
from src.notifications.telegram import TelegramNotifier
from src.risk.manager import RiskManager


def _signal():
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTC-USDT",
        strength=0.8,
        strategy_name="test",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _portfolio(equity):
    return PortfolioSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        total_equity=equity,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=[],
    )


def _collect_alerts(event_bus):
    alerts = []
    event_bus.subscribe(EventType.ALERT, lambda e: alerts.append(e.payload))
    return alerts


class TestDrawdownAlert:
    def test_halt_publishes_critical_alert_once(self):
        event_bus = EventBus()
        alerts = _collect_alerts(event_bus)
        rm = RiskManager(max_drawdown_pct=0.10, event_bus=event_bus)
        rm.set_initial_equity(50000.0)

        # Two rejected signals during the same halt → exactly one alert
        rm.validate_signal(_signal(), _portfolio(42000.0))
        rm.validate_signal(_signal(), _portfolio(41000.0))

        assert len(alerts) == 1
        assert alerts[0]["level"] == "critical"
        assert "drawdown" in alerts[0]["message"].lower()

    def test_alert_rearms_after_recovery(self):
        event_bus = EventBus()
        alerts = _collect_alerts(event_bus)
        rm = RiskManager(max_drawdown_pct=0.10, event_bus=event_bus)
        rm.set_initial_equity(50000.0)

        rm.validate_signal(_signal(), _portfolio(42000.0))  # halt → alert
        rm.validate_signal(_signal(), _portfolio(49000.0))  # recovered
        rm.validate_signal(_signal(), _portfolio(42000.0))  # halt again → alert

        assert len(alerts) == 2

    def test_no_event_bus_is_safe(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.set_initial_equity(50000.0)
        assert rm.validate_signal(_signal(), _portfolio(42000.0)) is False


class TestShutdownCloseFailureAlert:
    @pytest.mark.asyncio
    async def test_failed_close_publishes_critical_alert(self):
        event_bus = EventBus()
        alerts = _collect_alerts(event_bus)

        position = Position(
            id="p1", symbol="BTC-USDT", side=Side.BUY,
            entry_price=50000.0, current_price=50000.0, quantity=0.1,
        )
        exchange = AsyncMock()
        exchange.get_positions.return_value = [position]
        exchange.get_balance.return_value = {"total_equity": 1.0, "available": 1.0}

        order_executor = AsyncMock()
        order_executor.close_position.side_effect = RuntimeError("rejected")

        portfolio_manager = MagicMock()

        engine = TradingEngine(
            exchange=exchange,
            data_provider=AsyncMock(),
            risk_manager=MagicMock(),
            order_executor=order_executor,
            portfolio_manager=portfolio_manager,
            event_bus=event_bus,
            symbols=["BTC-USDT"],
            timeframe=TimeFrame.M5,
        )
        await engine.close_all_positions()

        assert len(alerts) == 1
        assert alerts[0]["level"] == "critical"
        assert "MANUAL CLOSE REQUIRED" in alerts[0]["message"]


class TestReconnectStormAlert:
    @pytest.mark.asyncio
    async def test_storm_alert_fires_once_per_window(self):
        event_bus = EventBus()
        alerts = _collect_alerts(event_bus)
        ws = BloFinWebSocket(event_bus=event_bus)

        for _ in range(7):
            await ws._check_reconnect_storm()

        assert len(alerts) == 1
        assert alerts[0]["level"] == "warning"
        assert "reconnect storm" in alerts[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self):
        event_bus = EventBus()
        alerts = _collect_alerts(event_bus)
        ws = BloFinWebSocket(event_bus=event_bus)

        for _ in range(4):
            await ws._check_reconnect_storm()

        assert alerts == []


class TestTelegramNotifier:
    def test_disabled_without_credentials(self):
        notifier = TelegramNotifier(background=False)
        assert notifier.enabled is False
        with patch("src.notifications.telegram.requests.post") as mock_post:
            notifier.notify("critical", "boom")
            mock_post.assert_not_called()

    def test_posts_message_with_credentials(self):
        notifier = TelegramNotifier(bot_token="tok", chat_id="42", background=False)
        with patch("src.notifications.telegram.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            notifier.notify("critical", "drawdown halt")
            mock_post.assert_called_once()
            url = mock_post.call_args.args[0]
            body = mock_post.call_args.kwargs["json"]
            assert "bottok" in url
            assert body["chat_id"] == "42"
            assert "drawdown halt" in body["text"]
            assert "[CRITICAL]" in body["text"]

    def test_post_failure_never_raises(self):
        notifier = TelegramNotifier(bot_token="tok", chat_id="42", background=False)
        with patch(
            "src.notifications.telegram.requests.post",
            side_effect=RuntimeError("network down"),
        ):
            notifier.notify("warning", "test")  # must not raise

    def test_attach_routes_alert_events(self):
        event_bus = EventBus()
        notifier = TelegramNotifier(bot_token="tok", chat_id="42", background=False)
        notifier.attach(event_bus)
        with patch("src.notifications.telegram.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            event_bus.publish_sync(
                Event(
                    event_type=EventType.ALERT,
                    payload={"level": "warning", "message": "hello"},
                )
            )
            mock_post.assert_called_once()
