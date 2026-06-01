"""Tests for the EventBus pub/sub system."""

import asyncio

import pytest

from src.core.enums import EventType
from src.core.events import Event, EventBus


class TestEventBus:
    def test_subscribe_and_publish_sync(self, event_bus):
        received = []

        def handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.CANDLE_UPDATE, handler)
        event = Event(event_type=EventType.CANDLE_UPDATE, payload={"test": 1})
        event_bus.publish_sync(event)

        assert len(received) == 1
        assert received[0].payload == {"test": 1}

    @pytest.mark.asyncio
    async def test_publish_async(self, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.ORDER_PLACED, handler)
        event = Event(event_type=EventType.ORDER_PLACED, payload="order_data")
        await event_bus.publish(event)

        assert len(received) == 1

    def test_unsubscribe(self, event_bus):
        received = []

        def handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.CANDLE_UPDATE, handler)
        event_bus.unsubscribe(EventType.CANDLE_UPDATE, handler)

        event = Event(event_type=EventType.CANDLE_UPDATE, payload=None)
        event_bus.publish_sync(event)

        assert len(received) == 0

    def test_unsubscribe_nonexistent(self, event_bus):
        def handler(event: Event):
            pass

        # Should not raise
        event_bus.unsubscribe(EventType.CANDLE_UPDATE, handler)

    def test_multiple_subscribers(self, event_bus):
        results = {"a": 0, "b": 0}

        def handler_a(event: Event):
            results["a"] += 1

        def handler_b(event: Event):
            results["b"] += 1

        event_bus.subscribe(EventType.SIGNAL_GENERATED, handler_a)
        event_bus.subscribe(EventType.SIGNAL_GENERATED, handler_b)

        event = Event(event_type=EventType.SIGNAL_GENERATED, payload=None)
        event_bus.publish_sync(event)

        assert results["a"] == 1
        assert results["b"] == 1

    def test_different_event_types_isolated(self, event_bus):
        received = []

        def handler(event: Event):
            received.append(event.event_type)

        event_bus.subscribe(EventType.ORDER_FILLED, handler)

        # Publish a different event type
        event = Event(event_type=EventType.CANDLE_UPDATE, payload=None)
        event_bus.publish_sync(event)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_error_in_callback_doesnt_break_others(self, event_bus):
        received = []

        def bad_handler(event: Event):
            raise ValueError("oops")

        def good_handler(event: Event):
            received.append(event)

        event_bus.subscribe(EventType.PORTFOLIO_UPDATE, bad_handler)
        event_bus.subscribe(EventType.PORTFOLIO_UPDATE, good_handler)

        event = Event(event_type=EventType.PORTFOLIO_UPDATE, payload="data")
        await event_bus.publish(event)

        assert len(received) == 1
