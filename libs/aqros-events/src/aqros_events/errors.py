"""Typed error hierarchy for the event bus library."""

from __future__ import annotations


class EventBusError(RuntimeError):
    """Base error for all event bus failures."""


class EventPublishError(EventBusError):
    """Raised when an ``EventBus.publish`` call fails.

    The ``topic`` and ``event_id`` of the failed envelope are carried so
    callers can log or retry the specific event.
    """

    def __init__(self, topic: str, event_id: str, message: str) -> None:
        self.topic = topic
        self.event_id = event_id
        super().__init__(f"[{topic}] publish {event_id}: {message}")


class EventSubscriptionError(EventBusError):
    """Raised when ``EventBus.subscribe`` or ``EventBus.unsubscribe`` fails."""

    def __init__(self, topic: str, message: str) -> None:
        self.topic = topic
        super().__init__(f"[{topic}] {message}")
