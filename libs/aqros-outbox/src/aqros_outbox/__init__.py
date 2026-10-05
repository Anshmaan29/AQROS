"""AQROS transactional outbox — guaranteed at-least-once event delivery.

The outbox pattern ensures that every event published after a database
write is durably stored in the same transaction. A background dispatcher
polls, publishes to the event bus, and marks events as delivered — with
automatic retry, exponential backoff, and a dead-letter queue.

Usage
-----
For services that own a database (e.g., model-registry, feature-store)::

    from aqros_outbox import (
        OutboxConfig, OutboxDispatcher, OutboxMetrics,
        SqlAlchemyOutboxRepository,
    )

    repo = SqlAlchemyOutboxRepository(session_factory)
    dispatcher = OutboxDispatcher(repo, event_bus, OutboxConfig(), OutboxMetrics())
    await dispatcher.start()
    # ... service runs ...
    await dispatcher.stop()

Writing events
--------------
When an event describes a database state change, use ``stage`` so the event
row is written in the *same* transaction as that change — this is the
transactional-outbox guarantee, and it is what prevents a crash between
"state committed" and "event published" from losing the event::

    async with session_factory() as session:
        order = Order(...)
        session.add(order)
        repo.stage(session, OutboxEvent(event_id=..., topic="orders.filled", ...))
        await session.commit()   # state + event become durable together

``repo.add(event)`` opens its own session and commits immediately; use it only
for events that are not coupled to a local state change.

For services without a database (e.g., inference-service, strategy-engine)::

    from aqros_outbox import DirectOutboxRepository

    repo = DirectOutboxRepository(event_bus)
    # Use ``repo.add(outbox_event)`` instead of ``event_bus.publish(envelope)``
"""

from __future__ import annotations

from aqros_outbox.dispatcher import OutboxConfig, OutboxDispatcher
from aqros_outbox.domain import (
    OutboxEvent,
    OutboxRepository,
    OutboxStatistics,
    OutboxStatus,
)
from aqros_outbox.metrics import NoopOutboxMetrics, OutboxMetrics
from aqros_outbox.models import OutboxModel
from aqros_outbox.repository import (
    DirectOutboxRepository,
    InMemoryOutboxRepository,
    SqlAlchemyOutboxRepository,
)
from aqros_outbox.serializer import (
    envelope_to_event,
    event_to_envelope,
)

__version__ = "0.1.0"

__all__ = [
    "DirectOutboxRepository",
    "InMemoryOutboxRepository",
    "NoopOutboxMetrics",
    "OutboxConfig",
    "OutboxDispatcher",
    "OutboxEvent",
    "OutboxMetrics",
    "OutboxModel",
    "OutboxRepository",
    "OutboxStatistics",
    "OutboxStatus",
    "SqlAlchemyOutboxRepository",
    "envelope_to_event",
    "event_to_envelope",
]
