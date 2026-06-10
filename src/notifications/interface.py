from abc import ABC, abstractmethod

from src.core.events import Event, EventBus
from src.core.enums import EventType


class INotifier(ABC):
    """Abstract interface for operator notifications (FABLE-011)."""

    @abstractmethod
    def notify(self, level: str, message: str) -> None:
        """Send a notification. Must never raise and never block the caller."""
        ...

    def attach(self, event_bus: EventBus) -> None:
        """Subscribe this notifier to ALERT events on the bus."""
        event_bus.subscribe(EventType.ALERT, self._on_alert)

    def _on_alert(self, event: Event) -> None:
        payload = event.payload or {}
        self.notify(payload.get("level", "warning"), payload.get("message", ""))
