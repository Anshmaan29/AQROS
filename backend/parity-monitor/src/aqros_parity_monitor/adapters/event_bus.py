"""Event bus adapter for the parity monitor.

Wraps the ``aqros_events.EventBus`` ABC in a thin adapter that implements
the ``EventPublisher`` port.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aqros_events import EventBus, EventEnvelope
from aqros_parity_monitor.ports.ports import EventPublisher

_PRODUCER_NAME = "parity-monitor"


class AqrosEventBusPublisher(EventPublisher):
    """Publishes parity events through an ``aqros_events.EventBus``.

    Args:
        bus: An ``EventBus`` instance (``InProcessEventBus`` or
            ``KafkaEventBus``).
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def publish_event(self, topic: str, payload: bytes) -> None:
        now = datetime.now(UTC)
        envelope = EventEnvelope(
            topic=topic,
            payload=payload,
            event_time=now,
            knowledge_time=now,
            producer=_PRODUCER_NAME,
            schema_version="1.0",
        )
        await self._bus.publish(envelope)
