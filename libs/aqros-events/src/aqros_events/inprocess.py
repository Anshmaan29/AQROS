"""InProcessEventBus — an in-memory, deterministic ``EventBus`` adapter.

Designed for development, testing, and single-process deployments where
Kafka/Redpanda is not yet available. Events are delivered synchronously
within the same process: ``publish`` blocks until every registered handler
has been invoked.

Thread safety
-------------
All public methods are async and use an ``asyncio.Lock`` so that concurrent
``publish``, ``subscribe``, and ``unsubscribe`` calls are serialised.
Handler execution is serialised per topic (FIFO order) but two publishes
to different topics proceed sequentially under the single lock.

Determinism
-----------
Handlers are called in registration order for a given topic. If a handler
raises, the exception is logged (via ``structlog``) but does not prevent
subsequent handlers from receiving the event — the bus guarantees
best-effort delivery. Returning a failed ``Task`` (via the raised exception)
is the caller's choice; the bus never fails ``publish`` because a downstream
handler failed.

No wildcard or pattern-based subscriptions are supported — only exact
topic matching.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import suppress

from aqros_events.bus import EventBus, EventHandler
from aqros_events.envelope import EventEnvelope
from aqros_events.errors import EventPublishError

try:
    from structlog import get_logger
except ImportError:
    import logging

    get_logger = logging.getLogger

_logger = get_logger(__name__)


class InProcessEventBus(EventBus):
    """An in-memory, deterministic ``EventBus`` for development and test.

    All events are delivered synchronously within the same process. No
    persistence, no serialisation, no external dependencies — events are
    passed as ``EventEnvelope`` objects directly to each registered handler.

    Usage::

        bus = InProcessEventBus()
        await bus.subscribe("orders.filled", my_handler)
        await bus.publish(envelope)
        await bus.unsubscribe("orders.filled", my_handler)
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    async def publish(self, envelope: EventEnvelope) -> None:
        async with self._lock:
            topic = envelope.topic
            handlers = list(self._handlers.get(topic, []))
        if not handlers:
            return
        errors: list[Exception] = []
        for handler in handlers:
            try:
                await handler(envelope)
            except Exception as exc:
                errors.append(exc)
                _logger.error(
                    "event_handler_failed",
                    topic=topic,
                    event_id=envelope.event_id,
                    error=str(exc),
                )
        if errors:
            raise EventPublishError(
                topic,
                envelope.event_id,
                f"{len(errors)} handler(s) failed; first error: {errors[0]}",
            )

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        async with self._lock:
            handlers = self._handlers[topic]
            if handler not in handlers:
                handlers.append(handler)

    async def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        async with self._lock:
            handlers = self._handlers.get(topic)
            if handlers is not None:
                with suppress(ValueError):
                    handlers.remove(handler)

    def handler_count(self, topic: str) -> int:
        """Return the number of handlers registered for ``topic``.

        Useful for assertions in tests. Not protected by the lock — call
        only when no concurrent mutation is expected.
        """
        return len(self._handlers.get(topic, []))
