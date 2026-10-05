from __future__ import annotations

from datetime import UTC, datetime

from aqros_outbox import OutboxEvent
from aqros_outbox.serializer import (
    deserialize_headers,
    envelope_to_event,
    event_to_envelope,
    serialize_headers,
)


class TestEventToEnvelope:
    def test_converts_all_fields(self) -> None:
        now = datetime.now(UTC)
        event = OutboxEvent(
            event_id="EVT001",
            topic="test.topic",
            payload=b'{"key": "value"}',
            content_type="application/json",
            event_time=now,
            knowledge_time=now,
            producer="svc",
            schema_version="1.0",
            correlation_id="CORR",
            causation_id="CAUS",
        )
        envelope = event_to_envelope(event)
        assert envelope.event_id == "EVT001"
        assert envelope.topic == "test.topic"
        assert envelope.payload == b'{"key": "value"}'
        assert envelope.producer == "svc"
        assert envelope.causation_id == "CAUS"

    def test_event_to_envelope_to_event_roundtrip(self) -> None:
        now = datetime.now(UTC)
        event = OutboxEvent(
            event_id="RT001",
            topic="roundtrip.test",
            payload=b'{"x": 1}',
            event_time=now,
            knowledge_time=now,
            producer="test",
            schema_version="1.0",
            correlation_id="CORR_RT",
            causation_id=None,
        )
        envelope = event_to_envelope(event)
        restored = envelope_to_event(envelope)
        assert restored.event_id == event.event_id
        assert restored.topic == event.topic
        assert restored.payload == event.payload
        assert restored.producer == event.producer


class TestHeaders:
    def test_serialize_deserialize(self) -> None:
        headers = {"content-type": "json", "x-retry": "3"}
        serialized = serialize_headers(headers)
        deserialized = deserialize_headers(serialized)
        assert deserialized == headers

    def test_empty_headers(self) -> None:
        assert deserialize_headers("") == {}
        assert deserialize_headers("{}") == {}
