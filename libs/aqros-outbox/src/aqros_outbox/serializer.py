from __future__ import annotations

import json
from datetime import datetime

from aqros_events import EventEnvelope
from aqros_outbox.domain import OutboxEvent


def event_to_envelope(event: OutboxEvent) -> EventEnvelope:
    return EventEnvelope(
        event_id=event.event_id,
        topic=event.topic,
        payload=event.payload,
        content_type=event.content_type,
        event_time=event.event_time or datetime.now(),
        knowledge_time=event.knowledge_time or datetime.now(),
        producer=event.producer,
        schema_version=event.schema_version,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
    )


def envelope_to_event(
    envelope: EventEnvelope,
    headers: dict[str, str] | None = None,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=envelope.event_id,
        topic=envelope.topic,
        payload=envelope.payload,
        content_type=envelope.content_type,
        event_time=envelope.event_time,
        knowledge_time=envelope.knowledge_time,
        producer=envelope.producer,
        schema_version=envelope.schema_version,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        headers=headers or {},
    )


def serialize_headers(headers: dict[str, str]) -> str:
    return json.dumps(headers, separators=(",", ":"))


def deserialize_headers(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    return dict(json.loads(raw))
