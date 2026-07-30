"""EventBus — the typed port for publishing and subscribing to events.

Two adapters are planned:
    - ``InProcessEventBus``  (development, test — implemented)
    - ``KafkaEventBus``      (production — deferred to V1)

The ``EventBus`` ABC defines the contract; callers depend only on this
abstraction. Infrastructure code (dependency injection, startup) wires the
concrete adapter into each service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aqros_events.envelope import EventEnvelope

EventHandler = Callable[["EventEnvelope"], Awaitable[None]]
"""Signature for an async event handler: ``async def handler(envelope: EventEnvelope) -> None``.

Handlers are fire-and-forget from the publisher's perspective — the bus may
invoke them synchronously (InProcessEventBus) or schedule them on a separate
thread/worker (Kafka). Handlers must not block for significant duration.
"""


class EventBus(ABC):
    """Port/ABC for publishing events and subscribing handlers to topics.

    Every call is async and idempotent where the semantics allow:
    ``publish`` is idempotent per ``event_id`` (the bus guarantees at-least-once
    delivery for a given ``event_id``; the consumer is responsible for
    deduplication). ``subscribe`` and ``unsubscribe`` are idempotent per
    ``(topic, handler)`` pair.
    """

    @abstractmethod
    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish ``envelope`` to its topic.

        At-least-once delivery: the same ``event_id`` may be delivered to a
        subscriber more than once; consumers SHOULD deduplicate on
        ``event_id``.

        Raises ``EventPublishError`` on any failure.
        """

    @abstractmethod
    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Register ``handler`` to receive events on ``topic``.

        The handler is invoked for every event published to ``topic`` after
        subscription. Idempotent: registering the same ``(topic, handler)``
        pair twice is a no-op.

        Raises ``EventSubscriptionError`` on any failure.
        """

    @abstractmethod
    async def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Remove a previously-registered ``handler`` from ``topic``.

        Idempotent: removing a handler that was never registered is a no-op.
        Raises ``EventSubscriptionError`` on any failure.
        """

    async def start(self) -> None:  # noqa: B027
        """Optional lifecycle hook: acquire resources, connect to external broker.

        Called before the first ``publish`` or ``subscribe``. The base
        implementation is a no-op. Adapters backed by external infrastructure
        (e.g. ``KafkaEventBus``) override this to initialise connections.
        """

    async def stop(self) -> None:  # noqa: B027
        """Optional lifecycle hook: release resources, disconnect.

        Called when the bus is no longer needed. The base implementation is
        a no-op. Adapters backed by external infrastructure override this to
        close connections gracefully.
        """
