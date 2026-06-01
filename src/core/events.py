import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.core.enums import EventType

logger = logging.getLogger(__name__)


@dataclass
class Event:
    event_type: EventType
    payload: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# Callback type: sync or async callable taking an Event
EventCallback = Callable[[Event], Any]


class EventBus:
    """Lightweight publish/subscribe event system supporting sync and async callbacks."""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventCallback]] = defaultdict(list)

    def subscribe(self, event_type: EventType, callback: EventCallback) -> None:
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: EventCallback) -> None:
        try:
            self._subscribers[event_type].remove(callback)
        except ValueError:
            pass

    async def publish(self, event: Event) -> None:
        for callback in self._subscribers.get(event.event_type, []):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "Error in event callback for %s", event.event_type
                )

    def publish_sync(self, event: Event) -> None:
        for callback in self._subscribers.get(event.event_type, []):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    logger.warning(
                        "Async callback %s called from publish_sync, skipping",
                        callback,
                    )
            except Exception:
                logger.exception(
                    "Error in event callback for %s", event.event_type
                )
