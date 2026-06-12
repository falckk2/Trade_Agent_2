"""
FABLE-018 (per-condition attribution): the signal log records WHY each trade
happened, the composite preserves contributing child metadata, and the report
helpers join trades back to alert conditions.
"""

import csv
import json
from datetime import datetime, timedelta, timezone

from src.core.enums import EventType, Side, SignalType
from src.core.events import Event, EventBus
from src.core.models import Candle, Signal, TradeRecord, utcnow
from src.portfolio.signal_log import (
    SignalLogger,
    extract_condition_events,
    load_signal_log,
    split_trades_by_condition,
)
from src.strategies.composite import CompositeStrategy
from src.strategies.webhook import WebhookSignalStrategy

T0 = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def _signal(strategy="tv_btc", symbol="BTC-USDT", metadata=None):
    return Signal(
        signal_type=SignalType.LONG,
        symbol=symbol,
        strength=0.8,
        strategy_name=strategy,
        timestamp=T0,
        metadata=metadata or {},
    )


def _trade(symbol="BTC-USDT", opened_at=None, strategy="tv_btc", pnl=10.0):
    opened = opened_at or T0 + timedelta(seconds=30)
    return TradeRecord(
        id="t1", symbol=symbol, side=Side.BUY, entry_price=100.0,
        exit_price=110.0, quantity=1.0, pnl=pnl, strategy_name=strategy,
        opened_at=opened, closed_at=opened + timedelta(hours=1),
    )


class TestSignalLogger:
    def test_appends_signal_with_metadata(self, tmp_path):
        logger = SignalLogger(data_dir=str(tmp_path))
        bus = EventBus()
        logger.attach(bus)

        bus.publish_sync(Event(
            event_type=EventType.SIGNAL_GENERATED,
            payload={"signal": _signal(metadata={"condition": "mcb_green_dot"})},
        ))

        rows = load_signal_log(tmp_path / "signal_log.csv")
        assert len(rows) == 1
        assert rows[0]["strategy_name"] == "tv_btc"
        assert rows[0]["symbol"] == "BTC-USDT"
        assert rows[0]["metadata"]["condition"] == "mcb_green_dot"

    def test_missing_signal_payload_ignored(self, tmp_path):
        logger = SignalLogger(data_dir=str(tmp_path))
        bus = EventBus()
        logger.attach(bus)
        bus.publish_sync(Event(event_type=EventType.SIGNAL_GENERATED, payload={}))
        assert load_signal_log(tmp_path / "signal_log.csv") == []

    def test_load_skips_malformed_rows(self, tmp_path):
        path = tmp_path / "signal_log.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "strategy_name", "symbol",
                             "signal_type", "strength", "metadata"])
            writer.writerow([T0.isoformat(), "s1", "BTC-USDT", "long", "0.8", "{}"])
            writer.writerow(["not-a-date", "s1", "BTC-USDT", "long", "0.8", "{}"])
        assert len(load_signal_log(path)) == 1


class TestCompositePreservesChildMetadata:
    def test_webhook_condition_survives_aggregation(self):
        webhook = WebhookSignalStrategy(name="tv_btc")
        webhook.inject(SignalType.LONG, strength=0.9,
                       metadata={"condition": "mcb_green_dot"})
        composite = CompositeStrategy(name="composite[tv_btc]")
        composite.add_strategy(webhook, 1.0)

        candles = [Candle(timestamp=T0, open=1, high=1, low=1, close=1, volume=1)]
        result = composite.analyze(candles)

        assert result.signal_type == SignalType.LONG
        child = result.metadata["children_signals"]["tv_btc"]
        assert child["metadata"]["condition"] == "mcb_green_dot"
        assert child["signal_type"] == "long"

    def test_hold_composite_has_no_children_signals(self):
        webhook = WebhookSignalStrategy(name="tv_btc")  # nothing injected
        composite = CompositeStrategy(name="c")
        composite.add_strategy(webhook, 1.0)
        candles = [Candle(timestamp=T0, open=1, high=1, low=1, close=1, volume=1)]
        result = composite.analyze(candles)
        assert result.signal_type == SignalType.HOLD
        assert "children_signals" not in result.metadata


class TestConditionExtractionAndJoin:
    def _log_rows(self):
        return [
            {  # webhook traded alone
                "timestamp": T0, "strategy_name": "tv_btc", "symbol": "BTC-USDT",
                "signal_type": "long", "strength": 0.8,
                "metadata": {"condition": "mcb_green_dot"},
            },
            {  # webhook inside a composite
                "timestamp": T0 + timedelta(hours=2),
                "strategy_name": "composite[sma,tv_btc]", "symbol": "BTC-USDT",
                "signal_type": "long", "strength": 0.4,
                "metadata": {
                    "children_signals": {
                        "tv_btc": {"signal_type": "long", "strength": 0.8,
                                   "metadata": {"condition": "blood_diamond"}},
                    }
                },
            },
            {  # unrelated strategy — must not produce an event
                "timestamp": T0, "strategy_name": "sma", "symbol": "BTC-USDT",
                "signal_type": "long", "strength": 0.5, "metadata": {},
            },
        ]

    def test_extracts_top_level_and_nested_conditions(self):
        events = extract_condition_events(self._log_rows(), "tv_btc")
        assert [e["condition"] for e in events] == ["mcb_green_dot", "blood_diamond"]

    def test_join_groups_trades_by_latest_preceding_event(self):
        events = extract_condition_events(self._log_rows(), "tv_btc")
        trades = [
            _trade(opened_at=T0 + timedelta(seconds=40)),
            _trade(opened_at=T0 + timedelta(hours=2, seconds=40)),
        ]
        groups = split_trades_by_condition(trades, events)
        assert len(groups["mcb_green_dot"]) == 1
        assert len(groups["blood_diamond"]) == 1

    def test_trade_outside_lag_window_is_unmatched(self):
        events = extract_condition_events(self._log_rows(), "tv_btc")
        trades = [_trade(opened_at=T0 + timedelta(hours=1))]  # 1h after signal
        groups = split_trades_by_condition(trades, events)
        assert "(unmatched)" in groups

    def test_symbol_mismatch_is_unmatched(self):
        events = extract_condition_events(self._log_rows(), "tv_btc")
        trades = [_trade(symbol="ETH-USDT", opened_at=T0 + timedelta(seconds=40))]
        groups = split_trades_by_condition(trades, events)
        assert "(unmatched)" in groups

    def test_roundtrip_through_csv(self, tmp_path):
        """Composite → logger → CSV → loader → extractor keeps the condition."""
        logger = SignalLogger(data_dir=str(tmp_path))
        bus = EventBus()
        logger.attach(bus)
        bus.publish_sync(Event(
            event_type=EventType.SIGNAL_GENERATED,
            payload={"signal": _signal(
                strategy="composite[sma,tv_btc]",
                metadata={"children_signals": {"tv_btc": {
                    "signal_type": "long", "strength": 0.8,
                    "metadata": {"condition": "mcb_green_dot"},
                }}},
            )},
        ))
        rows = load_signal_log(tmp_path / "signal_log.csv")
        events = extract_condition_events(rows, "tv_btc")
        assert events[0]["condition"] == "mcb_green_dot"
