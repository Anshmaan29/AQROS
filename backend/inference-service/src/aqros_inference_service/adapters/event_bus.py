"""Event bus adapter for the inference service.

Wraps the ``aqros_events.EventBus`` ABC in a thin adapter that implements
the ``PredictionPublisher`` port.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aqros_events import EventBus, EventEnvelope
from aqros_inference_service.ports.ports import PredictionPublisher

_PRODUCER_NAME = "inference-service"


class AqrosEventBusPublisher(PredictionPublisher):
    """Publishes inference events through an ``aqros_events.EventBus``."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def publish(self, topic: str, payload: bytes) -> None:
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
