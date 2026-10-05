from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from aqros_events import EventEnvelope, InProcessEventBus
from aqros_outbox import OutboxEvent


@pytest.fixture
def event_bus() -> InProcessEventBus:
    return InProcessEventBus()


@pytest.fixture
def sample_event() -> OutboxEvent:
    now = datetime.now(UTC)
    return OutboxEvent(
        event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        topic="test.events",
        payload=b'{"hello": "world"}',
        content_type="application/json",
        event_time=now,
        knowledge_time=now,
        producer="test-service",
        schema_version="1.0",
        correlation_id="CORR123",
        causation_id=None,
    )


@pytest_asyncio.fixture
async def collected_events(event_bus: InProcessEventBus) -> list[EventEnvelope]:
    collected: list[EventEnvelope] = []

    async def collector(envelope: EventEnvelope) -> None:
        collected.append(envelope)

    await event_bus.subscribe("test.events", collector)
    return collected
